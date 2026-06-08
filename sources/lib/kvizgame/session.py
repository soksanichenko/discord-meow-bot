"""Per-channel game session — manages WebSocket connections and game flow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from sources.lib.kvizgame.game import GameError, GameMachine, Phase
from sources.lib.kvizgame.protocol import In, Out, decode, encode

logger = logging.getLogger(__name__)

# How long to wait for any buzz before auto-closing (both window modes).
_BUZZ_AUTO_CLOSE_S = 30.0


class GameSession:
    """Manages one game and all WebSocket connections for a channel.

    Args:
        channel_id: Stable identifier for this session (e.g. Discord channel ID).
        game: Initialised GameMachine ready to play.
    """

    def __init__(self, channel_id: str, game: GameMachine) -> None:
        self._channel_id = channel_id
        self._game = game
        self._players: dict[str, web.WebSocketResponse] = {}
        self._buzz_task: asyncio.Task[None] | None = None

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def player_count(self) -> int:
        return len(self._players)

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, player_id: str, ws: web.WebSocketResponse) -> None:
        """Register a new WebSocket connection for a player.

        Sends the current game state to the new player and broadcasts
        a join notification to everyone else.

        Args:
            player_id: Identifier for the connecting player.
            ws: The player's WebSocket response object.
        """
        self._players[player_id] = ws
        # Notify others first, then send full state to the newcomer.
        await self._broadcast_except(
            player_id, Out.PLAYER_JOINED, {'player_id': player_id}
        )
        await ws.send_str(encode(Out.STATE, self._state_data()))
        logger.debug('Player %r joined session %r', player_id, self._channel_id)

    async def disconnect(self, player_id: str) -> None:
        """Remove a player's connection and notify others.

        Args:
            player_id: The disconnecting player.
        """
        self._players.pop(player_id, None)
        await self._broadcast(Out.PLAYER_LEFT, {'player_id': player_id})
        logger.debug('Player %r left session %r', player_id, self._channel_id)

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    async def handle(self, player_id: str, raw: str) -> None:
        """Parse and dispatch an incoming message from a player.

        Broadcasts updated state after any successful action.
        Sends an error back to the sender on invalid actions.

        Args:
            player_id: The sending player.
            raw: Raw JSON message string.
        """
        try:
            op, data = decode(raw)
        except ValueError as exc:
            await self._send_error(player_id, str(exc))
            return

        try:
            await self._dispatch(player_id, op, data)
        except (GameError, KeyError, TypeError, ValueError) as exc:
            await self._send_error(player_id, str(exc))

    async def _dispatch(self, player_id: str, op: str, data: dict[str, Any]) -> None:
        game = self._game

        if op == In.SELECT:
            game.select_question(
                player_id, int(data['theme_idx']), int(data['question_idx'])
            )
            await self._broadcast_state()

        elif op == In.BID:
            game.place_bid(player_id, int(data['amount']))
            await self._broadcast_state()

        elif op == In.TRANSFER:
            game.transfer_cat(player_id, str(data['recipient_id']))
            await self._broadcast_state()

        elif op == In.OPEN_BUZZER:
            phase = game.open_buzzer()
            await self._broadcast_state()
            if phase == Phase.BUZZER_OPEN:
                await self._schedule_buzz_close()

        elif op == In.BUZZ:
            accepted = game.buzz(player_id)
            if accepted and game.settings.buzz_window_ms == 0:
                await self._close_buzzer()
            else:
                await self._broadcast_state()

        elif op == In.JUDGE:
            game.judge_answer(bool(data['correct']))
            await self._broadcast_state()

        elif op == In.ADVANCE:
            game.advance()
            await self._broadcast_state()

        elif op == In.NEXT_ROUND:
            game.next_round()
            await self._broadcast_state()

        else:
            raise ValueError(f'Unknown op {op!r}')

    # ------------------------------------------------------------------
    # Buzz window timer
    # ------------------------------------------------------------------

    async def _schedule_buzz_close(self) -> None:
        """Start the buzz-window timer.

        For buzz_window_ms > 0: wait that many milliseconds then close.
        For buzz_window_ms == 0: only a long fallback timeout runs
        (the normal path closes immediately on first buzz in _dispatch).
        """
        self._cancel_buzz_task()
        delay = (
            self._game.settings.buzz_window_ms / 1000
            if self._game.settings.buzz_window_ms > 0
            else _BUZZ_AUTO_CLOSE_S
        )
        self._buzz_task = asyncio.create_task(self._buzz_timer(delay))

    async def _buzz_timer(self, delay_s: float) -> None:
        await asyncio.sleep(delay_s)
        if self._game.phase == Phase.BUZZER_OPEN:
            await self._close_buzzer()

    async def _close_buzzer(self) -> None:
        self._cancel_buzz_task()
        self._game.close_buzzer()
        await self._broadcast_state()

    def _cancel_buzz_task(self) -> None:
        if self._buzz_task and not self._buzz_task.done():
            self._buzz_task.cancel()
        self._buzz_task = None

    # ------------------------------------------------------------------
    # State snapshot
    # ------------------------------------------------------------------

    def _state_data(self) -> dict[str, Any]:
        game = self._game
        phase = game.phase

        board: list[dict[str, Any]] = []
        if phase != Phase.GAME_OVER:
            for t_idx, theme in enumerate(game.current_round.themes):
                board.append(
                    {
                        'name': theme.name,
                        'questions': [
                            {
                                'price': q.price,
                                'played': (t_idx, q_idx) in game.played,
                            }
                            for q_idx, q in enumerate(theme.questions)
                        ],
                    }
                )

        current_question: dict[str, Any] | None = None
        if game.current_question is not None:
            cq = game.current_question
            current_question = {
                'theme_name': cq.theme_name,
                'price': cq.question.price,
                'q_type': cq.question.q_type,
                'scenario': [
                    {'type': a.type, 'content': a.content, 'time': a.time}
                    for a in cq.question.scenario
                ],
                # TODO: omit right answers when auth is in place
                'right': cq.question.right,
            }

        return {
            'phase': phase.name,
            'active_player_id': game.active_player_id
            if phase != Phase.GAME_OVER
            else None,
            'scores': game.scores,
            'round_name': game.current_round.name if phase != Phase.GAME_OVER else None,
            'board': board,
            'current_question': current_question,
            'current_answerer_id': game.current_answerer_id,
            'connected_players': list(self._players.keys()),
        }

    # ------------------------------------------------------------------
    # Broadcast helpers
    # ------------------------------------------------------------------

    async def _broadcast(self, op: str, data: Any = None) -> None:
        msg = encode(op, data)
        for ws in list(self._players.values()):
            if not ws.closed:
                await ws.send_str(msg)

    async def _broadcast_except(
        self, exclude_id: str, op: str, data: Any = None
    ) -> None:
        msg = encode(op, data)
        for pid, ws in list(self._players.items()):
            if pid != exclude_id and not ws.closed:
                await ws.send_str(msg)

    async def _broadcast_state(self) -> None:
        await self._broadcast(Out.STATE, self._state_data())

    async def _send_error(self, player_id: str, message: str) -> None:
        ws = self._players.get(player_id)
        if ws and not ws.closed:
            await ws.send_str(encode(Out.ERROR, {'message': message}))
