"""Contract tests for the WS v1 protocol.

These roundtrip every example from docs/protocol.md through the pydantic
schemas. If this file starts failing, the protocol was broken and you
need a v2 instead of editing in place.
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from electronbot_es.core.messages import (
    ClientMessage,
    ErrorMessage,
    MetricsTurn,
    ServerMessage,
    SessionStart,
    TtsCancel,
)


client_adapter = TypeAdapter(ClientMessage)
server_adapter = TypeAdapter(ServerMessage)


# ---------- client → server ----------

def test_session_start_full():
    raw = {
        "type": "session.start",
        "protocol_version": "1.0",
        "device_id": "mock-esp32-001",
        "user_id": None,
        "auth_token": None,
        "locale": "es-419",
        "audio_in": {"sample_rate": 16000, "encoding": "pcm_s16le", "channels": 1},
        "audio_out": {"sample_rate": 16000, "encoding": "pcm_s16le", "channels": 1},
        "capabilities": {
            "ack_filler_local": True,
            "barge_in": True,
            "display": False,
            "servos": False,
        },
    }
    msg = client_adapter.validate_python(raw)
    assert isinstance(msg, SessionStart)
    assert msg.locale == "es-419"
    assert msg.capabilities.ack_filler_local is True


def test_session_start_minimal():
    msg = client_adapter.validate_python(
        {"type": "session.start", "device_id": "dev1"}
    )
    assert isinstance(msg, SessionStart)
    assert msg.user_id is None
    assert msg.audio_in.sample_rate == 16000


def test_audio_start_end_roundtrip():
    start = client_adapter.validate_python(
        {"type": "audio.start", "turn_id": "t1", "timestamp_ms": 1712345678000}
    )
    end = client_adapter.validate_python({"type": "audio.end", "turn_id": "t1"})
    assert start.turn_id == end.turn_id == "t1"


def test_tts_cancel_defaults_to_barge_in():
    msg = client_adapter.validate_python({"type": "tts.cancel", "turn_id": "t1"})
    assert isinstance(msg, TtsCancel)
    assert msg.reason == "barge_in"


def test_tts_cancel_rejects_unknown_reason():
    with pytest.raises(ValidationError):
        client_adapter.validate_python(
            {"type": "tts.cancel", "turn_id": "t1", "reason": "bogus"}
        )


def test_client_discriminator_rejects_unknown_type():
    with pytest.raises(ValidationError):
        client_adapter.validate_python({"type": "audio.garbage", "turn_id": "t1"})


# ---------- server → client ----------

def test_session_ready():
    msg = server_adapter.validate_python(
        {
            "type": "session.ready",
            "session_id": "s1",
            "protocol_version": "1.0",
            "server_version": "0.1.0",
        }
    )
    assert msg.server_version == "0.1.0"


def test_stt_partial_and_final():
    p = server_adapter.validate_python(
        {"type": "stt.partial", "turn_id": "t1", "text": "hola cómo"}
    )
    f = server_adapter.validate_python(
        {
            "type": "stt.final",
            "turn_id": "t1",
            "text": "hola cómo estás",
            "confidence": 0.93,
            "language": "es",
        }
    )
    assert p.text == "hola cómo"
    assert f.confidence == 0.93


def test_llm_status_states():
    for state in ("thinking", "generating", "done"):
        msg = server_adapter.validate_python(
            {"type": "llm.status", "turn_id": "t1", "state": state}
        )
        assert msg.state == state


def test_llm_status_rejects_unknown_state():
    with pytest.raises(ValidationError):
        server_adapter.validate_python(
            {"type": "llm.status", "turn_id": "t1", "state": "sleeping"}
        )


def test_tts_start_and_end():
    s = server_adapter.validate_python(
        {
            "type": "tts.start",
            "turn_id": "t1",
            "sample_rate": 16000,
            "encoding": "pcm_s16le",
            "channels": 1,
            "tier": "T3",
            "source": "cartesia",
        }
    )
    e = server_adapter.validate_python({"type": "tts.end", "turn_id": "t1"})
    assert s.tier == "T3"
    assert e.turn_id == "t1"


def test_metrics_turn_full():
    msg = server_adapter.validate_python(
        {
            "type": "metrics.turn",
            "turn_id": "t1",
            "tier": "T3",
            "latencies_ms": {
                "stt_final": 420,
                "llm_first_token": 280,
                "tts_first_chunk": 150,
                "wake_to_first_audio": 870,
            },
            "tokens": {"in": 42, "out": 68},
            "cost_usd": 0.0012,
            "providers": {"stt": "deepgram", "llm": "groq", "tts": "cartesia"},
        }
    )
    assert isinstance(msg, MetricsTurn)
    assert msg.latencies_ms.wake_to_first_audio == 870
    assert msg.tokens.in_ == 42
    assert msg.providers.llm == "groq"


def test_metrics_turn_minimal():
    msg = server_adapter.validate_python(
        {"type": "metrics.turn", "turn_id": "t1", "tier": "T1"}
    )
    assert msg.cost_usd == 0.0
    assert msg.tokens.in_ == 0


def test_error_message():
    msg = server_adapter.validate_python(
        {
            "type": "error",
            "turn_id": "t1",
            "code": "stt_timeout",
            "message": "Deepgram did not return a final transcript in 3s",
            "fatal": False,
        }
    )
    assert isinstance(msg, ErrorMessage)
    assert msg.fatal is False


def test_error_rejects_unknown_code():
    with pytest.raises(ValidationError):
        server_adapter.validate_python(
            {"type": "error", "code": "meteor_strike", "message": "oops"}
        )
