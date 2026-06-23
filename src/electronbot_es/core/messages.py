"""Pydantic schemas for the ElectronBot-ES WebSocket v1 protocol.

The canonical contract lives in docs/protocol.md. This module is the
runtime enforcement layer — every JSON control message crossing the WS
is validated here. Binary frames (PCM audio) are NOT handled here;
they travel as raw WebSocket binary frames outside this schema.

FROZEN (Day 2). Changes require a v2 discriminator.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


PROTOCOL_VERSION = "1.0"


# ---------- shared ----------

class AudioFormat(BaseModel):
    sample_rate: int = 16000
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    channels: int = 1


class DeviceCapabilities(BaseModel):
    ack_filler_local: bool = False
    barge_in: bool = True
    display: bool = False
    servos: bool = False


# ---------- client → server ----------

class SessionStart(BaseModel):
    type: Literal["session.start"] = "session.start"
    protocol_version: str = PROTOCOL_VERSION
    device_id: str
    user_id: Optional[str] = None
    auth_token: Optional[str] = None
    locale: str = "es-419"
    audio_in: AudioFormat = Field(default_factory=AudioFormat)
    audio_out: AudioFormat = Field(default_factory=AudioFormat)
    capabilities: DeviceCapabilities = Field(default_factory=DeviceCapabilities)


class AudioStart(BaseModel):
    type: Literal["audio.start"] = "audio.start"
    turn_id: str
    timestamp_ms: Optional[int] = None


class AudioEnd(BaseModel):
    type: Literal["audio.end"] = "audio.end"
    turn_id: str


class TtsCancel(BaseModel):
    type: Literal["tts.cancel"] = "tts.cancel"
    turn_id: str
    reason: Literal["barge_in", "user_cancel", "timeout"] = "barge_in"


class SessionEnd(BaseModel):
    type: Literal["session.end"] = "session.end"


ClientMessage = Annotated[
    Union[SessionStart, AudioStart, AudioEnd, TtsCancel, SessionEnd],
    Field(discriminator="type"),
]


# ---------- server → client ----------

class SessionReady(BaseModel):
    type: Literal["session.ready"] = "session.ready"
    session_id: str
    protocol_version: str = PROTOCOL_VERSION
    server_version: str


class SttPartial(BaseModel):
    type: Literal["stt.partial"] = "stt.partial"
    turn_id: str
    text: str
    confidence: Optional[float] = None


class SttFinal(BaseModel):
    type: Literal["stt.final"] = "stt.final"
    turn_id: str
    text: str
    confidence: Optional[float] = None
    language: Optional[str] = None


class LlmStatus(BaseModel):
    type: Literal["llm.status"] = "llm.status"
    turn_id: str
    state: Literal["thinking", "generating", "done", "searching"]


class TtsStart(BaseModel):
    type: Literal["tts.start"] = "tts.start"
    turn_id: str
    sample_rate: int = 16000
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    channels: int = 1
    tier: Literal["T1", "T2", "T3"]
    source: str


class TtsEnd(BaseModel):
    type: Literal["tts.end"] = "tts.end"
    turn_id: str


class TurnLatencies(BaseModel):
    stt_final: Optional[int] = None
    llm_first_token: Optional[int] = None
    tts_first_chunk: Optional[int] = None
    wake_to_first_audio: Optional[int] = None


class TurnTokens(BaseModel):
    in_: int = Field(default=0, alias="in")
    out: int = 0

    model_config = {"populate_by_name": True}


class TurnProviders(BaseModel):
    stt: Optional[str] = None
    llm: Optional[str] = None
    tts: Optional[str] = None


class MetricsTurn(BaseModel):
    type: Literal["metrics.turn"] = "metrics.turn"
    turn_id: str
    tier: Literal["T1", "T2", "T3"]
    latencies_ms: TurnLatencies = Field(default_factory=TurnLatencies)
    tokens: TurnTokens = Field(default_factory=TurnTokens)
    cost_usd: float = 0.0
    providers: TurnProviders = Field(default_factory=TurnProviders)


ErrorCode = Literal[
    "auth_failed",
    "quota_exceeded",
    "stt_timeout",
    "llm_timeout",
    "tts_failed",
    "provider_down",
    "protocol_violation",
    "internal",
]


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    turn_id: Optional[str] = None
    code: ErrorCode
    message: str
    fatal: bool = False


ServerMessage = Annotated[
    Union[
        SessionReady,
        SttPartial,
        SttFinal,
        LlmStatus,
        TtsStart,
        TtsEnd,
        MetricsTurn,
        ErrorMessage,
    ],
    Field(discriminator="type"),
]
