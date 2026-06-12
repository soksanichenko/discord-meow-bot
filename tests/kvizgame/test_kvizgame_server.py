"""Integration tests for the KvizGame WebSocket server."""

import asyncio
import io
import json
import struct
import zipfile
import zlib

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient

from sources.lib.kvizgame.game import GameMachine, Settings
from sources.lib.kvizgame.parser import (
    Atom,
    Package,
    Question,
    Round,
    Theme,
)
from sources.lib.kvizgame.server import create_app
from sources.lib.kvizgame.session import GameSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PLAYERS = ['p1', 'p2']
NAMES = {'p1': 'Alice', 'p2': 'Bob'}


def _question(price: int = 100, q_type: str = 'simple') -> Question:
    return Question(
        price=price,
        q_type=q_type,
        scenario=[Atom(type='text', content='Q text')],
        right=['Answer'],
    )


def _package_one_question() -> Package:
    return Package(
        name='Test',
        rounds=[
            Round(name='R1', themes=[Theme(name='Science', questions=[_question(100)])])
        ],
    )


def _package_two_questions() -> Package:
    return Package(
        name='Test',
        rounds=[
            Round(
                name='R1',
                themes=[
                    Theme(name='Science', questions=[_question(100), _question(200)])
                ],
            )
        ],
    )


def _make_siq(package_xml: str, tmp_path) -> str:
    path = tmp_path / 'test.siq'
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('content.xml', package_xml)
    return str(path)


def _make_siq_with_media(
    package_xml: str, tmp_path, media_files: dict[str, bytes]
) -> str:
    path = tmp_path / 'test.siq'
    with zipfile.ZipFile(path, 'w') as zf:
        zf.writestr('content.xml', package_xml)
        for name, data in media_files.items():
            zf.writestr(name, data)
    return str(path)


def _make_cp866_siq(package_xml: str, tmp_path, media_files: dict[str, bytes]) -> str:
    """Write a .siq where media filenames are raw CP866 bytes (no UTF-8 flag).

    Python's zipfile always sets the UTF-8 flag for non-ASCII names, so we
    build the ZIP structure manually to mimic what SIGame on Russian Windows
    actually produces.
    """
    buf = io.BytesIO()
    entries: list[tuple[bytes, int, int, int]] = []

    all_files: dict[str, bytes] = {
        'content.xml': package_xml.encode('utf-8'),
        **media_files,
    }

    for name, data in all_files.items():
        crc = zlib.crc32(data) & 0xFFFFFFFF
        name_bytes = name.encode('cp866')
        local_offset = buf.tell()
        buf.write(
            struct.pack(
                '<4sHHHHHIIIHH',
                b'PK\x03\x04',
                20,
                0,
                0,
                0,
                0,
                crc,
                len(data),
                len(data),
                len(name_bytes),
                0,
            )
        )
        buf.write(name_bytes)
        buf.write(data)
        entries.append((name_bytes, crc, len(data), local_offset))

    cd_offset = buf.tell()
    for name_bytes, crc, size, offset in entries:
        buf.write(
            struct.pack(
                '<4sHHHHHHIIIHHHHHII',
                b'PK\x01\x02',
                20,
                20,
                0,
                0,
                0,
                0,
                crc,
                size,
                size,
                len(name_bytes),
                0,
                0,
                0,
                0,
                0,
                offset,
            )
        )
        buf.write(name_bytes)

    cd_size = buf.tell() - cd_offset
    buf.write(
        struct.pack(
            '<4sHHHHIIH',
            b'PK\x05\x06',
            0,
            0,
            len(entries),
            len(entries),
            cd_size,
            cd_offset,
            0,
        )
    )

    path = tmp_path / 'cp866.siq'
    path.write_bytes(buf.getvalue())
    return str(path)


SIMPLE_SIQ_XML = """<?xml version="1.0"?>
<package name="Test" version="4" difficulty="5">
  <rounds>
    <round name="R1">
      <themes>
        <theme name="Science">
          <questions>
            <question price="100">
              <scenario><atom>Question text</atom></scenario>
              <right><answer>Answer</answer></right>
            </question>
            <question price="200">
              <scenario><atom>Question 2</atom></scenario>
              <right><answer>Answer 2</answer></right>
            </question>
          </questions>
        </theme>
      </themes>
    </round>
  </rounds>
</package>
"""


def _game(package: Package | None = None) -> GameMachine:
    return GameMachine(
        package or _package_two_questions(),
        PLAYERS,
        NAMES,
        Settings(buzz_window_ms=0),
    )


