"""VoiceSessionOrchestrator — Day 4 streaming pipeline.

Coordinates STT → LLM → TTS for one turn with speculative TTS:
tokens from the LLM feed a sentence buffer; each completed sentence
goes onto a text queue that TTS pulls from as an AsyncIterator[str].
This means the first TTS chunk leaves the server while the LLM is
still generating later sentences.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

from electronbot_es.core.cost import TurnCost, cost_t1, cost_t2, cost_t3
from electronbot_es.core.obs import log_turn
from electronbot_es.core.messages import (
    LlmStatus,
    MetricsTurn,
    SttFinal,
    SttPartial,
    TtsEnd,
    TtsStart,
    TurnLatencies,
    TurnProviders,
    TurnTokens,
)
from electronbot_es.core.persona import build_messages
from electronbot_es.core.protocols import (
    LLMAdapter,
    STTAdapter,
    TTSAdapter,
)
from electronbot_es.router.intent_router import IntentRouter, RouterDecision


logger = logging.getLogger(__name__)

_SENTENCE_BOUNDARY = re.compile(r"[\.!\?]+(?=\s|$)")

# ~20ms of 16kHz mono s16le = 640 bytes. Matches the client frame cadence
# so T1 playback feels the same as streamed TTS.
_T1_CHUNK_BYTES = 640

SendJson = Callable[[dict], Awaitable[None]]
SendBinary = Callable[[bytes], Awaitable[None]]


@dataclass
class TurnMetrics:
    turn_id: str
    started_at: float = field(default_factory=time.perf_counter)
    stt_final_ms: Optional[float] = None
    llm_first_token_ms: Optional[float] = None
    tts_first_chunk_ms: Optional[float] = None
    # Cost accounting inputs.
    audio_in_bytes: int = 0
    llm_tokens_in: int = 0  # rough estimate: word count of the built messages
    llm_tokens_out: int = 0  # rough estimate: word count of emitted tokens
    tts_chars: int = 0

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000

    def stt_seconds(self, sample_rate: int = 16000) -> float:
        # 16-bit mono PCM → 2 bytes per sample.
        return self.audio_in_bytes / (sample_rate * 2)


class VoiceSessionOrchestrator:
    def __init__(
        self,
        *,
        stt: STTAdapter,
        llm: LLMAdapter,
        tts: TTSAdapter,
        send_json: SendJson,
        send_binary: SendBinary,
        providers_label: TurnProviders,
        router: Optional[IntentRouter] = None,
        tts_sample_rate: int = 16000,
        tts_source: str = "cartesia",
    ) -> None:
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._send_json = send_json
        self._send_binary = send_binary
        self._providers = providers_label
        self._router = router
        self._tts_sample_rate = tts_sample_rate
        self._tts_source = tts_source

    async def run_turn(
        self, turn_id: str, audio_iter: AsyncIterator[bytes]
    ) -> None:
        metrics = TurnMetrics(turn_id=turn_id)
        transcript = ""

        async def counting_audio_iter() -> AsyncIterator[bytes]:
            async for frame in audio_iter:
                metrics.audio_in_bytes += len(frame)
                yield frame

        try:
            # ---------- STT ----------
            # Break on the first final: one turn = one final in our protocol.
            # Staying in the loop would block on the STT adapter's grace window.
            async for chunk in self._stt.transcribe_stream(counting_audio_iter()):
                if chunk.is_final:
                    transcript = chunk.text.strip()
                    metrics.stt_final_ms = metrics.elapsed_ms()
                    await self._send_json(
                        SttFinal(
                            turn_id=turn_id,
                            text=transcript,
                            confidence=chunk.confidence,
                            language="es",
                        ).model_dump()
                    )
                    break
                else:
                    if chunk.text:
                        await self._send_json(
                            SttPartial(
                                turn_id=turn_id,
                                text=chunk.text,
                                confidence=chunk.confidence,
                            ).model_dump()
                        )

            if not transcript:
                logger.warning("turn %s: empty transcript, skipping LLM", turn_id)
                return

            # ---------- Router: T1 / T2 / T3 ----------
            decision: RouterDecision = (
                self._router.route(transcript)
                if self._router is not None
                else RouterDecision(tier="T3")
            )
            logger.info(
                "turn %s routed tier=%s handler=%s",
                turn_id,
                decision.tier,
                decision.handler_id,
            )

            if decision.tier == "T1":
                await self._run_t1(turn_id, decision, metrics)
                return
            if decision.tier == "T2":
                await self._run_t2(turn_id, decision, metrics)
                return

            # ---------- T3: LLM + speculative TTS ----------
            await self._send_json(
                LlmStatus(turn_id=turn_id, state="thinking").model_dump()
            )

            # Rough token count for input billing: words in the built prompt.
            # Not perfect (Groq tokenizer ≠ whitespace split) but for Week 1
            # cost tracking it's within ~20% — good enough to spot trends.
            built_messages = build_messages(transcript)
            metrics.llm_tokens_in = sum(
                len(m.content.split()) for m in built_messages
            )

            text_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

            async def llm_producer() -> None:
                buffer = ""
                try:
                    async for token in self._llm.generate_stream(built_messages):
                        if metrics.llm_first_token_ms is None:
                            metrics.llm_first_token_ms = metrics.elapsed_ms()
                        metrics.llm_tokens_out += len(token.split()) or 1
                        buffer += token
                        # Emit as many complete sentences as we can.
                        while True:
                            m = _SENTENCE_BOUNDARY.search(buffer)
                            if not m:
                                break
                            cut = m.end()
                            sentence, buffer = buffer[:cut], buffer[cut:].lstrip()
                            if sentence.strip():
                                metrics.tts_chars += len(sentence)
                                await text_queue.put(sentence)
                    # Flush remainder.
                    if buffer.strip():
                        metrics.tts_chars += len(buffer)
                        await text_queue.put(buffer)
                finally:
                    await text_queue.put(None)

            async def text_iter() -> AsyncIterator[str]:
                while True:
                    item = await text_queue.get()
                    if item is None:
                        return
                    yield item

            llm_task = asyncio.create_task(llm_producer())

            await self._send_json(
                TtsStart(
                    turn_id=turn_id,
                    sample_rate=self._tts_sample_rate,
                    tier="T3",
                    source=self._tts_source,
                ).model_dump()
            )

            first_audio_sent = False
            try:
                async for audio_chunk in self._tts.synthesize_stream(text_iter()):
                    if not audio_chunk.pcm:
                        continue
                    if not first_audio_sent:
                        metrics.tts_first_chunk_ms = metrics.elapsed_ms()
                        await self._send_json(
                            LlmStatus(
                                turn_id=turn_id, state="generating"
                            ).model_dump()
                        )
                        first_audio_sent = True
                    await self._send_binary(audio_chunk.pcm)
            finally:
                if not llm_task.done():
                    llm_task.cancel()
                    try:
                        await llm_task
                    except (asyncio.CancelledError, Exception):
                        pass

            await self._send_json(
                TtsEnd(turn_id=turn_id).model_dump()
            )

            # ---------- metrics ----------
            cost = cost_t3(
                stt_seconds=metrics.stt_seconds(self._tts_sample_rate),
                llm_tokens_in=metrics.llm_tokens_in,
                llm_tokens_out=metrics.llm_tokens_out,
                tts_chars=metrics.tts_chars,
            )
            metrics_payload = MetricsTurn(
                turn_id=turn_id,
                tier="T3",
                latencies_ms=TurnLatencies(
                    stt_final=int(metrics.stt_final_ms or 0),
                    llm_first_token=int(metrics.llm_first_token_ms or 0),
                    tts_first_chunk=int(metrics.tts_first_chunk_ms or 0),
                    wake_to_first_audio=int(
                        max(0, (metrics.tts_first_chunk_ms or 0) - (metrics.stt_final_ms or 0))
                    ),
                ),
                tokens=TurnTokens.model_validate(
                    {"in": metrics.llm_tokens_in, "out": metrics.llm_tokens_out}
                ),
                providers=self._providers,
                cost_usd=round(cost.total, 6),
            ).model_dump(by_alias=True)
            await self._send_json(metrics_payload)
            log_turn(metrics_payload)
            logger.info(
                "turn %s T3 done: stt=%.0fms llm_ftt=%.0fms tts_first=%.0fms "
                "tok_in=%d tok_out=%d chars=%d cost=$%.6f",
                turn_id,
                metrics.stt_final_ms or 0,
                metrics.llm_first_token_ms or 0,
                metrics.tts_first_chunk_ms or 0,
                metrics.llm_tokens_in,
                metrics.llm_tokens_out,
                metrics.tts_chars,
                cost.total,
            )
        except asyncio.CancelledError:
            logger.info("turn %s cancelled (barge-in or disconnect)", turn_id)
            raise
        except Exception as e:
            logger.exception("turn %s failed: %s", turn_id, e)

    # ---------- T1: canned WAV short-circuit ----------

    async def _run_t1(
        self,
        turn_id: str,
        decision: RouterDecision,
        metrics: TurnMetrics,
    ) -> None:
        wav_path = decision.canned_wav
        assert wav_path is not None
        if not wav_path.exists():
            logger.error("turn %s: canned wav missing: %s", turn_id, wav_path)
            # Fall back to T3 would require LLM setup; instead emit error + return.
            return

        pcm = _read_wav_pcm(wav_path, expected_sr=self._tts_sample_rate)

        await self._send_json(
            TtsStart(
                turn_id=turn_id,
                sample_rate=self._tts_sample_rate,
                tier="T1",
                source="canned",
            ).model_dump()
        )

        for offset in range(0, len(pcm), _T1_CHUNK_BYTES):
            if metrics.tts_first_chunk_ms is None:
                metrics.tts_first_chunk_ms = metrics.elapsed_ms()
            await self._send_binary(pcm[offset : offset + _T1_CHUNK_BYTES])

        await self._send_json(TtsEnd(turn_id=turn_id).model_dump())
        cost = cost_t1()
        metrics_payload = MetricsTurn(
            turn_id=turn_id,
            tier="T1",
            latencies_ms=TurnLatencies(
                stt_final=int(metrics.stt_final_ms or 0),
                tts_first_chunk=int(metrics.tts_first_chunk_ms or 0),
                wake_to_first_audio=int(
                    max(0, (metrics.tts_first_chunk_ms or 0) - (metrics.stt_final_ms or 0))
                ),
            ),
            providers=TurnProviders(
                stt=self._providers.stt, llm=None, tts="canned"
            ),
            cost_usd=round(cost.total, 6),
        ).model_dump(by_alias=True)
        await self._send_json(metrics_payload)
        log_turn(metrics_payload)
        logger.info(
            "turn %s T1 done: handler=%s stt=%.0fms first=%.0fms cost=$%.6f",
            turn_id,
            decision.handler_id,
            metrics.stt_final_ms or 0,
            metrics.tts_first_chunk_ms or 0,
            cost.total,
        )

    # ---------- T2: template text → TTS streaming ----------

    async def _run_t2(
        self,
        turn_id: str,
        decision: RouterDecision,
        metrics: TurnMetrics,
    ) -> None:
        text = decision.template_text or ""
        if not text.strip():
            logger.warning("turn %s: empty template text", turn_id)
            return

        metrics.tts_chars = len(text)

        await self._send_json(
            TtsStart(
                turn_id=turn_id,
                sample_rate=self._tts_sample_rate,
                tier="T2",
                source=self._tts_source,
            ).model_dump()
        )

        async def single_text() -> AsyncIterator[str]:
            yield text

        first_audio_sent = False
        async for audio_chunk in self._tts.synthesize_stream(single_text()):
            if not audio_chunk.pcm:
                continue
            if not first_audio_sent:
                metrics.tts_first_chunk_ms = metrics.elapsed_ms()
                first_audio_sent = True
            await self._send_binary(audio_chunk.pcm)

        await self._send_json(TtsEnd(turn_id=turn_id).model_dump())
        cost = cost_t2(
            stt_seconds=metrics.stt_seconds(self._tts_sample_rate),
            tts_chars=metrics.tts_chars,
        )
        metrics_payload = MetricsTurn(
            turn_id=turn_id,
            tier="T2",
            latencies_ms=TurnLatencies(
                stt_final=int(metrics.stt_final_ms or 0),
                tts_first_chunk=int(metrics.tts_first_chunk_ms or 0),
                wake_to_first_audio=int(
                    max(0, (metrics.tts_first_chunk_ms or 0) - (metrics.stt_final_ms or 0))
                ),
            ),
            providers=TurnProviders(
                stt=self._providers.stt, llm=None, tts=self._providers.tts
            ),
            cost_usd=round(cost.total, 6),
        ).model_dump(by_alias=True)
        await self._send_json(metrics_payload)
        log_turn(metrics_payload)
        logger.info(
            "turn %s T2 done: handler=%s text=%r first=%.0fms cost=$%.6f",
            turn_id,
            decision.handler_id,
            text,
            metrics.tts_first_chunk_ms or 0,
            cost.total,
        )


def _read_wav_pcm(path: Path, expected_sr: int) -> bytes:
    """Read raw PCM bytes from a mono s16le WAV. Asserts sample rate."""
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1, f"{path}: expected mono"
        assert w.getsampwidth() == 2, f"{path}: expected 16-bit"
        assert w.getframerate() == expected_sr, (
            f"{path}: sr={w.getframerate()} expected={expected_sr}"
        )
        return w.readframes(w.getnframes())
