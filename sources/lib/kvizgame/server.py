"""aiohttp WebSocket server for KvizGame sessions."""

from __future__ import annotations

import logging
import pathlib
import urllib.parse

import aiohttp
from aiohttp import WSMsgType, web

from sources.config import config
from sources.lib.kvizgame.game import GameMachine, Settings
from sources.lib.kvizgame.parser import load
from sources.lib.kvizgame.session import GameSession, cleanup_stale_media_dirs

logger = logging.getLogger(__name__)

_VALID_MEDIA_FOLDERS = frozenset({'Images', 'Audio', 'Video'})


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


async def _token_handler(request: web.Request) -> web.Response:
    """POST /token — exchange Discord OAuth2 code for access token.

    Expected JSON body:
      code  str  Authorization code from the Discord SDK authorize() call.
    """
    body = await request.json()
    code = body.get('code', '').strip()
    if not code:
        raise web.HTTPBadRequest(reason='Missing code')

    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            'https://discord.com/api/oauth2/token',
            data={
                'client_id': config.discord_client_id,
                'client_secret': config.discord_client_secret,
                'grant_type': 'authorization_code',
                'code': code,
            },
        )
        if resp.status != 200:
            text = await resp.text()
            logger.warning('Discord token exchange failed (%d): %s', resp.status, text)
            raise web.HTTPBadGateway(reason='Discord token exchange failed')
        data = await resp.json()

    return web.json_response({'access_token': data['access_token']})


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

    required = {'channel_id', 'siq_path', 'player_ids', 'player_names', 'host_id'}
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

    session = GameSession(channel_id, game, body['siq_path'], body['host_id'])
    session.save()
    sessions[channel_id] = session
    logger.info('Session created for channel %r', channel_id)
    return web.json_response({'channel_id': channel_id}, status=201)


async def _media_handler(request: web.Request) -> web.Response:
    """GET /media/{channel_id}/{media_path} — serve a pre-extracted media file.

    media_path must start with Images/, Audio/, or Video/.
    Files are extracted from the .siq archive at session creation time and
    served directly from the filesystem, supporting Range requests for video.
    """
    channel_id = request.match_info['channel_id']
    media_path = request.match_info['media_path']

    folder = media_path.split('/')[0] if '/' in media_path else ''
    if folder not in _VALID_MEDIA_FOLDERS:
        raise web.HTTPForbidden(reason='Invalid media path')

    sessions: dict[str, GameSession] = request.app['sessions']
    session = sessions.get(channel_id)
    if session is None:
        raise web.HTTPNotFound(reason=f'No session for channel {channel_id!r}')

    media_dir_resolved = pathlib.Path(session.media_dir).resolve()
    file_path = (media_dir_resolved / urllib.parse.unquote(media_path)).resolve()
    if not file_path.is_relative_to(media_dir_resolved):
        raise web.HTTPForbidden(reason='Invalid media path')
    if not file_path.is_file():
        raise web.HTTPNotFound(reason=f'Media file {media_path!r} not found')

    return web.FileResponse(file_path)


async def _delete_session(request: web.Request) -> web.Response:
    """DELETE /sessions/{channel_id} — remove a session."""
    channel_id = request.match_info['channel_id']
    sessions: dict[str, GameSession] = request.app['sessions']
    session = sessions.pop(channel_id, None)
    if session is None:
        raise web.HTTPNotFound(reason=f'No session for channel {channel_id!r}')
    session.delete_saved()
    logger.info('Session removed for channel %r', channel_id)
    return web.Response(status=204)


async def _on_startup(app: web.Application) -> None:
    active = {s.media_dir for s in app['sessions'].values() if s.media_dir}
    cleanup_stale_media_dirs(active)


async def _on_cleanup(app: web.Application) -> None:
    # Media dirs for surviving sessions are cleaned now; load() will re-extract
    # them if the server restarts with saved sessions still on disk.
    active = {s.media_dir for s in app['sessions'].values() if s.media_dir}
    cleanup_stale_media_dirs(active)


def create_app(sessions: dict | None = None) -> web.Application:
    """Create and return the aiohttp application.

    The session registry lives in app['sessions'] as a plain dict so
    tests can inspect or pre-populate it directly.

    Args:
        sessions: Optional external sessions dict to share with the Discord cog.
                  If None, a new empty dict is created.
    """
    app = web.Application()
    app['sessions'] = sessions if sessions is not None else {}
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_post('/token', _token_handler)
    app.router.add_post('/sessions', _create_session)
    app.router.add_delete('/sessions/{channel_id}', _delete_session)
    app.router.add_get('/media/{channel_id}/{media_path:.*}', _media_handler)
    app.router.add_get('/ws/{channel_id}', _ws_handler)
    if config.kvizgame_frontend_dir:
        _frontend = pathlib.Path(config.kvizgame_frontend_dir)

        async def _index(request: web.Request) -> web.FileResponse:
            return web.FileResponse(_frontend / 'index.html')

        app.router.add_get('/', _index)
        app.router.add_static('/assets', _frontend / 'assets')
    return app
