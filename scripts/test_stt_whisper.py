"""Smoke test: stream a WAV through FasterWhisperSTT and print transcripts.

Usage:
    uv run python scripts/test_stt_whisper.py path/to/audio.wav
"""

from __future__ import annotations

import asyncio
import sys
import time
import wave
from pathlib import Path
from typing import AsyncIterator

from electronbot_es.adapters.stt_whisper import FasterWhisperSTT


async def wav_chunks(
    path: Path, chunk_ms: int = 100, realtime: bool = True
) -> AsyncIterator[bytes]:
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
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
    with wave.open(str(wav_path), "rb") as wf:
        sample_rate = wf.getframerate()

    print("Cargando modelo (primera vez descarga ~470MB)...")
    t0 = time.perf_counter()
    stt = FasterWhisperSTT(sample_rate=sample_rate, model_size="small")
    print(f"Modelo listo en {(time.perf_counter() - t0):.2f}s")

    start = time.perf_counter()
    finals: list[str] = []
    first_at: float | None = None

    async for chunk in stt.transcribe_stream(wav_chunks(wav_path)):
        elapsed_ms = (time.perf_counter() - start) * 1000
        if first_at is None:
            first_at = elapsed_ms
        tag = "FINAL" if chunk.is_final else "interim"
        print(f"[{elapsed_ms:7.1f}ms] {tag:7s} | {chunk.text}")
        if chunk.is_final:
            finals.append(chunk.text)

    print("\n--- RESUMEN ---")
    if first_at is not None:
        print(f"first_chunk_at: {first_at:.1f} ms")
    print(f"final transcript: {' '.join(finals)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    asyncio.run(main(Path(sys.argv[1])))
