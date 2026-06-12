"""Per-channel game session — manages WebSocket connections and game flow."""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import shutil
import tempfile
import urllib.parse
import zipfile
from typing import Any

from aiohttp import web

from sources.config import config as _config
from sources.lib.kvizgame.game import GameError, GameMachine, Phase
from sources.lib.kvizgame.parser import load as _load_siq
from sources.lib.kvizgame.protocol import In, Out, decode, encode

logger = logging.getLogger(__name__)

_MEDIA_FOLDERS = frozenset({'Images', 'Audio', 'Video'})
_MEDIA_DIR_PREFIX = 'kvizgame_media_'


def cleanup_stale_media_dirs(active_dirs: set[str]) -> None:
    """Remove /tmp/kvizgame_media_* directories not referenced by any active session.

    Call this on server startup (to purge remnants of previous runs) and on
    server shutdown (to purge dirs for sessions that won't be resumed).

    Args:
        active_dirs: Set of media_dir paths currently in use; these are kept.
    """
    tmp = pathlib.Path(tempfile.gettempdir())
    for entry in tmp.iterdir():
        if (
            entry.is_dir()
            and entry.name.startswith(_MEDIA_DIR_PREFIX)
            and str(entry) not in active_dirs
        ):
            shutil.rmtree(entry, ignore_errors=True)
            logger.debug('Removed stale media dir %s', entry)


# How long to wait for any buzz before auto-closing (both window modes).
_BUZZ_AUTO_CLOSE_S = 30.0