def _session(
    channel_id: str = '123456789012345678', package: Package | None = None
) -> GameSession:
    return GameSession(channel_id, _game(package), '', 'p1')


async def _recv_json(ws) -> dict:
    msg = await ws.receive()
    assert msg.type == WSMsgType.TEXT
    return json.loads(msg.data)


async def _drain_until(ws, op: str, max_msgs: int = 10) -> dict:
    """Read messages until one with the given op is found."""
    for _ in range(max_msgs):
        msg = await _recv_json(ws)
        if msg['op'] == op:
            return msg
    raise AssertionError(f'op {op!r} not received in {max_msgs} messages')


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


class TestCreateSession:
    async def test_create_returns_201(self, aiohttp_client, tmp_path):
        client: TestClient = await aiohttp_client(create_app())
        siq_path = _make_siq(SIMPLE_SIQ_XML, tmp_path)
        resp = await client.post(
            '/sessions',
            json={
                'channel_id': '123456789012345678',
                'siq_path': siq_path,
                'player_ids': PLAYERS,
                'player_names': NAMES,
                'host_id': 'host1',
            },
        )
        assert resp.status == 201
        body = await resp.json()
        assert body['channel_id'] == '123456789012345678'

    async def test_duplicate_channel_returns_409(self, aiohttp_client, tmp_path):
        client: TestClient = await aiohttp_client(create_app())
        siq_path = _make_siq(SIMPLE_SIQ_XML, tmp_path)
        payload = {
            'channel_id': '123456789012345678',
            'siq_path': siq_path,
            'player_ids': PLAYERS,
            'player_names': NAMES,
            'host_id': 'host1',
        }
        await client.post('/sessions', json=payload)
        resp = await client.post('/sessions', json=payload)
        assert resp.status == 409

    async def test_missing_fields_returns_400(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.post('/sessions', json={'channel_id': '123456789012345678'})
        assert resp.status == 400

    async def test_invalid_siq_path_returns_422(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.post(
            '/sessions',
            json={
                'channel_id': '123456789012345678',
                'siq_path': '/nonexistent/file.siq',
                'player_ids': PLAYERS,
                'player_names': NAMES,
                'host_id': 'host1',
            },
        )
        assert resp.status == 422


class TestDeleteSession:
    async def test_delete_existing_returns_204(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        resp = await client.delete('/sessions/123456789012345678')
        assert resp.status == 204
        assert '123456789012345678' not in app['sessions']

    async def test_delete_missing_returns_404(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.delete('/sessions/missing')
        assert resp.status == 404


# ---------------------------------------------------------------------------
# WebSocket — connection
# ---------------------------------------------------------------------------


class TestMediaEndpoint:
    _PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8  # minimal PNG header bytes

    async def test_serves_image(self, aiohttp_client, tmp_path):
        siq_path = _make_siq_with_media(
            SIMPLE_SIQ_XML, tmp_path, {'Images/pic.png': self._PNG}
        )
        app = create_app()
        app['sessions']['123456789012345678'] = GameSession(
            '123456789012345678', _game(), siq_path, 'host1'
        )
        client: TestClient = await aiohttp_client(app)
        resp = await client.get('/media/123456789012345678/Images/pic.png')
        assert resp.status == 200
        assert resp.content_type == 'image/png'
        assert await resp.read() == self._PNG

    async def test_cyrillic_filename_cp866(self, aiohttp_client, tmp_path):
        """Files stored with CP866 names (common in Russian SIGame packs) must be found."""
        siq_path = _make_cp866_siq(
            SIMPLE_SIQ_XML, tmp_path, {'Images/Владимир Путин.jpg': self._PNG}
        )
        app = create_app()
        app['sessions']['123456789012345678'] = GameSession(
            '123456789012345678', _game(), siq_path, 'host1'
        )
        client: TestClient = await aiohttp_client(app)
        resp = await client.get(
            '/media/123456789012345678/Images/%D0%92%D0%BB%D0%B0%D0%B4%D0%B8%D0%BC%D0%B8%D1%80%20%D0%9F%D1%83%D1%82%D0%B8%D0%BD.jpg'
        )
        assert resp.status == 200
        assert await resp.read() == self._PNG

    async def test_at_prefix_filename(self, aiohttp_client, tmp_path):
        """Frontend strips @ before building the URL; bare filename must still work."""
        siq_path = _make_siq_with_media(
            SIMPLE_SIQ_XML, tmp_path, {'Audio/track.mp3': b'ID3data'}
        )
        app = create_app()
        app['sessions']['123456789012345678'] = GameSession(
            '123456789012345678', _game(), siq_path, 'host1'
        )
        client: TestClient = await aiohttp_client(app)
        resp = await client.get('/media/123456789012345678/Audio/track.mp3')
        assert resp.status == 200
        assert resp.content_type == 'audio/mpeg'

    async def test_missing_session_returns_404(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.get('/media/no_such/Images/pic.png')
        assert resp.status == 404

    async def test_missing_file_in_archive_returns_404(self, aiohttp_client, tmp_path):
        siq_path = _make_siq(SIMPLE_SIQ_XML, tmp_path)
        app = create_app()
        app['sessions']['123456789012345678'] = GameSession(
            '123456789012345678', _game(), siq_path, 'host1'
        )
        client: TestClient = await aiohttp_client(app)
        resp = await client.get('/media/123456789012345678/Images/nonexistent.png')
        assert resp.status == 404

    async def test_invalid_folder_returns_403(self, aiohttp_client, tmp_path):
        siq_path = _make_siq(SIMPLE_SIQ_XML, tmp_path)
        app = create_app()
        app['sessions']['123456789012345678'] = GameSession(
            '123456789012345678', _game(), siq_path, 'host1'
        )
        client: TestClient = await aiohttp_client(app)
        resp = await client.get('/media/123456789012345678/../secret.txt')
        assert resp.status in (403, 404)  # aiohttp may normalise the path


class TestWebSocketConnect:
    async def test_connect_sends_state(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            msg = await _recv_json(ws)
            assert msg['op'] == 'state'
            assert msg['d']['phase'] == 'BOARD'

    async def test_connect_missing_player_id_returns_400(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        resp = await client.get('/ws/123456789012345678')
        assert resp.status == 400

    async def test_connect_unknown_channel_returns_404(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.get('/ws/missing?player_id=p1')
        assert resp.status == 404

    async def test_second_player_receives_joined_notification(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws1:
            await _recv_json(ws1)  # consume initial state
            async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws2:
                await _recv_json(ws2)  # consume state for p2
                # p1 should have received player_joined for p2
                joined = await _drain_until(ws1, 'player_joined')
                assert joined['d']['player_id'] == 'p2'


# ---------------------------------------------------------------------------
# WebSocket — game flow
# ---------------------------------------------------------------------------


class TestGameFlow:
    async def test_select_question_broadcasts_state(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)  # initial state
            await ws.send_str(
                json.dumps({'op': 'select', 'd': {'theme_idx': 0, 'question_idx': 0}})
            )
            state = await _drain_until(ws, 'state')
            assert state['d']['phase'] == 'QUESTION'

    async def test_wrong_player_select_returns_error(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(
                json.dumps({'op': 'select', 'd': {'theme_idx': 0, 'question_idx': 0}})
            )
            err = await _drain_until(ws, 'error')
            assert 'not the active player' in err['d']['message']

    async def test_invalid_json_returns_error(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str('not json')
            err = await _drain_until(ws, 'error')
            assert 'JSON' in err['d']['message']

    async def test_unknown_op_returns_error(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'fly_to_moon', 'd': {}}))
            err = await _drain_until(ws, 'error')
            assert 'Unknown op' in err['d']['message']

    async def test_full_question_flow_strict_buzz(self, aiohttp_client):
        """p1 selects → opens buzzer → p1 buzzes first → wins → correct answer."""
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)

            await ws.send_str(
                json.dumps({'op': 'select', 'd': {'theme_idx': 0, 'question_idx': 0}})
            )
            await _drain_until(ws, 'state')  # QUESTION

            await ws.send_str(json.dumps({'op': 'open_buzzer', 'd': {}}))
            state = await _drain_until(ws, 'state')
            assert state['d']['phase'] == 'BUZZER_OPEN'

            await ws.send_str(json.dumps({'op': 'buzz', 'd': {}}))
            state = await _drain_until(ws, 'state')
            assert state['d']['phase'] == 'ANSWERING'
            assert state['d']['current_answerer_id'] == 'p1'

            await ws.send_str(json.dumps({'op': 'judge', 'd': {'correct': True}}))
            state = await _drain_until(ws, 'state')
            assert state['d']['phase'] == 'ANSWER_RESULT'
            assert state['d']['scores']['p1'] == 100

    async def test_buzz_window_auto_closes_after_delay(self, aiohttp_client):
        """With buzz_window_ms > 0, buzzer closes after the window even without buzz."""
        app = create_app()
        game = GameMachine(
            _package_one_question(),
            PLAYERS,
            NAMES,
            Settings(buzz_window_ms=50),  # 50 ms window
        )
        app['sessions']['123456789012345678'] = GameSession(
            '123456789012345678', game, '', 'p1'
        )
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(
                json.dumps({'op': 'select', 'd': {'theme_idx': 0, 'question_idx': 0}})
            )
            await _drain_until(ws, 'state')
            await ws.send_str(json.dumps({'op': 'open_buzzer', 'd': {}}))
            await _drain_until(ws, 'state')  # BUZZER_OPEN

            # Wait for auto-close (window + margin)
            await asyncio.sleep(0.15)
            state = await _drain_until(ws, 'state')
            # No buzzes → goes to ANSWER_RESULT
            assert state['d']['phase'] == 'ANSWER_RESULT'

    async def test_round_end_to_game_over(self, aiohttp_client):
        """Single-question round: after answering, advance → ROUND_END → GAME_OVER."""
        app = create_app()
        app['sessions']['123456789012345678'] = _session(
            '123456789012345678', _package_one_question()
        )
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)

            await ws.send_str(
                json.dumps({'op': 'select', 'd': {'theme_idx': 0, 'question_idx': 0}})
            )
            await _drain_until(ws, 'state')
            await ws.send_str(json.dumps({'op': 'open_buzzer', 'd': {}}))
            await _drain_until(ws, 'state')
            await ws.send_str(json.dumps({'op': 'buzz', 'd': {}}))
            await _drain_until(ws, 'state')
            await ws.send_str(json.dumps({'op': 'judge', 'd': {'correct': True}}))
            await _drain_until(ws, 'state')  # ANSWER_RESULT

            await ws.send_str(json.dumps({'op': 'advance', 'd': {}}))
            state = await _drain_until(ws, 'state')
            assert state['d']['phase'] == 'ROUND_END'

            await ws.send_str(json.dumps({'op': 'next_round', 'd': {}}))
            state = await _drain_until(ws, 'state')
            assert state['d']['phase'] == 'GAME_OVER'

    async def test_state_includes_host_id_and_paused(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            state = await _recv_json(ws)
            assert state['d']['host_id'] == 'p1'
            assert state['d']['paused'] is False
            assert state['d']['appeal_by'] is None

    async def test_non_host_cannot_open_buzzer(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        app['sessions']['123456789012345678']._game.select_question('p1', 0, 0)
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'open_buzzer', 'd': {}}))
            err = await _drain_until(ws, 'error')
            assert 'host' in err['d']['message'].lower()

    async def test_non_host_cannot_judge(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        # Drive to ANSWERING: select → open → buzz → close
        g = app['sessions']['123456789012345678']._game
        g.select_question('p1', 0, 0)
        g.open_buzzer()
        g.buzz('p1')
        g.close_buzzer()
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'judge', 'd': {'correct': True}}))
            err = await _drain_until(ws, 'error')
            assert 'host' in err['d']['message'].lower()

    async def test_non_host_cannot_advance(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        g = app['sessions']['123456789012345678']._game
        g.select_question('p1', 0, 0)
        g.open_buzzer()
        g.buzz('p1')
        g.close_buzzer()
        g.judge_answer(True)
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'advance', 'd': {}}))
            err = await _drain_until(ws, 'error')
            assert 'host' in err['d']['message'].lower()


# ---------------------------------------------------------------------------
# Pause / Resume
# ---------------------------------------------------------------------------


class TestPauseResume:
    async def test_host_can_pause(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'pause', 'd': {}}))
            state = await _drain_until(ws, 'state')
            assert state['d']['paused'] is True

    async def test_non_host_cannot_pause(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'pause', 'd': {}}))
            err = await _drain_until(ws, 'error')
            assert 'host' in err['d']['message'].lower()

    async def test_ops_blocked_while_paused(self, aiohttp_client):
        app = create_app()
        session = _session('123456789012345678')
        session._paused = True
        app['sessions']['123456789012345678'] = session
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(
                json.dumps({'op': 'select', 'd': {'theme_idx': 0, 'question_idx': 0}})
            )
            err = await _drain_until(ws, 'error')
            assert 'paused' in err['d']['message'].lower()

    async def test_host_can_resume(self, aiohttp_client):
        app = create_app()
        session = _session('123456789012345678')
        session._paused = True
        app['sessions']['123456789012345678'] = session
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'resume', 'd': {}}))
            state = await _drain_until(ws, 'state')
            assert state['d']['paused'] is False


