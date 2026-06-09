"""Integration tests for the KvizGame WebSocket server."""

import asyncio
import json
import zipfile

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


def _session(channel_id: str = 'ch1', package: Package | None = None) -> GameSession:
    return GameSession(channel_id, _game(package), '')


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
                'channel_id': 'ch1',
                'siq_path': siq_path,
                'player_ids': PLAYERS,
                'player_names': NAMES,
            },
        )
        assert resp.status == 201
        body = await resp.json()
        assert body['channel_id'] == 'ch1'

    async def test_duplicate_channel_returns_409(self, aiohttp_client, tmp_path):
        client: TestClient = await aiohttp_client(create_app())
        siq_path = _make_siq(SIMPLE_SIQ_XML, tmp_path)
        payload = {
            'channel_id': 'ch1',
            'siq_path': siq_path,
            'player_ids': PLAYERS,
            'player_names': NAMES,
        }
        await client.post('/sessions', json=payload)
        resp = await client.post('/sessions', json=payload)
        assert resp.status == 409

    async def test_missing_fields_returns_400(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.post('/sessions', json={'channel_id': 'ch1'})
        assert resp.status == 400

    async def test_invalid_siq_path_returns_422(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.post(
            '/sessions',
            json={
                'channel_id': 'ch1',
                'siq_path': '/nonexistent/file.siq',
                'player_ids': PLAYERS,
                'player_names': NAMES,
            },
        )
        assert resp.status == 422


class TestDeleteSession:
    async def test_delete_existing_returns_204(self, aiohttp_client):
        app = create_app()
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        resp = await client.delete('/sessions/ch1')
        assert resp.status == 204
        assert 'ch1' not in app['sessions']

    async def test_delete_missing_returns_404(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.delete('/sessions/missing')
        assert resp.status == 404


# ---------------------------------------------------------------------------
# WebSocket — connection
# ---------------------------------------------------------------------------


class TestWebSocketConnect:
    async def test_connect_sends_state(self, aiohttp_client):
        app = create_app()
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p1') as ws:
            msg = await _recv_json(ws)
            assert msg['op'] == 'state'
            assert msg['d']['phase'] == 'BOARD'

    async def test_connect_missing_player_id_returns_400(self, aiohttp_client):
        app = create_app()
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        resp = await client.get('/ws/ch1')
        assert resp.status == 400

    async def test_connect_unknown_channel_returns_404(self, aiohttp_client):
        client: TestClient = await aiohttp_client(create_app())
        resp = await client.get('/ws/missing?player_id=p1')
        assert resp.status == 404

    async def test_second_player_receives_joined_notification(self, aiohttp_client):
        app = create_app()
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p1') as ws1:
            await _recv_json(ws1)  # consume initial state
            async with client.ws_connect('/ws/ch1?player_id=p2') as ws2:
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
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p1') as ws:
            await _recv_json(ws)  # initial state
            await ws.send_str(
                json.dumps({'op': 'select', 'd': {'theme_idx': 0, 'question_idx': 0}})
            )
            state = await _drain_until(ws, 'state')
            assert state['d']['phase'] == 'QUESTION'

    async def test_wrong_player_select_returns_error(self, aiohttp_client):
        app = create_app()
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p2') as ws:
            await _recv_json(ws)
            await ws.send_str(
                json.dumps({'op': 'select', 'd': {'theme_idx': 0, 'question_idx': 0}})
            )
            err = await _drain_until(ws, 'error')
            assert 'not the active player' in err['d']['message']

    async def test_invalid_json_returns_error(self, aiohttp_client):
        app = create_app()
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str('not json')
            err = await _drain_until(ws, 'error')
            assert 'JSON' in err['d']['message']

    async def test_unknown_op_returns_error(self, aiohttp_client):
        app = create_app()
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p1') as ws:
            await _recv_json(ws)
            await ws.send_str(json.dumps({'op': 'fly_to_moon', 'd': {}}))
            err = await _drain_until(ws, 'error')
            assert 'Unknown op' in err['d']['message']

    async def test_full_question_flow_strict_buzz(self, aiohttp_client):
        """p1 selects → opens buzzer → p1 buzzes first → wins → correct answer."""
        app = create_app()
        app['sessions']['ch1'] = _session('ch1')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p1') as ws:
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
        app['sessions']['ch1'] = GameSession('ch1', game, '')
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p1') as ws:
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
        app['sessions']['ch1'] = _session('ch1', _package_one_question())
        client: TestClient = await aiohttp_client(app)
        async with client.ws_connect('/ws/ch1?player_id=p1') as ws:
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
