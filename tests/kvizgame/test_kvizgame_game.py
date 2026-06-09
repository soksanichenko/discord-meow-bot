"""Tests for sources/lib/kvizgame/game.py."""

import pytest

from sources.lib.kvizgame.game import GameError, GameMachine, Phase, Settings
from sources.lib.kvizgame.parser import Atom, Package, Question, Round, Theme

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _question(price: int = 100, q_type: str = 'simple', **type_params) -> Question:
    q = Question(
        price=price,
        q_type=q_type,
        type_params=dict(type_params),
        scenario=[Atom(type='text', content='Question text')],
        right=['Correct answer'],
    )
    return q


def _theme(name: str = 'Theme', questions: list[Question] | None = None) -> Theme:
    return Theme(name=name, questions=questions or [_question(100), _question(200)])


def _round(
    name: str = 'Round 1', themes: list[Theme] | None = None, is_final: bool = False
) -> Round:
    return Round(name=name, is_final=is_final, themes=themes or [_theme()])


def _package(*rounds: Round) -> Package:

    return Package(name='Test', rounds=list(rounds))


def _game(
    rounds: list[Round] | None = None,
    players: list[str] | None = None,
    buzz_window_ms: int = 0,
) -> GameMachine:
    rounds = rounds or [_round()]
    players = players or ['p1', 'p2']
    names = {pid: pid.upper() for pid in players}
    return GameMachine(
        _package(*rounds), players, names, Settings(buzz_window_ms=buzz_window_ms)
    )


def _play_question(
    game: GameMachine, selector: str = 'p1', theme_idx: int = 0, question_idx: int = 0
) -> None:
    """Helper: select a question and open the buzzer."""
    game.select_question(selector, theme_idx, question_idx)
    if game.phase == Phase.QUESTION:
        game.open_buzzer()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestInit:
    def test_starts_in_board_phase(self):
        assert _game().phase == Phase.BOARD

    def test_first_player_is_active(self):
        g = _game(players=['p1', 'p2'])
        assert g.active_player_id == 'p1'

    def test_all_scores_start_at_zero(self):
        g = _game(players=['p1', 'p2'])
        assert g.scores == {'p1': 0, 'p2': 0}

    def test_requires_at_least_two_players(self):
        with pytest.raises(ValueError, match='At least 2 players'):
            _game(players=['only'])

    def test_skips_final_round(self):
        rounds = [_round('R1'), _round('Final', is_final=True)]
        g = _game(rounds=rounds)
        assert g.current_round.name == 'R1'
        assert len(g._rounds) == 1

    def test_raises_when_only_final_rounds(self):
        with pytest.raises(ValueError, match='no playable'):
            _game(rounds=[_round(is_final=True)])


# ---------------------------------------------------------------------------
# Board — question selection
# ---------------------------------------------------------------------------


class TestSelectQuestion:
    def test_correct_player_can_select(self):
        g = _game()
        phase = g.select_question('p1', 0, 0)
        assert phase == Phase.QUESTION

    def test_wrong_player_raises(self):
        g = _game()
        with pytest.raises(GameError, match='not the active player'):
            g.select_question('p2', 0, 0)

    def test_already_played_raises(self):
        g = _game()
        g.select_question('p1', 0, 0)
        g.open_buzzer()
        g.buzz('p1')
        g.close_buzzer()
        g.judge_answer(True)
        g.advance()
        with pytest.raises(GameError, match='already played'):
            g.select_question('p1', 0, 0)

    def test_out_of_range_theme_raises(self):
        g = _game()
        with pytest.raises(GameError, match='theme_idx'):
            g.select_question('p1', 99, 0)

    def test_out_of_range_question_raises(self):
        g = _game()
        with pytest.raises(GameError, match='question_idx'):
            g.select_question('p1', 0, 99)

    def test_question_marked_as_played(self):
        g = _game()
        g.select_question('p1', 0, 0)
        assert (0, 0) in g.played

    def test_auction_question_goes_to_bidding(self):
        rounds = [_round(themes=[_theme(questions=[_question(q_type='auction')])])]
        g = _game(rounds=rounds)
        phase = g.select_question('p1', 0, 0)
        assert phase == Phase.AUCTION_BIDDING

    def test_cat_question_goes_to_transfer(self):
        rounds = [_round(themes=[_theme(questions=[_question(q_type='cat')])])]
        g = _game(rounds=rounds)
        phase = g.select_question('p1', 0, 0)
        assert phase == Phase.CAT_TRANSFER

    def test_bagcat_question_goes_to_transfer(self):
        rounds = [_round(themes=[_theme(questions=[_question(q_type='bagcat')])])]
        g = _game(rounds=rounds)
        phase = g.select_question('p1', 0, 0)
        assert phase == Phase.CAT_TRANSFER