# ---------------------------------------------------------------------------
# Appeal
# ---------------------------------------------------------------------------


def _session_with_wrong_answer(
    player_id: str = 'p2', host_id: str = 'p1'
) -> GameSession:
    """Session where *player_id* just answered an auction question wrong.

    Auction questions have a fixed answerer, so a wrong answer goes directly
    to ANSWER_RESULT without reopening the buzzer, making last_wrong_judged_id
    equal to player_id even with 2 players.
    """
    q = Question(
        price=100,
        q_type='auction',
        type_params={},
        scenario=[Atom(type='text', content='Q')],
        right=['Answer'],
        wrong=[],
    )
    pkg = Package(
        name='Test', rounds=[Round(name='R1', themes=[Theme(name='T', questions=[q])])]
    )
    # player_id must be first (active player) so they can place the auction bid.
    other = 'p_other'
    game = GameMachine(
        pkg,
        [player_id, other],
        {player_id: 'Bob', other: 'Other'},
        Settings(buzz_window_ms=0),
    )
    game.select_question(player_id, 0, 0)  # → AUCTION_BIDDING
    game.place_bid(player_id, 100)  # → QUESTION
    game.open_buzzer()  # → ANSWERING (player_id fixed)
    game.judge_answer(False)  # fixed answerer → ANSWER_RESULT
    return GameSession('123456789012345678', game, '', host_id)


