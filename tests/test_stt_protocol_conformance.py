from __future__ import annotations

from typing import AsyncIterator

from electronbot_es.adapters.stt_deepgram import DeepgramSTT
from electronbot_es.core.protocols import STTAdapter, TranscriptChunk

# NOTE: we do NOT import FasterWhisperSTT at module top because constructing
# it triggers a 470MB model download on first use. The conformance test that
# touches it is gated on the model being present locally.


class FakeSTT:
    async def transcribe_stream(
        self, audio: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptChunk]:
        async for _ in audio:
            yield TranscriptChunk(text="hola", is_final=True, confidence=0.99)

    async def aclose(self) -> None:
        return


def test_deepgram_conforms_to_sttadapter() -> None:
    adapter = DeepgramSTT(api_key="dg_fake_key_for_ctor_only")
    assert isinstance(adapter, STTAdapter)


def test_fake_conforms_to_sttadapter() -> None:
    assert isinstance(FakeSTT(), STTAdapter)