# ---------------------------------------------------------------------------
# Auction bidding
# ---------------------------------------------------------------------------


class TestAuctionBidding:
    def _auction_game(self) -> GameMachine:
        rounds = [
            _round(themes=[_theme(questions=[_question(price=200, q_type='auction')])])
        ]
        return _game(rounds=rounds)

    def test_valid_bid_accepted(self):
        g = self._auction_game()
        g.select_question('p1', 0, 0)
        amount = g.place_bid('p1', 200)
        assert amount == 200
        assert g.phase == Phase.QUESTION

    def test_bid_below_minimum_raises(self):
        g = self._auction_game()
        g.select_question('p1', 0, 0)
        with pytest.raises(GameError, match='at least'):
            g.place_bid('p1', 50)

    def test_wrong_player_raises(self):
        g = self._auction_game()
        g.select_question('p1', 0, 0)
        with pytest.raises(GameError, match='active player'):
            g.place_bid('p2', 200)

    def test_minimum_bid_is_one_when_price_zero(self):
        rounds = [
            _round(themes=[_theme(questions=[_question(price=0, q_type='auction')])])
        ]
        g = _game(rounds=rounds)
        g.select_question('p1', 0, 0)
        amount = g.place_bid('p1', 1)
        assert amount == 1

    def test_correct_auction_answer_adds_bid_not_price(self):
        g = self._auction_game()
        g.select_question('p1', 0, 0)
        g.place_bid('p1', 500)
        g.open_buzzer()  # goes to ANSWERING directly
        g.judge_answer(True)
        assert g.scores['p1'] == 500

    def test_wrong_auction_answer_deducts_bid(self):
        g = self._auction_game()
        g.select_question('p1', 0, 0)
        g.place_bid('p1', 500)
        g.open_buzzer()
        g.judge_answer(False)
        assert g.scores['p1'] == -500
        assert g.phase == Phase.ANSWER_RESULT  # no one else can answer auction


# ---------------------------------------------------------------------------
# Cat transfer
# ---------------------------------------------------------------------------


class TestCatTransfer:
    def _cat_game(self) -> GameMachine:
        rounds = [
            _round(themes=[_theme(questions=[_question(price=300, q_type='cat')])])
        ]
        return _game(rounds=rounds)

    def test_transfer_to_other_player(self):
        g = self._cat_game()
        g.select_question('p1', 0, 0)
        recipient = g.transfer_cat('p1', 'p2')
        assert recipient == 'p2'
        assert g.current_answerer_id == 'p2'
        assert g.phase == Phase.QUESTION

    def test_transfer_to_self_raises(self):
        g = self._cat_game()
        g.select_question('p1', 0, 0)
        with pytest.raises(GameError, match='yourself'):
            g.transfer_cat('p1', 'p1')

    def test_transfer_wrong_player_raises(self):
        g = self._cat_game()
        g.select_question('p1', 0, 0)
        with pytest.raises(GameError, match='active player'):
            g.transfer_cat('p2', 'p1')

    def test_transfer_unknown_recipient_raises(self):
        g = self._cat_game()
        g.select_question('p1', 0, 0)
        with pytest.raises(GameError, match='Unknown player'):
            g.transfer_cat('p1', 'ghost')

    def test_open_buzzer_goes_to_answering_after_transfer(self):
        g = self._cat_game()
        g.select_question('p1', 0, 0)
        g.transfer_cat('p1', 'p2')
        phase = g.open_buzzer()
        assert phase == Phase.ANSWERING

    def test_correct_cat_answer_scores_recipient(self):
        g = self._cat_game()
        g.select_question('p1', 0, 0)
        g.transfer_cat('p1', 'p2')
        g.open_buzzer()
        g.judge_answer(True)
        assert g.scores['p2'] == 300


