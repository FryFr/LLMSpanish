"""Generate a sample WAV per candidate Cartesia voice so the user can pick.

Outputs to assets/voice_candidates/<voice_name>.wav
"""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path

from cartesia import AsyncCartesia

from electronbot_es.core.config import get_settings


CANDIDATES = {
    "Valeria_CheerfulPromoter": "ad8eee76-d702-4a1f-a1bd-7596755ae4c9",
    "Camila_HappyConversationalist": "bef2ba57-5c10-433b-b215-3bef35110a81",
    "Alondra_ReassuringSister": "ccfea4bf-b3f4-421e-87ed-dd05dae01431",
    "Catalina_NeighborlyGuide": "162e0f37-8504-474c-bb33-c606c01890dc",
    "Fernanda_FriendlyGuide": "b4b8e2af-6139-466e-a93a-30c20d2e1fc5",
    "Juanita_HelpfulCompanion": "c68a8bd0-f99e-4e7f-915d-a097da6d024c",
}

SAMPLE_TEXT = (
    "¡Hola parce! Soy Michi, tu gatico asistente. "
    "Qué chévere tenerte por acá. "
    "Si necesitas algo, me dices de una y te ayudo, ¿listo?"
)

SAMPLE_RATE = 22050
MODEL_ID = "sonic-2"


async def gen_voice(client: AsyncCartesia, name: str, voice_id: str, out_dir: Path) -> None:
    print(f"Generating {name} ...")
    audio_bytes = bytearray()

    async with client.tts.websocket_connect() as connection:
        context = connection.context(
            model_id=MODEL_ID,
            voice={"mode": "id", "id": voice_id},
            output_format={
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": SAMPLE_RATE,
            },
            language="es",
        )
        await context.push(SAMPLE_TEXT)
        await context.no_more_inputs()

        async for response in context.receive():
            rtype = getattr(response, "type", None)
            if rtype == "chunk" and response.audio:
                audio_bytes.extend(response.audio)
            elif rtype == "done":
                break
            elif rtype == "error":
                raise RuntimeError(f"Cartesia error: {response}")

    out_path = out_dir / f"{name}.wav"
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(audio_bytes))
    duration = len(audio_bytes) / 2 / SAMPLE_RATE
    print(f"  -> {out_path} ({duration:.1f}s)")


async def main() -> None:
    out_dir = Path("assets/voice_candidates")
    out_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    client = AsyncCartesia(api_key=settings.cartesia_api_key)
    try:
        for name, vid in CANDIDATES.items():
            try:
                await gen_voice(client, name, vid, out_dir)
            except Exception as e:
                print(f"  !! {name} failed: {e}")
    finally:
        await client.close()

    print(f"\nDone. Listen to them in {out_dir} and pick your favorite.")


if __name__ == "__main__":
    asyncio.run(main())
