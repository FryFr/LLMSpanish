from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents,
)

from electronbot_es.core.protocols import TranscriptChunk

# Silence the SDK's noisy "tasks cancelled error:" log at shutdown — it's
# cleanup noise, not a real failure. Deepgram uses VerboseLogger per-module,
# so we blanket-set the namespace AND disable propagation on the noisy one.
for _name in (
    "deepgram",
    "deepgram.clients.common.v1.abstract_async_websocket",
    "deepgram.clients.listen.v1.websocket.async_client",
):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.CRITICAL)
    _lg.propagate = False


class DeepgramSTT:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "nova-3",
        language: str = "es-419",
        sample_rate: int = 16000,
        interim_results: bool = True,
        endpointing_ms: int = 300,
        utterance_end_ms: int = 1000,
        finalize_timeout_s: float = 3.0,
    ) -> None:
        self._client = DeepgramClient(
            api_key,
            DeepgramClientOptions(
                # CRITICAL == 50; suppresses ERROR-level cleanup noise
                # the SDK emits on shutdown ("tasks cancelled error:").
                verbose=logging.CRITICAL,
                options={"keepalive": "true"},
            ),
        )
        self._options = LiveOptions(
            model=model,
            language=language,
            encoding="linear16",
            sample_rate=sample_rate,
            channels=1,
            interim_results=interim_results,
            smart_format=True,
            punctuate=True,
            endpointing=endpointing_ms,
            utterance_end_ms=utterance_end_ms,
            vad_events=True,
        )
        self._finalize_timeout_s = finalize_timeout_s

    async def transcribe_stream(
        self, audio: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscriptChunk]:
        queue: asyncio.Queue[TranscriptChunk | None] = asyncio.Queue()
        connection = self._client.listen.asyncwebsocket.v("1")

        async def on_transcript(_self, result, **_kwargs):
            alt = result.channel.alternatives[0]
            text = alt.transcript
            if not text:
                return
            await queue.put(
                TranscriptChunk(
                    text=text,
                    is_final=bool(result.is_final),
                    confidence=getattr(alt, "confidence", None),
                    start_ms=int(result.start * 1000) if result.start else None,
                    end_ms=int((result.start + result.duration) * 1000)
                    if result.start and result.duration
                    else None,
                )
            )

        async def on_utterance_end(_self, **_kwargs):
            # Speech segment closed on server side — stop consuming.
            await queue.put(None)

        async def on_close(_self, **_kwargs):
            await queue.put(None)

        async def on_error(_self, _error, **_kwargs):
            await queue.put(None)

        connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
        connection.on(LiveTranscriptionEvents.UtteranceEnd, on_utterance_end)
        connection.on(LiveTranscriptionEvents.Close, on_close)
        connection.on(LiveTranscriptionEvents.Error, on_error)

        started = await connection.start(self._options)
        if not started:
            raise RuntimeError("Failed to open Deepgram streaming connection")

        finalize_sent = asyncio.Event()

        async def pump_audio() -> None:
            try:
                async for chunk in audio:
                    await connection.send(chunk)
            finally:
                # Flush remaining buffers server-side so final transcripts
                # + UtteranceEnd get delivered. Do NOT call finish() here.
                await connection.finalize()
                finalize_sent.set()

        pump_task = asyncio.create_task(pump_audio())

        try:
            while True:
                # After finalize, if Deepgram never emits UtteranceEnd within
                # the grace window, break out to avoid hanging forever.
                if finalize_sent.is_set():
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=self._finalize_timeout_s
                        )
                    except asyncio.TimeoutError:
                        break
                else:
                    item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            if not pump_task.done():
                pump_task.cancel()
                try:
                    await pump_task
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                await connection.finish()
            except Exception:
                pass

    async def aclose(self) -> None:
        return
