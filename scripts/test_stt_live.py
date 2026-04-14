"""Smoke test: stream a WAV through DeepgramSTT and print transcripts.

Usage:
    uv run python scripts/test_stt_live.py path/to/audio.wav
"""

from __future__ import annotations

import asyncio
import sys
import time
import wave
from pathlib import Path
from typing import AsyncIterator

from electronbot_es.adapters.stt_deepgram import DeepgramSTT
from electronbot_es.core.config import get_settings


async def wav_chunks(
    path: Path, chunk_ms: int = 100, realtime: bool = True
) -> AsyncIterator[bytes]:
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1, "mono WAV required"
        assert wf.getsampwidth() == 2, "16-bit PCM required"
        rate = wf.getframerate()
        frames_per_chunk = int(rate * chunk_ms / 1000)
        delay = chunk_ms / 1000
        while True:
            data = wf.readframes(frames_per_chunk)
            if not data:
                return
            yield data
            if realtime:
                await asyncio.sleep(delay)


async def main(wav_path: Path) -> None:
    settings = get_settings()
    if not settings.deepgram_api_key:
        print("ERROR: DEEPGRAM_API_KEY not set in .env", file=sys.stderr)
        sys.exit(1)

    with wave.open(str(wav_path), "rb") as wf:
        sample_rate = wf.getframerate()

    stt = DeepgramSTT(
        api_key=settings.deepgram_api_key, sample_rate=sample_rate
    )

    start = time.perf_counter()
    first_chunk_at: float | None = None
    finals: list[str] = []

    async for chunk in stt.transcribe_stream(wav_chunks(wav_path)):
        elapsed_ms = (time.perf_counter() - start) * 1000
        if first_chunk_at is None:
            first_chunk_at = elapsed_ms
        tag = "FINAL" if chunk.is_final else "interim"
        print(f"[{elapsed_ms:7.1f}ms] {tag:7s} | {chunk.text}")
        if chunk.is_final:
            finals.append(chunk.text)

    print("\n--- RESUMEN ---")
    print(f"first_chunk_at: {first_chunk_at:.1f} ms" if first_chunk_at else "no chunks")
    print(f"final transcript: {' '.join(finals)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    asyncio.run(main(Path(sys.argv[1])))
