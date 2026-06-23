from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class TranscriptChunk:
    text: str
    is_final: bool
    confidence: float | None = None
    start_ms: int | None = None
    end_ms: int | None = None


@runtime_checkable
class STTAdapter(Protocol):
    async def transcribe_stream(
        self, audio: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptChunk]: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AudioChunk:
    pcm: bytes                  # raw PCM, int16 little-endian
    sample_rate: int
    is_final: bool = False      # true on the last chunk of a synthesis


@runtime_checkable
class TTSAdapter(Protocol):
    async def synthesize_stream(
        self, text: AsyncIterator[str]
    ) -> AsyncIterator[AudioChunk]: ...

    async def aclose(self) -> None: ...


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict


@runtime_checkable
class LLMAdapter(Protocol):
    async def generate_stream(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class SearchAdapter(Protocol):
    async def search(self, query: str) -> str: ...

    async def aclose(self) -> None: ...
