"""Smoke test: stream text to PiperTTS (local) and save to WAV.

Usage:
    uv run python scripts/test_tts_piper.py
    uv run python scripts/test_tts_piper.py "Tu frase acá"
"""

from __future__ import annotations

import asyncio
import sys
import time
import wave
from pathlib import Path
from typing import AsyncIterator

from electronbot_es.adapters.tts_piper import PiperTTS


DEFAULT_FRAGMENTS = [
    "Hola, ",
    "soy Michi, ",
    "tu asistente de voz. ",
    "Estoy listo para escucharte.",
]


async def text_fragments(fragments: list[str], delay_ms: int = 50) -> AsyncIterator[str]:
    for f in fragments:
        yield f
        await asyncio.sleep(delay_ms / 1000)


async def main(fragments: list[str], out: Path) -> None:
    tts = PiperTTS()

    all_pcm = bytearray()
    sample_rate = 22050
    start = time.perf_counter()
    first_audio_at: float | None = None
    chunks = 0

    async for chunk in tts.synthesize_stream(text_fragments(fragments)):
        elapsed_ms = (time.perf_counter() - start) * 1000
        if first_audio_at is None:
            first_audio_at = elapsed_ms
            print(f"[{elapsed_ms:7.1f}ms] FIRST AUDIO ({len(chunk.pcm)} bytes)")
        all_pcm.extend(chunk.pcm)
        sample_rate = chunk.sample_rate
        chunks += 1

    total_ms = (time.perf_counter() - start) * 1000
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(all_pcm))

    duration_s = len(all_pcm) / 2 / sample_rate
    print("\n--- RESUMEN ---")
    print(f"first_audio_at: {first_audio_at:.1f} ms" if first_audio_at else "no audio")
    print(f"total_time:     {total_ms:.1f} ms")
    print(f"chunks:         {chunks}")
    print(f"sample_rate:    {sample_rate} Hz")
    print(f"audio_duration: {duration_s:.2f} s ({len(all_pcm)} bytes)")
    print(f"saved:          {out}")


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        frags = [sys.argv[1]]
    else:
        frags = DEFAULT_FRAGMENTS
    asyncio.run(main(frags, Path("assets/test/tts_piper_out.wav")))