# ---------------------------------------------------------------------------
# Buzzer
# ---------------------------------------------------------------------------


class TestBuzzer:
    def test_open_buzzer_moves_to_buzzer_open(self):
        g = _game()
        g.select_question('p1', 0, 0)
        phase = g.open_buzzer()
        assert phase == Phase.BUZZER_OPEN

    def test_buzz_recorded(self):
        g = _game()
        _play_question(g)
        result = g.buzz('p1')
        assert result is True

    def test_duplicate_buzz_ignored(self):
        g = _game()
        _play_question(g)
        g.buzz('p1')
        g.buzz('p1')
        assert g._buzzes.count('p1') == 1

    def test_unknown_player_raises(self):
        g = _game()
        _play_question(g)
        with pytest.raises(GameError, match='Unknown player'):
            g.buzz('ghost')

    def test_close_buzzer_strict_picks_first(self):
        g = _game(buzz_window_ms=0)
        _play_question(g)
        g.buzz('p2')
        g.buzz('p1')
        winner = g.close_buzzer()
        assert winner == 'p2'
        assert g.phase == Phase.ANSWERING

    def test_close_buzzer_window_picks_randomly(self):
        """With window > 0, both players should win over many trials."""
        winners = set()
        for _ in range(50):
            g = _game(buzz_window_ms=150)
            _play_question(g)
            g.buzz('p1')
            g.buzz('p2')
            winners.add(g.close_buzzer())
        assert winners == {'p1', 'p2'}

    def test_close_buzzer_no_buzzes_returns_none(self):
        g = _game()
        _play_question(g)
        result = g.close_buzzer()
        assert result is None
        assert g.phase == Phase.ANSWER_RESULT


# ---------------------------------------------------------------------------
# Answering and scoring
# ---------------------------------------------------------------------------


class TestJudging:
    def test_correct_answer_adds_score(self):
        g = _game()
        _play_question(g)
        g.buzz('p1')
        g.close_buzzer()
        g.judge_answer(True)
        assert g.scores['p1'] == 100

    def test_correct_answer_makes_winner_active_player(self):
        g = _game()
        _play_question(g)
        g.buzz('p2')
        g.close_buzzer()
        g.judge_answer(True)
        assert g.active_player_id == 'p2'

    def test_correct_answer_moves_to_answer_result(self):
        g = _game()
        _play_question(g)
        g.buzz('p1')
        g.close_buzzer()
        phase = g.judge_answer(True)
        assert phase == Phase.ANSWER_RESULT

    def test_wrong_answer_deducts_score(self):
        g = _game()
        _play_question(g)
        g.buzz('p1')
        g.close_buzzer()
        g.judge_answer(False)
        assert g.scores['p1'] == -100

    def test_wrong_answer_reopens_buzzer_for_others(self):
        g = _game()
        _play_question(g)
        g.buzz('p1')
        g.close_buzzer()
        phase = g.judge_answer(False)
        assert phase == Phase.BUZZER_OPEN

    def test_wrong_player_blocked_from_buzzing_again(self):
        g = _game()
        _play_question(g)
        g.buzz('p1')
        g.close_buzzer()
        g.judge_answer(False)
        # p1 already answered wrong — buzz returns False
        result = g.buzz('p1')
        assert result is False

    def test_all_wrong_moves_to_answer_result(self):
        g = _game(players=['p1', 'p2'])
        _play_question(g)
        g.buzz('p1')
        g.close_buzzer()
        g.judge_answer(False)  # p1 wrong, buzzer reopens
        g.buzz('p2')
        g.close_buzzer()
        phase = g.judge_answer(False)  # p2 wrong, no one left
        assert phase == Phase.ANSWER_RESULT


