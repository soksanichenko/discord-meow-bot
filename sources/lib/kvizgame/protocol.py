"""WebSocket message protocol for the KvizGame server.

All messages are JSON objects with two fields:
  op  — operation name (string)
  d   — payload (object, may be empty)

Example:
  {"op": "buzz", "d": {}}
  {"op": "state", "d": {"phase": "BUZZER_OPEN", ...}}
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Incoming op codes  (client → server)
# ---------------------------------------------------------------------------


class In:
    """Op codes sent by clients."""

    SELECT = 'select'  # Active player picks a question
    BID = 'bid'  # Active player places an auction bid
    TRANSFER = 'transfer'  # Active player nominates cat recipient
    OPEN_BUZZER = 'open_buzzer'  # Host signals question fully shown
    BUZZ = 'buzz'  # Player presses the buzzer
    JUDGE = 'judge'  # Host judges the answer
    ADVANCE = 'advance'  # Host acknowledges result, moves on
    NEXT_ROUND = 'next_round'  # Host starts the next round
    PAUSE = 'pause'  # Host pauses the game
    RESUME = 'resume'  # Host resumes the game
    CORRECT_SCORES = 'correct_scores'  # Host manually adjusts scores
    PLACE_FINAL_BID = 'place_final_bid'  # Player bids in final round
    SUBMIT_FINAL_ANSWER = 'submit_final_answer'  # Player submits final answer
    START_FINAL_JUDGING = 'start_final_judging'  # Host starts final judging
    JUDGE_FINAL = 'judge_final'  # Host judges a final answer
    REQUEST_APPEAL = 'request_appeal'  # Player appeals host judgment
    RESOLVE_APPEAL = 'resolve_appeal'  # Host accepts or rejects appeal


# ---------------------------------------------------------------------------
# Outgoing op codes  (server → client)
# ---------------------------------------------------------------------------


class Out:
    """Op codes sent by the server."""

    STATE = 'state'  # Full game state snapshot
    ERROR = 'error'  # Action rejected; includes message
    PLAYER_JOINED = 'player_joined'
    PLAYER_LEFT = 'player_left'


# ---------------------------------------------------------------------------
# Encode / decode helpers
# ---------------------------------------------------------------------------


def encode(op: str, data: Any = None) -> str:
    """Serialise an outgoing message to JSON.

    Args:
        op: Outgoing op code.
        data: Payload dict; defaults to empty dict.
    """
    return json.dumps({'op': op, 'd': data if data is not None else {}})


def decode(raw: str) -> tuple[str, dict[str, Any]]:
    """Parse an incoming JSON message.

    Args:
        raw: Raw JSON string from the client.

    Returns:
        (op, data) tuple.

    Raises:
        ValueError: If the message is not valid JSON or missing 'op'.
    """
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid JSON: {exc}') from exc
    if 'op' not in msg:
        raise ValueError("Message missing 'op' field")
    return msg['op'], msg.get('d', {})
