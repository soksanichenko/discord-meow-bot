"""aiohttp WebSocket server for KvizGame sessions."""

from __future__ import annotations

import logging

from aiohttp import WSMsgType, web

from sources.lib.kvizgame.game import GameMachine, Settings
from sources.lib.kvizgame.parser import load
from sources.lib.kvizgame.session import GameSession

logger = logging.getLogger(__name__)


async def _ws_handler(request: web.Request) -> web.WebSocketResponse:
    """WebSocket endpoint: /ws/{channel_id}?player_id=xxx"""
    channel_id = request.match_info['channel_id']
    player_id = request.rel_url.query.get('player_id', '').strip()

    if not player_id:
        raise web.HTTPBadRequest(reason='player_id query parameter is required')

    sessions: dict[str, GameSession] = request.app['sessions']
    session = sessions.get(channel_id)
    if session is None:
        raise web.HTTPNotFound(reason=f'No session for channel {channel_id!r}')

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    await session.connect(player_id, ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                await session.handle(player_id, msg.data)
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        await session.disconnect(player_id)

    return ws


async def _create_session(request: web.Request) -> web.Response:
    """POST /sessions — create a game session.

    Expected JSON body:
      channel_id    str           Unique channel identifier
      siq_path      str           Path to the .siq file on the server
      player_ids    list[str]     Ordered list of player IDs
      player_names  dict[str,str] player_id → display name
      buzz_window_ms int          Optional; default 0
    """
    body = await request.json()

    required = {'channel_id', 'siq_path', 'player_ids', 'player_names'}
    missing = required - body.keys()
    if missing:
        raise web.HTTPBadRequest(reason=f'Missing fields: {", ".join(sorted(missing))}')

    channel_id: str = body['channel_id']
    sessions: dict[str, GameSession] = request.app['sessions']
    if channel_id in sessions:
        raise web.HTTPConflict(reason=f'Session {channel_id!r} already exists')

    try:
        package = load(body['siq_path']).package
    except Exception as exc:
        raise web.HTTPUnprocessableEntity(reason=f'Failed to load .siq: {exc}') from exc

    settings = Settings(buzz_window_ms=int(body.get('buzz_window_ms', 0)))
    try:
        game = GameMachine(package, body['player_ids'], body['player_names'], settings)
    except (ValueError, KeyError) as exc:
        raise web.HTTPUnprocessableEntity(reason=str(exc)) from exc

    sessions[channel_id] = GameSession(channel_id, game)
    logger.info('Session created for channel %r', channel_id)
    return web.json_response({'channel_id': channel_id}, status=201)


async def _delete_session(request: web.Request) -> web.Response:
    """DELETE /sessions/{channel_id} — remove a session."""
    channel_id = request.match_info['channel_id']
    sessions: dict[str, GameSession] = request.app['sessions']
    if sessions.pop(channel_id, None) is None:
        raise web.HTTPNotFound(reason=f'No session for channel {channel_id!r}')
    logger.info('Session removed for channel %r', channel_id)
    return web.Response(status=204)


def create_app() -> web.Application:
    """Create and return the aiohttp application.

    The session registry lives in app['sessions'] as a plain dict so
    tests can inspect or pre-populate it directly.
    """
    app = web.Application()
    app['sessions'] = {}
    app.router.add_post('/sessions', _create_session)
    app.router.add_delete('/sessions/{channel_id}', _delete_session)
    app.router.add_get('/ws/{channel_id}', _ws_handler)
    return app