class TestAppeal:
    async def test_eligible_player_can_request_appeal(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session_with_wrong_answer()
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'request_appeal', 'd': {}}))
            state = await _drain_until(ws, 'state')
            assert state['d']['appeal_by'] == 'p2'
            assert state['d']['paused'] is True

    async def test_host_cannot_request_appeal(self, aiohttp_client):
        app = create_app()
        app['sessions']['123456789012345678'] = _session_with_wrong_answer()
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'request_appeal', 'd': {}}))
            err = await _drain_until(ws, 'error')
            assert 'host' in err['d']['message'].lower()

    async def test_no_wrong_judgment_cannot_appeal(self, aiohttp_client):
        """Correct judgment → last_wrong_judged_id is None → appeal rejected."""
        app = create_app()
        # Use _session() (2-player, p1 active); p1 answers correctly → no wrong judgment
        app['sessions']['123456789012345678'] = _session('123456789012345678')
        g = app['sessions']['123456789012345678']._game
        g.select_question('p1', 0, 0)
        g.open_buzzer()
        g.buzz('p1')
        g.close_buzzer()
        g.judge_answer(True)  # correct → last_wrong_judged_id is None
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'request_appeal', 'd': {}}))
            err = await _drain_until(ws, 'error')
            assert 'judgment' in err['d']['message'].lower()

    async def test_resolve_appeal_accept_corrects_score(self, aiohttp_client):
        app = create_app()
        session = _session_with_wrong_answer()
        session._appeal_by = 'p2'
        session._paused = True
        app['sessions']['123456789012345678'] = session
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(
                json.dumps({'op': 'resolve_appeal', 'd': {'accept': True}})
            )
            state = await _drain_until(ws, 'state')
            # wrong: -100; accept: +200 → net 100
            assert state['d']['scores']['p2'] == 100
            assert state['d']['appeal_by'] is None
            assert state['d']['paused'] is False

    async def test_resolve_appeal_reject_leaves_score_unchanged(self, aiohttp_client):
        app = create_app()
        session = _session_with_wrong_answer()
        session._appeal_by = 'p2'
        session._paused = True
        app['sessions']['123456789012345678'] = session
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(
                json.dumps({'op': 'resolve_appeal', 'd': {'accept': False}})
            )
            state = await _drain_until(ws, 'state')
            assert state['d']['scores']['p2'] == -100
            assert state['d']['appeal_by'] is None
            assert state['d']['paused'] is False

    async def test_non_host_cannot_resolve_appeal(self, aiohttp_client):
        app = create_app()
        session = _session('123456789012345678')
        session._appeal_by = 'p2'
        session._paused = True
        app['sessions']['123456789012345678'] = session
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(
                json.dumps({'op': 'resolve_appeal', 'd': {'accept': True}})
            )
            err = await _drain_until(ws, 'error')
            assert 'host' in err['d']['message'].lower()

    async def test_ops_blocked_while_appeal_pending(self, aiohttp_client):
        app = create_app()
        session = _session('123456789012345678')
        session._appeal_by = 'p2'
        session._paused = True
        app['sessions']['123456789012345678'] = session
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/123456789012345678?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'resume', 'd': {}}))
            err = await _drain_until(ws, 'error')
            assert 'appeal' in err['d']['message'].lower()