class GameSession:
    """Manages one game and all WebSocket connections for a channel.

    Args:
        channel_id: Stable identifier for this session (e.g. Discord channel ID).
        game: Initialised GameMachine ready to play.
        siq_path: Path to the .siq archive.
        host_id: Discord user ID of the host (not a player).
    """

    def __init__(
        self, channel_id: str, game: GameMachine, siq_path: str, host_id: str
    ) -> None:
        if not channel_id.isdigit():
            raise ValueError(f'channel_id must be numeric, got {channel_id!r}')
        self._channel_id = channel_id
        self._game = game
        self._siq_path = siq_path
        self._host_id = host_id
        self._paused: bool = False
        self._appeal_by: str | None = None
        self._media_dir = self._extract_media() if siq_path else ''
        self._players: dict[str, web.WebSocketResponse] = {}
        self._buzz_task: asyncio.Task[None] | None = None

    @property
    def channel_id(self) -> str:
        return self._channel_id

    @property
    def host_id(self) -> str:
        return self._host_id

    @property
    def siq_path(self) -> str:
        return self._siq_path

    @property
    def media_dir(self) -> str:
        return self._media_dir

    @property
    def phase(self) -> Phase:
        return self._game.phase

    @property
    def player_count(self) -> int:
        return len(self._players)

    # ------------------------------------------------------------------
    # Media extraction
    # ------------------------------------------------------------------

    def _extract_media(self) -> str:
        """Extract media folders from the .siq archive into a fresh temp directory.

        Uses metadata_encoding='cp866' so Cyrillic filenames stored without the
        UTF-8 flag (common in Russian SIGame packs) are decoded correctly.
        Entries that carry the UTF-8 flag are unaffected by metadata_encoding.
        """
        dest = tempfile.mkdtemp(prefix='kvizgame_media_')
        with zipfile.ZipFile(self._siq_path, metadata_encoding='cp866') as zf:
            members = [
                m
                for m in zf.namelist()
                if m.split('/')[0] in _MEDIA_FOLDERS and not m.endswith('/')
            ]
            dest_resolved = pathlib.Path(dest).resolve()
            for member in members:
                # Some packs URL-encode filenames inside the ZIP.
                # Decode to get the real Cyrillic name; avoids the 255-byte FS limit.
                decoded = urllib.parse.unquote(member)
                target = (pathlib.Path(dest) / decoded).resolve()
                if not target.is_relative_to(dest_resolved):
                    logger.warning('Skipping zip entry with path traversal: %r', member)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open('wb') as dst:
                    shutil.copyfileobj(src, dst)
        logger.debug(
            'Extracted %d media files for session %r to %s',
            len(members),
            self._channel_id,
            dest,
        )
        return dest

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Write session state to disk; no-op if phase is GAME_OVER."""
        if self._game.phase == Phase.GAME_OVER:
            self.delete_saved()
            return
        sessions_dir = pathlib.Path(_config.kvizgame_sessions_dir)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        data = {
            'channel_id': self._channel_id,
            'siq_path': self._siq_path,
            'host_id': self._host_id,
            'paused': self._paused,
            'appeal_by': self._appeal_by,
            'game': self._game.to_dict(),
        }
        (sessions_dir / f'{self._channel_id}.json').write_text(json.dumps(data))

    def delete_saved(self) -> None:
        """Remove the saved session file and extracted media directory."""
        path = pathlib.Path(_config.kvizgame_sessions_dir) / f'{self._channel_id}.json'
        path.unlink(missing_ok=True)
        if self._media_dir:
            shutil.rmtree(self._media_dir, ignore_errors=True)
            self._media_dir = ''

    @classmethod
    def load(cls, path: pathlib.Path) -> GameSession:
        """Restore a session from a file written by save().

        Args:
            path: Path to the JSON session file.

        Returns:
            Restored GameSession (no WebSocket connections — players reconnect).

        Raises:
            Exception: If the file is missing, corrupt, or the .siq pack is gone.
        """
        data = json.loads(path.read_text())
        siq_path = data['siq_path']
        package = _load_siq(siq_path).package
        game = GameMachine.from_dict(package, data['game'])
        session = cls(data['channel_id'], game, siq_path, data['host_id'])
        session._paused = data.get('paused', False)
        session._appeal_by = data.get('appeal_by')
        return session

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
        appeal_ops = (In.RESOLVE_APPEAL,)
        if self._appeal_by and op not in appeal_ops:
            raise GameError('An appeal is pending')
        if self._paused and op not in (In.RESUME, In.CORRECT_SCORES, *appeal_ops):
            raise GameError('Game is paused')

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
            if player_id != self._host_id:
                raise GameError('Only the host can open the buzzer')
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
            if player_id != self._host_id:
                raise GameError('Only the host can judge answers')
            game.judge_answer(bool(data['correct']))
            await self._broadcast_state()

        elif op == In.ADVANCE:
            if player_id != self._host_id:
                raise GameError('Only the host can advance')

            game.advance()
            await self._broadcast_state()

        elif op == In.NEXT_ROUND:
            game.next_round()
            await self._broadcast_state()

        elif op == In.PLACE_FINAL_BID:
            game.place_final_bid(player_id, int(data['amount']))
            await self._broadcast_state()

        elif op == In.SUBMIT_FINAL_ANSWER:
            game.submit_final_answer(player_id, str(data.get('answer', '')))
            await self._broadcast_state()

        elif op == In.START_FINAL_JUDGING:
            if player_id != self._host_id:
                raise GameError('Only the host can start judging')
            game.start_final_judging()
            await self._broadcast_state()

        elif op == In.JUDGE_FINAL:
            if player_id != self._host_id:
                raise GameError('Only the host can judge final answers')
            game.judge_final_answer(bool(data['correct']))
            await self._broadcast_state()

        elif op == In.CORRECT_SCORES:
            if player_id != self._host_id:
                raise GameError('Only the host can correct scores')
            adjustments: dict[str, int] = {
                str(k): int(v)
                for k, v in data.get('adjustments', {}).items()
                if int(v) != 0
            }
            if adjustments:
                game.correct_scores(adjustments)
                await self._broadcast_state()

        elif op == In.PAUSE:
            if player_id != self._host_id:
                raise GameError('Only the host can pause')
            if not self._paused:
                self._paused = True
                self._cancel_buzz_task()
                await self._broadcast_state()

        elif op == In.RESUME:
            if player_id != self._host_id:
                raise GameError('Only the host can resume')
            if self._paused:
                self._paused = False
                await self._broadcast_state()
                if self._game.phase == Phase.BUZZER_OPEN:
                    await self._schedule_buzz_close()

        elif op == In.REQUEST_APPEAL:
            eligible = game.last_wrong_judged_id
            if player_id == self._host_id:
                raise GameError('Host cannot request an appeal')
            if eligible is None:
                raise GameError('No wrong judgment to appeal')
            if player_id != eligible:
                raise GameError('Only the judged player can appeal')
            if self._appeal_by:
                raise GameError('An appeal is already pending')
            self._appeal_by = player_id
            self._paused = True
            self._cancel_buzz_task()
            await self._broadcast_state()

        elif op == In.RESOLVE_APPEAL:
            if player_id != self._host_id:
                raise GameError('Only the host can resolve an appeal')
            if not self._appeal_by:
                raise GameError('No appeal is pending')
            if bool(data.get('accept')):
                game.accept_appeal()
            self._appeal_by = None
            self._paused = False
            await self._broadcast_state()
            if game.phase == Phase.BUZZER_OPEN:
                await self._schedule_buzz_close()

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

        final_state: dict[str, Any] = {}
        if phase in (Phase.FINAL_BID, Phase.FINAL_QUESTION, Phase.FINAL_JUDGING):
            final_state['final_round_name'] = (
                game._final_round.name if game._final_round else ''
            )
            final_state['final_theme_name'] = game.final_theme_name
            final_state['final_bids_submitted'] = game.final_bids_submitted
            final_state['final_answers_submitted'] = game.final_answers_submitted
            if phase == Phase.FINAL_QUESTION and game.final_question is not None:
                q = game.final_question
                final_state['final_question'] = {
                    'scenario': [
                        {'type': a.type, 'content': a.content, 'time': a.time}
                        for a in q.scenario
                    ],
                    'right': q.right,
                }
            if phase == Phase.FINAL_JUDGING:
                final_state['final_current_judge_id'] = game.final_current_judge_id
                final_state['final_current_answer'] = game.final_current_answer()
                final_state['final_current_bid'] = game.final_current_bid()

        return {
            'phase': phase.name,
            'host_id': self._host_id,
            'paused': self._paused,
            'appeal_by': self._appeal_by,
            'last_judged_id': game.last_wrong_judged_id,
            'player_names': game.player_names,
            'active_player_id': game.active_player_id
            if phase != Phase.GAME_OVER
            else None,
            'scores': game.scores,
            'round_name': game.current_round.name if phase != Phase.GAME_OVER else None,
            'board': board,
            'current_question': current_question,
            'current_answerer_id': game.current_answerer_id,
            'connected_players': list(self._players.keys()),
            **final_state,
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
        self.save()

    async def _send_error(self, player_id: str, message: str) -> None:
        ws = self._players.get(player_id)
        if ws and not ws.closed:
            await ws.send_str(encode(Out.ERROR, {'message': message}))
