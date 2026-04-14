from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import AsyncIterator

from piper import PiperVoice

from electronbot_es.core.protocols import AudioChunk


DEFAULT_MODEL_DIR = Path("D:/models/piper")
DEFAULT_VOICE = "es_MX-claude-high"

_SENTENCE_END = re.compile(r"[\.!\?\n]+|,\s")


class PiperTTS:
    """Local streaming TTS via Piper (ONNX, CPU).

    Piper synthesizes one sentence at a time. To fit the streaming
    Protocol, we buffer incoming fragments until a sentence boundary
    is seen, then synthesize that sentence and yield its audio chunk.
    This gives pseudo-streaming at sentence granularity — perfect for
    Day 4 speculative TTS where the LLM emits punctuation tokens.
    """

    def __init__(
        self,
        *,
        voice_name: str = DEFAULT_VOICE,
        model_dir: Path = DEFAULT_MODEL_DIR,
        use_cuda: bool = False,
    ) -> None:
        model_path = model_dir / f"{voice_name}.onnx"
        config_path = model_dir / f"{voice_name}.onnx.json"
        if not model_path.exists():
            raise FileNotFoundError(
                f"Piper model not found: {model_path}. "
                f"Run: uv run python -m piper.download_voices {voice_name}"
            )
        self._voice = PiperVoice.load(
            model_path=model_path,
            config_path=config_path,
            use_cuda=use_cuda,
        )
        self._sample_rate = self._voice.config.sample_rate

    async def synthesize_stream(
        self, text: AsyncIterator[str]
    ) -> AsyncIterator[AudioChunk]:
        loop = asyncio.get_running_loop()
        buffer = ""

        async def flush(sentence: str) -> list[AudioChunk]:
            if not sentence.strip():
                return []
            # Piper is sync + CPU-bound → offload to thread pool.
            chunks_raw = await loop.run_in_executor(
                None, lambda: list(self._voice.synthesize(sentence))
            )
            return [
                AudioChunk(
                    pcm=c.audio_int16_bytes,
                    sample_rate=self._sample_rate,
                    is_final=False,
                )
                for c in chunks_raw
                if c.audio_int16_bytes
            ]

        async for fragment in text:
            if not fragment:
                continue
            buffer += fragment
            # Try to break on the last sentence boundary seen.
            while True:
                match = None
                for m in _SENTENCE_END.finditer(buffer):
                    match = m
                if match is None:
                    break
                cut = match.end()
                sentence, buffer = buffer[:cut], buffer[cut:]
                for chunk in await flush(sentence):
                    yield chunk
                # Only break once per fragment iteration — allow more to accumulate.
                break

        # Drain remaining buffer.
        tail_chunks = await flush(buffer)
        for i, chunk in enumerate(tail_chunks):
            yield AudioChunk(
                pcm=chunk.pcm,
                sample_rate=chunk.sample_rate,
                is_final=(i == len(tail_chunks) - 1),
            )

    async def aclose(self) -> None:
        return
