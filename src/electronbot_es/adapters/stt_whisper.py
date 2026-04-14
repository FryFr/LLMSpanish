from __future__ import annotations

import asyncio
from typing import AsyncIterator

import numpy as np
from faster_whisper import WhisperModel

from electronbot_es.core.protocols import TranscriptChunk


class FasterWhisperSTT:
    """Local STT using faster-whisper (CTranslate2).

    Whisper is not natively streaming — it processes fixed windows. We emit
    pseudo-streaming by accumulating audio into a buffer and running the model
    every `chunk_window_s` seconds. The text of each block is emitted as a
    single TranscriptChunk with `is_final=True` (no interims).
    """

    def __init__(
        self,
        *,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "es",
        sample_rate: int = 16000,
        chunk_window_s: float = 2.0,
        beam_size: int = 1,
        vad_filter: bool = True,
        download_root: str | None = "D:/models/faster-whisper",
    ) -> None:
        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
            download_root=download_root,
        )
        self._language = language
        self._sample_rate = sample_rate
        self._chunk_window_samples = int(sample_rate * chunk_window_s)
        self._beam_size = beam_size
        self._vad_filter = vad_filter

    def _transcribe_block(self, pcm_f32: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            pcm_f32,
            language=self._language,
            beam_size=self._beam_size,
            vad_filter=self._vad_filter,
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def transcribe_stream(
        self, audio: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptChunk]:
        loop = asyncio.get_running_loop()
        buffer = bytearray()
        position_samples = 0

        async def flush(final: bool) -> TranscriptChunk | None:
            nonlocal buffer, position_samples
            if len(buffer) < 2:
                return None
            pcm_i16 = np.frombuffer(bytes(buffer), dtype=np.int16)
            pcm_f32 = pcm_i16.astype(np.float32) / 32768.0
            text = await loop.run_in_executor(None, self._transcribe_block, pcm_f32)
            samples = len(pcm_i16)
            start_ms = int(position_samples * 1000 / self._sample_rate)
            end_ms = int((position_samples + samples) * 1000 / self._sample_rate)
            position_samples += samples
            buffer = bytearray()
            if not text:
                return None
            return TranscriptChunk(
                text=text, is_final=True, start_ms=start_ms, end_ms=end_ms
            )

        async for chunk in audio:
            buffer.extend(chunk)
            if len(buffer) // 2 >= self._chunk_window_samples:
                tc = await flush(final=False)
                if tc is not None:
                    yield tc

        tc = await flush(final=True)
        if tc is not None:
            yield tc

    async def aclose(self) -> None:
        return