# ---------------------------------------------------------------------------
# Advance and round flow
# ---------------------------------------------------------------------------


class TestAdvanceAndRounds:
    def _full_question(
        self,
        game: GameMachine,
        selector: str,
        theme_idx: int,
        q_idx: int,
        correct: bool = True,
    ) -> None:
        """Play one question through to ANSWER_RESULT."""
        game.select_question(selector, theme_idx, q_idx)
        game.open_buzzer()
        game.buzz(selector)
        game.close_buzzer()
        game.judge_answer(correct)

    def test_advance_returns_to_board(self):
        g = _game()
        self._full_question(g, 'p1', 0, 0)
        phase = g.advance()
        assert phase == Phase.BOARD

    def test_advance_goes_to_round_end_when_complete(self):
        # Round with a single question
        rounds = [_round(themes=[_theme(questions=[_question()])])]
        g = _game(rounds=rounds)
        self._full_question(g, 'p1', 0, 0)
        phase = g.advance()
        assert phase == Phase.ROUND_END

    def test_round_complete_flag(self):
        rounds = [_round(themes=[_theme(questions=[_question()])])]
        g = _game(rounds=rounds)
        assert not g.round_complete
        self._full_question(g, 'p1', 0, 0)
        assert g.round_complete

    def test_next_round_moves_to_board(self):
        rounds = [
            _round('R1', themes=[_theme(questions=[_question()])]),
            _round('R2', themes=[_theme(questions=[_question()])]),
        ]
        g = _game(rounds=rounds)
        self._full_question(g, 'p1', 0, 0)
        g.advance()  # → ROUND_END
        phase = g.next_round()
        assert phase == Phase.BOARD
        assert g.current_round.name == 'R2'

    def test_next_round_clears_board(self):
        rounds = [
            _round('R1', themes=[_theme(questions=[_question()])]),
            _round('R2', themes=[_theme(questions=[_question()])]),
        ]
        g = _game(rounds=rounds)
        self._full_question(g, 'p1', 0, 0)
        g.advance()
        g.next_round()
        assert g.played == frozenset()

    def test_next_round_on_last_round_ends_game(self):
        rounds = [_round(themes=[_theme(questions=[_question()])])]
        g = _game(rounds=rounds)
        self._full_question(g, 'p1', 0, 0)
        g.advance()
        phase = g.next_round()
        assert phase == Phase.GAME_OVER

    def test_scores_accumulate_across_rounds(self):
        rounds = [
            _round('R1', themes=[_theme(questions=[_question(100)])]),
            _round('R2', themes=[_theme(questions=[_question(200)])]),
        ]
        g = _game(rounds=rounds)
        self._full_question(g, 'p1', 0, 0)
        g.advance()
        g.next_round()
        self._full_question(g, 'p1', 0, 0)
        assert g.scores['p1'] == 300


# ---------------------------------------------------------------------------
# Phase guard
# ---------------------------------------------------------------------------


class TestPhaseGuard:
    def test_cannot_buzz_in_board_phase(self):
        g = _game()
        with pytest.raises(GameError, match='BOARD'):
            g.buzz('p1')

    def test_cannot_judge_in_board_phase(self):
        g = _game()
        with pytest.raises(GameError, match='BOARD'):
            g.judge_answer(True)

    def test_cannot_advance_in_board_phase(self):
        g = _game()
        with pytest.raises(GameError, match='BOARD'):
            g.advance()

    def test_cannot_next_round_in_board_phase(self):
        g = _game()
        with pytest.raises(GameError, match='BOARD'):
            g.next_round()

    def test_cannot_select_in_buzzer_open_phase(self):
        g = _game()
        _play_question(g)
        with pytest.raises(GameError, match='BUZZER_OPEN'):
            g.select_question('p1', 0, 1)
