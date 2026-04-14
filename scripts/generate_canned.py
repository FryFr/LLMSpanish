"""Pre-synthesize canned T1 responses with Cartesia.

Reads src/electronbot_es/router/canned_responses.yaml, calls Cartesia for each
variant, writes WAVs into assets/canned/<entry_id>_<i>.wav.

Run once (and whenever the YAML or voice changes):
    uv run python scripts/generate_canned.py

Each WAV is 16kHz mono PCM — matches the server's TTS sample_rate so the
orchestrator can stream the bytes straight through without resampling.
"""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import yaml
from cartesia import AsyncCartesia

from electronbot_es.adapters.tts_cartesia import DEFAULT_MODEL_ID, DEFAULT_VOICE_ID
from electronbot_es.core.config import get_settings


SAMPLE_RATE = 16000  # match the server runtime
ROOT = Path(__file__).resolve().parents[1]
CANNED_YAML = ROOT / "src" / "electronbot_es" / "router" / "canned_responses.yaml"
OUT_DIR = ROOT / "assets" / "canned"


async def synth_one(
    client: AsyncCartesia, text: str, out_path: Path
) -> None:
    audio_bytes = bytearray()
    async with client.tts.websocket_connect() as connection:
        context = connection.context(
            model_id=DEFAULT_MODEL_ID,
            voice={"mode": "id", "id": DEFAULT_VOICE_ID},
            output_format={
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": SAMPLE_RATE,
            },
            language="es",
        )
        await context.push(text)
        await context.no_more_inputs()

        async for response in context.receive():
            rtype = getattr(response, "type", None)
            if rtype == "chunk" and response.audio:
                audio_bytes.extend(response.audio)
            elif rtype == "done":
                break
            elif rtype == "error":
                raise RuntimeError(f"Cartesia error: {response}")

    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(audio_bytes))


async def main() -> None:
    raw = yaml.safe_load(CANNED_YAML.read_text(encoding="utf-8"))
    entries = raw.get("canned", [])
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    client = AsyncCartesia(api_key=settings.cartesia_api_key)
    total_variants = 0
    failures = 0
    try:
        for entry in entries:
            entry_id = entry["id"]
            variants = entry["variants"]
            for i, text in enumerate(variants):
                total_variants += 1
                out_path = OUT_DIR / f"{entry_id}_{i}.wav"
                preview = text.encode("ascii", "replace").decode("ascii")
                print(f"[{total_variants:3d}] {entry_id}_{i}: {preview}")
                try:
                    await synth_one(client, text, out_path)
                    size_kb = out_path.stat().st_size / 1024
                    print(f"      -> {out_path.name} ({size_kb:.1f} KB)")
                except Exception as e:
                    print(f"      !! failed: {e}")
                    failures += 1
    finally:
        await client.close()

    print(f"\nDone. {total_variants - failures}/{total_variants} variants written to {OUT_DIR}")
    if failures:
        print(f"WARNING: {failures} failures — check logs above")


if __name__ == "__main__":
    asyncio.run(main())
