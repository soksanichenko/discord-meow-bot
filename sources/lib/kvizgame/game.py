"""KvizGame state machine — pure game logic, no I/O, no async."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from sources.lib.kvizgame.parser import Package, Question, Round


class Phase(Enum):
    """All possible game phases."""

    BOARD = auto()  # Active player selects a question
    AUCTION_BIDDING = auto()  # Active player places a bid
    CAT_TRANSFER = auto()  # Active player nominates a recipient
    QUESTION = auto()  # Question content is being shown
    BUZZER_OPEN = auto()  # Players may buzz in
    ANSWERING = auto()  # Nominated player is answering
    ANSWER_RESULT = auto()  # Result shown; call advance() to continue
    ROUND_END = auto()  # Round complete; call next_round() to continue
    FINAL_BID = auto()  # Final round: all players submit bids
    FINAL_QUESTION = auto()  # Final round: question shown, players type answers
    FINAL_JUDGING = auto()  # Final round: host judges each player in turn
    GAME_OVER = auto()  # All playable rounds finished


@dataclass
class Player:
    """Runtime player state.

    Attributes:
        id: Stable identifier (e.g. Discord user ID as string).
        name: Display name.
        score: Current score; may be negative.
    """

    id: str
    name: str
    score: int = 0
    _wrong_this_question: bool = field(default=False, repr=False)


@dataclass
class Settings:
    """Game-level configuration.

    Attributes:
        buzz_window_ms: 0 = strict first-buzz-wins; >0 = random among
            all buzzes collected within the window (caller handles timing).
    """

    buzz_window_ms: int = 0


@dataclass
class QuestionRef:
    """Identifies a question currently in play.

    Attributes:
        theme_idx: Column index in the current round.
        question_idx: Row index within the theme.
        theme_name: Display name of the category.
        question: The question object from the parsed package.
    """

    theme_idx: int
    question_idx: int
    theme_name: str
    question: Question


class GameError(Exception):
    """Raised when an action is invalid in the current game state."""


class GameMachine:
    """KvizGame state machine.

    Drives a single game from question selection through scoring.
    Final rounds are skipped automatically. All state-changing methods
    raise GameError when called outside their valid phase.

    Args:
        package: Parsed .siq package.
        player_ids: Ordered list of player IDs; first element picks first.
        player_names: Mapping from player ID to display name.
        settings: Optional game settings; defaults to strict first-buzz.
    """

    def __init__(
        self,
        package: Package,
        player_ids: list[str],
        player_names: dict[str, str],
        settings: Settings | None = None,
    ) -> None:
        if not player_ids:
            raise ValueError('At least 1 player required')

        self._settings = settings or Settings()
        self._players: dict[str, Player] = {
            pid: Player(id=pid, name=player_names.get(pid, pid)) for pid in player_ids
        }
        self._player_order = list(player_ids)
        self._active_player_idx = 0

        self._rounds = [r for r in package.rounds if not r.is_final]
        if not self._rounds:
            raise ValueError('Package has no playable (non-final) rounds')
        final_rounds = [r for r in package.rounds if r.is_final]
        self._final_round: Round | None = final_rounds[0] if final_rounds else None

        self._round_idx = 0
        self._board: set[tuple[int, int]] = set()

        self._current_question: QuestionRef | None = None
        self._current_answerer_id: str | None = None
        self._auction_bid: int = 0
        self._buzzes: list[str] = []
        self._last_judged_id: str | None = None
        self._last_judged_stake: int = 0
        self._last_judged_correct: bool = True

        self._phase = Phase.BOARD

        # Final round state (populated when entering FINAL_BID)
        self._final_theme_name: str = ''
        self._final_question: Question | None = None
        self._final_bids: dict[str, int] = {}
        self._final_answers: dict[str, str] = {}
        self._final_judgment_queue: list[str] = []
        self._final_current_judge_id: str | None = None

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def phase(self) -> Phase:
        """Current game phase."""
        return self._phase

    @property
    def settings(self) -> Settings:
        """Game settings."""
        return self._settings

    @property
    def active_player_id(self) -> str:
        """ID of the player whose turn it is to select a question."""
        return self._player_order[self._active_player_idx]

    @property
    def scores(self) -> dict[str, int]:
        """Current scores as {player_id: score}."""
        return {pid: p.score for pid, p in self._players.items()}

    @property
    def player_names(self) -> dict[str, str]:
        """Display names as {player_id: name}."""
        return {pid: p.name for pid, p in self._players.items()}

    @property
    def current_question(self) -> QuestionRef | None:
        """The question currently in play, or None between questions."""
        return self._current_question

    @property
    def current_answerer_id(self) -> str | None:
        """ID of the player currently answering, or None."""
        return self._current_answerer_id

    @property
    def current_round(self) -> Round:
        """The round currently in play."""
        return self._rounds[self._round_idx]

    @property
    def played(self) -> frozenset[tuple[int, int]]:
        """Set of (theme_idx, question_idx) pairs played in the current round."""
        return frozenset(self._board)

    @property
    def round_complete(self) -> bool:
        """True when every question in the current round has been played."""
        total = sum(len(t.questions) for t in self.current_round.themes)
        return len(self._board) >= total

    @property
    def has_final_round(self) -> bool:
        """True if the package contains a final round."""
        return self._final_round is not None

    @property
    def final_theme_name(self) -> str:
        return self._final_theme_name

    @property
    def final_question(self) -> Question | None:
        return self._final_question

    @property
    def final_bids_submitted(self) -> list[str]:
        return list(self._final_bids.keys())

    @property
    def final_answers_submitted(self) -> list[str]:
        return list(self._final_answers.keys())

    @property
    def final_current_judge_id(self) -> str | None:
        return self._final_current_judge_id

    def final_current_answer(self) -> str:
        """Answer of the player currently being judged."""
        if self._final_current_judge_id is None:
            return ''
        return self._final_answers.get(self._final_current_judge_id, '')

    def final_current_bid(self) -> int:
        """Bid of the player currently being judged."""
        if self._final_current_judge_id is None:
            return 0
        return self._final_bids.get(self._final_current_judge_id, 0)

    # ------------------------------------------------------------------
    # Phase: BOARD
    # ------------------------------------------------------------------

    def select_question(
        self, player_id: str, theme_idx: int, question_idx: int
    ) -> Phase:
        """Active player selects a question from the board.

        Args:
            player_id: Must equal active_player_id.
            theme_idx: Theme (column) index in the current round.
            question_idx: Question (row) index within the theme.

        Returns:
            New phase: AUCTION_BIDDING, CAT_TRANSFER, or QUESTION.

        Raises:
            GameError: Wrong phase, wrong player, or question already played.
        """
        self._require_phase(Phase.BOARD)
        if player_id != self.active_player_id:
            raise GameError(f'{player_id!r} is not the active player')

        round_ = self.current_round
        if theme_idx >= len(round_.themes):
            raise GameError(f'theme_idx {theme_idx} out of range')
        theme = round_.themes[theme_idx]
        if question_idx >= len(theme.questions):
            raise GameError(f'question_idx {question_idx} out of range')
        if (theme_idx, question_idx) in self._board:
            raise GameError('Question already played')

        question = theme.questions[question_idx]
        self._board.add((theme_idx, question_idx))
        self._current_question = QuestionRef(
            theme_idx=theme_idx,
            question_idx=question_idx,
            theme_name=theme.name,
            question=question,
        )
        self._reset_for_new_question()

        if question.q_type == 'auction':
            self._phase = Phase.AUCTION_BIDDING
        elif question.q_type in ('cat', 'bagcat'):
            self._phase = Phase.CAT_TRANSFER
        else:
            self._phase = Phase.QUESTION

        return self._phase

    # ------------------------------------------------------------------
    # Phase: AUCTION_BIDDING
    # ------------------------------------------------------------------

    def place_bid(self, player_id: str, amount: int) -> int:
        """Active player places a bid for an auction question.

        The bid must be at least max(1, question.price). No upper bound —
        going into debt is allowed.

        Args:
            player_id: Must equal active_player_id.
            amount: Bid amount.

        Returns:
            Accepted bid amount.
        """
        self._require_phase(Phase.AUCTION_BIDDING)
        if player_id != self.active_player_id:
            raise GameError('Only the active player bids in an auction')

        min_bid = max(1, self._current_question.question.price)
        if amount < min_bid:
            raise GameError(f'Bid must be at least {min_bid}')

        self._auction_bid = amount
        self._current_answerer_id = player_id
        self._phase = Phase.QUESTION
        return self._auction_bid

    # ------------------------------------------------------------------
    # Phase: CAT_TRANSFER
    # ------------------------------------------------------------------

    def transfer_cat(self, player_id: str, recipient_id: str) -> str:
        """Active player nominates another player to answer a cat question.

        Args:
            player_id: Must equal active_player_id.
            recipient_id: The player who will answer; cannot be the selector.

        Returns:
            recipient_id.
        """
        self._require_phase(Phase.CAT_TRANSFER)
        if player_id != self.active_player_id:
            raise GameError('Only the active player transfers the cat')
        if recipient_id == player_id:
            raise GameError('Cannot transfer the cat to yourself')
        if recipient_id not in self._players:
            raise GameError(f'Unknown player {recipient_id!r}')

        self._current_answerer_id = recipient_id
        self._phase = Phase.QUESTION
        return recipient_id

    # ------------------------------------------------------------------
    # Phase: QUESTION → BUZZER_OPEN or ANSWERING
    # ------------------------------------------------------------------

    def open_buzzer(self) -> Phase:
        """Signal that the question is fully shown and players may now respond.

        For auction and cat questions the answerer is already fixed, so the
        phase jumps directly to ANSWERING instead of BUZZER_OPEN.

        Returns:
            ANSWERING (fixed answerer) or BUZZER_OPEN.
        """
        self._require_phase(Phase.QUESTION)
        if self._current_answerer_id is not None:
            self._phase = Phase.ANSWERING
        else:
            self._phase = Phase.BUZZER_OPEN
        return self._phase

    # ------------------------------------------------------------------
    # Phase: BUZZER_OPEN
    # ------------------------------------------------------------------

    def buzz(self, player_id: str) -> bool:
        """Record a buzz attempt from a player.

        Args:
            player_id: The buzzing player.

        Returns:
            True if the buzz was recorded; False if the player answered
            wrong for this question and is blocked.

        Raises:
            GameError: Wrong phase or unknown player.
        """
        self._require_phase(Phase.BUZZER_OPEN)
        if player_id not in self._players:
            raise GameError(f'Unknown player {player_id!r}')
        if self._players[player_id]._wrong_this_question:
            return False
        if player_id not in self._buzzes:
            self._buzzes.append(player_id)
        return True

    def close_buzzer(self) -> str | None:
        """Select the answerer from collected buzzes and move to ANSWERING.

        With buzz_window_ms=0: first recorded buzz wins.
        With buzz_window_ms>0: random pick among all recorded buzzes
        (the caller is responsible for waiting the window before calling).

        Returns:
            Selected player ID, or None if no eligible buzz was recorded
            (phase becomes ANSWER_RESULT).
        """
        self._require_phase(Phase.BUZZER_OPEN)
        eligible = [
            pid for pid in self._buzzes if not self._players[pid]._wrong_this_question
        ]

        if not eligible:
            self._phase = Phase.ANSWER_RESULT
            return None

        winner = (
            random.choice(eligible)
            if self._settings.buzz_window_ms > 0
            else eligible[0]
        )
        self._current_answerer_id = winner
        self._phase = Phase.ANSWERING
        return winner

    # ------------------------------------------------------------------
    # Phase: ANSWERING
    # ------------------------------------------------------------------

    def judge_answer(self, correct: bool) -> Phase:
        """Judge the current answerer's response.

        Correct answer: player gains the stake; they become the active
        player; phase moves to ANSWER_RESULT.

        Wrong answer: player loses the stake; if others can still buzz
        (normal questions), phase returns to BUZZER_OPEN; otherwise
        ANSWER_RESULT.

        Args:
            correct: True if the answer was accepted.

        Returns:
            New phase: BUZZER_OPEN or ANSWER_RESULT.
        """
        self._require_phase(Phase.ANSWERING)
        player = self._players[self._current_answerer_id]
        question = self._current_question.question
        stake = self._auction_bid if question.q_type == 'auction' else question.price

        self._last_judged_id = player.id
        self._last_judged_stake = stake
        self._last_judged_correct = correct

        if correct:
            player.score += stake
            self._active_player_idx = self._player_order.index(player.id)
            self._phase = Phase.ANSWER_RESULT
        else:
            player.score -= stake
            player._wrong_this_question = True
            self._current_answerer_id = None

            fixed_answerer_type = question.q_type in ('cat', 'bagcat', 'auction')
            can_buzz = not fixed_answerer_type and any(
                not p._wrong_this_question for p in self._players.values()
            )
            if can_buzz:
                self._buzzes.clear()
                self._phase = Phase.BUZZER_OPEN
            else:
                self._phase = Phase.ANSWER_RESULT

        return self._phase

    # ------------------------------------------------------------------
    # Phase: ANSWER_RESULT
    # ------------------------------------------------------------------

    def advance(self) -> Phase:
        """Acknowledge the result and move to the next question or round.

        Returns:
            BOARD if questions remain in this round, ROUND_END otherwise.
        """
        self._require_phase(Phase.ANSWER_RESULT)
        self._current_question = None
        self._current_answerer_id = None
        self._auction_bid = 0
        self._buzzes.clear()
        self._last_judged_id = None

        self._phase = Phase.ROUND_END if self.round_complete else Phase.BOARD
        return self._phase

    def accept_appeal(self) -> None:
        """Reverse the most recent wrong judgment: undo penalty and award the stake.

        The appealing player's score is corrected (+stake*2) and they become
        the active player for the next question.

        Raises:
            GameError: Not in ANSWER_RESULT or last judgment was not wrong.
        """
        self._require_phase(Phase.ANSWER_RESULT)
        if self._last_judged_id is None or self._last_judged_correct:
            raise GameError('No wrong judgment to appeal')
        player = self._players[self._last_judged_id]
        player.score += self._last_judged_stake * 2
        player._wrong_this_question = False
        self._active_player_idx = self._player_order.index(self._last_judged_id)
        self._last_judged_id = None

    @property
    def last_wrong_judged_id(self) -> str | None:
        """Player eligible to appeal: the last judged player, only if wrong."""
        if not self._last_judged_correct and self._last_judged_id is not None:
            return self._last_judged_id
        return None

    # ------------------------------------------------------------------
    # Phase: ROUND_END
    # ------------------------------------------------------------------

    def next_round(self) -> Phase:
        """Advance to the next non-final round, or start the final round, or end the game.

        Returns:
            BOARD, FINAL_BID, or GAME_OVER.
        """
        self._require_phase(Phase.ROUND_END)
        self._round_idx += 1
        self._board.clear()
        if self._round_idx < len(self._rounds):
            self._phase = Phase.BOARD
        elif self._final_round is not None:
            self._start_final()
        else:
            self._phase = Phase.GAME_OVER
        return self._phase

    # ------------------------------------------------------------------
    # Phase: FINAL_BID
    # ------------------------------------------------------------------

    def _start_final(self) -> None:
        """Initialise final round state and transition to FINAL_BID."""
        assert self._final_round is not None
        self._final_bids = {}
        self._final_answers = {}
        self._final_judgment_queue = []
        self._final_current_judge_id = None
        theme = self._final_round.themes[0] if self._final_round.themes else None
        self._final_theme_name = theme.name if theme else ''
        self._final_question = (
            theme.questions[0] if (theme and theme.questions) else None
        )
        self._phase = Phase.FINAL_BID

    def place_final_bid(self, player_id: str, amount: int) -> Phase:
        """Player submits a bid for the final round.

        Args:
            player_id: The bidding player.
            amount: Bid amount; must be at least 1.

        Returns:
            FINAL_BID while waiting for others; FINAL_QUESTION when all have bid.
        """
        self._require_phase(Phase.FINAL_BID)
        if player_id not in self._players:
            raise GameError(f'Unknown player {player_id!r}')
        if player_id in self._final_bids:
            raise GameError('Already placed a bid')
        if amount < 1:
            raise GameError('Bid must be at least 1')
        self._final_bids[player_id] = amount
        if set(self._final_bids) == set(self._players):
            self._phase = Phase.FINAL_QUESTION
        return self._phase

    # ------------------------------------------------------------------
    # Phase: FINAL_QUESTION
    # ------------------------------------------------------------------

    def submit_final_answer(self, player_id: str, answer: str) -> None:
        """Player submits their written answer.

        Args:
            player_id: The answering player.
            answer: The player's answer text.
        """
        self._require_phase(Phase.FINAL_QUESTION)
        if player_id not in self._players:
            raise GameError(f'Unknown player {player_id!r}')
        self._final_answers[player_id] = answer.strip()

    def start_final_judging(self) -> Phase:
        """Host starts the judging phase.

        Returns:
            FINAL_JUDGING, or GAME_OVER if no players to judge.
        """
        self._require_phase(Phase.FINAL_QUESTION)
        self._final_judgment_queue = list(self._players.keys())
        self._final_current_judge_id = (
            self._final_judgment_queue.pop(0) if self._final_judgment_queue else None
        )
        self._phase = (
            Phase.FINAL_JUDGING if self._final_current_judge_id else Phase.GAME_OVER
        )
        return self._phase

    # ------------------------------------------------------------------
    # Phase: FINAL_JUDGING
    # ------------------------------------------------------------------

    def judge_final_answer(self, correct: bool) -> Phase:
        """Host judges the current player's final answer.

        Args:
            correct: True if the answer is accepted.

        Returns:
            FINAL_JUDGING if more players remain, GAME_OVER otherwise.
        """
        self._require_phase(Phase.FINAL_JUDGING)
        if self._final_current_judge_id is None:
            raise GameError('No player to judge')
        player = self._players[self._final_current_judge_id]
        bid = self._final_bids.get(self._final_current_judge_id, 0)
        player.score += bid if correct else -bid
        self._final_current_judge_id = (
            self._final_judgment_queue.pop(0) if self._final_judgment_queue else None
        )
        self._phase = (
            Phase.FINAL_JUDGING if self._final_current_judge_id else Phase.GAME_OVER
        )
        return self._phase

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def correct_scores(self, adjustments: dict[str, int]) -> None:
        """Apply manual score deltas.

        Args:
            adjustments: Mapping of player_id → delta to add (negative to subtract).

        Raises:
            GameError: If any player_id is unknown.
        """
        unknown = set(adjustments) - set(self._players)
        if unknown:
            raise GameError(f'Unknown player(s): {", ".join(sorted(unknown))}')
        for pid, delta in adjustments.items():
            self._players[pid].score += delta

    def _require_phase(self, expected: Phase) -> None:
        if self._phase != expected:
            raise GameError(
                f'Action requires phase {expected.name}, current is {self._phase.name}'
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize game state to a JSON-compatible dict."""
        return {
            'settings': {'buzz_window_ms': self._settings.buzz_window_ms},
            'players': [
                {
                    'id': p.id,
                    'name': p.name,
                    'score': p.score,
                    'wrong_this_question': p._wrong_this_question,
                }
                for p in self._players.values()
            ],
            'player_order': self._player_order,
            'active_player_idx': self._active_player_idx,
            'round_idx': self._round_idx,
            'board': [list(pair) for pair in self._board],
            'phase': self._phase.name,
            'current_question': (
                {
                    'theme_idx': self._current_question.theme_idx,
                    'question_idx': self._current_question.question_idx,
                    'theme_name': self._current_question.theme_name,
                }
                if self._current_question is not None
                else None
            ),
            'current_answerer_id': self._current_answerer_id,
            'auction_bid': self._auction_bid,
            'buzzes': list(self._buzzes),
            'last_judged_id': self._last_judged_id,
            'last_judged_stake': self._last_judged_stake,
            'last_judged_correct': self._last_judged_correct,
            'final_bids': self._final_bids,
            'final_answers': self._final_answers,
            'final_judgment_queue': self._final_judgment_queue,
            'final_current_judge_id': self._final_current_judge_id,
        }

    @classmethod
    def from_dict(cls, package: Package, data: dict[str, Any]) -> GameMachine:
        """Reconstruct a GameMachine from a dict produced by to_dict().

        Args:
            package: Re-parsed .siq package (questions are referenced by index).
            data: Serialized state dict.
        """
        obj: GameMachine = cls.__new__(cls)
        obj._settings = Settings(buzz_window_ms=data['settings']['buzz_window_ms'])
        obj._players = {}
        for p_data in data['players']:
            p = Player(id=p_data['id'], name=p_data['name'], score=p_data['score'])
            p._wrong_this_question = p_data['wrong_this_question']
            obj._players[p.id] = p
        obj._player_order = data['player_order']
        obj._active_player_idx = data['active_player_idx']
        obj._rounds = [r for r in package.rounds if not r.is_final]
        final_rounds = [r for r in package.rounds if r.is_final]
        obj._final_round = final_rounds[0] if final_rounds else None
        obj._round_idx = data['round_idx']
        obj._board = {(pair[0], pair[1]) for pair in data['board']}
        obj._phase = Phase[data['phase']]

        cq_data = data.get('current_question')
        if cq_data is not None:
            t_idx = cq_data['theme_idx']
            q_idx = cq_data['question_idx']
            theme = obj._rounds[obj._round_idx].themes[t_idx]
            obj._current_question = QuestionRef(
                theme_idx=t_idx,
                question_idx=q_idx,
                theme_name=cq_data['theme_name'],
                question=theme.questions[q_idx],
            )
        else:
            obj._current_question = None

        obj._current_answerer_id = data.get('current_answerer_id')
        obj._auction_bid = data.get('auction_bid', 0)
        obj._buzzes = data.get('buzzes', [])
        obj._last_judged_id = data.get('last_judged_id')
        obj._last_judged_stake = data.get('last_judged_stake', 0)
        obj._last_judged_correct = data.get('last_judged_correct', True)
        obj._final_bids = data.get('final_bids', {})
        obj._final_answers = data.get('final_answers', {})
        obj._final_judgment_queue = data.get('final_judgment_queue', [])
        obj._final_current_judge_id = data.get('final_current_judge_id')
        # Restore final question reference if in a final phase
        final_phases = {Phase.FINAL_BID, Phase.FINAL_QUESTION, Phase.FINAL_JUDGING}
        if obj._phase in final_phases and obj._final_round is not None:
            theme = obj._final_round.themes[0] if obj._final_round.themes else None
            obj._final_theme_name = theme.name if theme else ''
            obj._final_question = (
                theme.questions[0] if (theme and theme.questions) else None
            )
        else:
            obj._final_theme_name = ''
            obj._final_question = None
        return obj

    def _reset_for_new_question(self) -> None:
        self._buzzes.clear()
        self._current_answerer_id = None
        self._auction_bid = 0
        self._last_judged_id = None
        self._last_judged_correct = True
        for player in self._players.values():
            player._wrong_this_question = False
