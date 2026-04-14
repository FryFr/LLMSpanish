from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from cartesia import AsyncCartesia

from electronbot_es.core.protocols import AudioChunk


logger = logging.getLogger(__name__)


# Default Cartesia voice — Juanita "Helpful Companion" (feminine, young,
# warm). Slight Mexican accent compensated by Colombian vocabulary in the
# system prompt and canned responses. Voice cloning for authentic Colombian
# accent is a Week 2+ upgrade.
DEFAULT_VOICE_ID = "c68a8bd0-f99e-4e7f-915d-a097da6d024c"  # Juanita
DEFAULT_MODEL_ID = "sonic-2"


class CartesiaTTS:
    """Cloud streaming TTS via Cartesia WebSocket — persistent connection.

    The WebSocket connection is opened lazily (or eagerly via `prewarm()`)
    and reused across turns. Each call to `synthesize_stream` creates a
    fresh `context()` on the same connection, which is how Cartesia's SDK
    is meant to be used for continuous sessions.

    This kills the ~800ms handshake cost that was dominating T2 latency.
    """

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str = DEFAULT_VOICE_ID,
        model_id: str = DEFAULT_MODEL_ID,
        language: str = "es",
        sample_rate: int = 16000,
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._language = language
        self._sample_rate = sample_rate

        self._client: Optional[AsyncCartesia] = None
        self._connection_cm = None  # the async context manager from websocket_connect()
        self._connection = None
        self._connect_lock = asyncio.Lock()

    async def prewarm(self) -> None:
        """Eagerly open the WS connection so the first turn pays no handshake."""
        try:
            await self._ensure_connection()
            logger.info("cartesia tts prewarmed")
        except Exception as e:
            logger.warning("cartesia prewarm failed (will retry lazily): %s", e)

    async def _ensure_connection(self):
        async with self._connect_lock:
            if self._connection is not None:
                return self._connection
            if self._client is None:
                self._client = AsyncCartesia(api_key=self._api_key)
            cm = self._client.tts.websocket_connect()
            connection = await cm.__aenter__()
            self._connection_cm = cm
            self._connection = connection
            return connection

    async def _reset_connection(self) -> None:
        """Tear down a dead connection so the next call reopens it."""
        async with self._connect_lock:
            cm, self._connection_cm = self._connection_cm, None
            self._connection = None
            if cm is not None:
                try:
                    await cm.__aexit__(None, None, None)
                except Exception:
                    pass

    async def synthesize_stream(
        self, text: AsyncIterator[str]
    ) -> AsyncIterator[AudioChunk]:
        connection = await self._ensure_connection()

        try:
            context = connection.context(
                model_id=self._model_id,
                voice={"mode": "id", "id": self._voice_id},
                output_format={
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": self._sample_rate,
                },
                language=self._language,
            )

            async def pump_text() -> None:
                try:
                    async for fragment in text:
                        if not fragment:
                            continue
                        await context.push(fragment)
                finally:
                    await context.no_more_inputs()

            pump_task = asyncio.create_task(pump_text())

            try:
                async for response in context.receive():
                    rtype = getattr(response, "type", None)
                    if rtype == "chunk":
                        audio = response.audio
                        if not audio:
                            continue
                        yield AudioChunk(
                            pcm=audio,
                            sample_rate=self._sample_rate,
                            is_final=bool(response.done),
                        )
                    elif rtype == "done":
                        break
                    elif rtype == "error":
                        raise RuntimeError(
                            f"Cartesia TTS error: {getattr(response, 'error', response)}"
                        )
            finally:
                if not pump_task.done():
                    pump_task.cancel()
                    try:
                        await pump_task
                    except (asyncio.CancelledError, Exception):
                        pass
        except Exception:
            # Dead connection → drop it so the next call reconnects.
            await self._reset_connection()
            raise

    async def aclose(self) -> None:
        await self._reset_connection()
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
