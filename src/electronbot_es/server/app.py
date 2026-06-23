"""FastAPI application exposing /ws/voice (protocol v1).

Mixes text frames (JSON control) and binary frames (PCM audio) on a
single WebSocket per session. See docs/protocol.md for the contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
logging.getLogger("electronbot_es").setLevel(logging.DEBUG)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from electronbot_es.adapters.llm_groq import GroqLLM
from electronbot_es.adapters.search_tavily import TavilySearch
from electronbot_es.adapters.stt_deepgram import DeepgramSTT
from electronbot_es.adapters.tts_cartesia import CartesiaTTS
from electronbot_es.core.agentic import SearchAugmentedResponder
from electronbot_es.core.config import get_settings
from electronbot_es.core.messages import (
    ClientMessage,
    ErrorMessage,
    SessionReady,
)
from electronbot_es.core.orchestrator import VoiceSessionOrchestrator
from electronbot_es.core.messages import TurnProviders
from electronbot_es.router.intent_router import IntentRouter
from electronbot_es.router.templates import DEFAULT_TEMPLATES


_ROOT = Path(__file__).resolve().parents[3]
_CANNED_YAML = _ROOT / "src" / "electronbot_es" / "router" / "canned_responses.yaml"
_CANNED_ASSETS = _ROOT / "assets" / "canned"


logger = logging.getLogger("electronbot_es.server")

SERVER_VERSION = "0.1.0"

client_adapter = TypeAdapter(ClientMessage)


def create_app() -> FastAPI:
    app = FastAPI(title="ElectronBot-ES Voice Backend", version=SERVER_VERSION)

    # Router is stateless + thread-safe (read-only after init). Build once.
    router = IntentRouter.from_yaml(
        _CANNED_YAML, _CANNED_ASSETS, templates=list(DEFAULT_TEMPLATES)
    )
    logger.info(
        "router loaded: %d canned entries, %d template handlers",
        len(router.canned),
        len(router.templates),
    )

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "version": SERVER_VERSION}

    @app.websocket("/ws/voice")
    async def voice_ws(ws: WebSocket) -> None:
        await ws.accept()
        session_id = f"s_{uuid.uuid4().hex[:12]}"
        settings = get_settings()

        # ---- session.start handshake ----
        try:
            first = await ws.receive_text()
            start_msg = client_adapter.validate_json(first)
        except (WebSocketDisconnect, ValidationError) as e:
            logger.warning("session %s: bad session.start: %s", session_id, e)
            await ws.close()
            return

        if start_msg.type != "session.start":
            await ws.send_json(
                ErrorMessage(
                    code="protocol_violation",
                    message=f"Expected session.start, got {start_msg.type}",
                    fatal=True,
                ).model_dump()
            )
            await ws.close()
            return

        await ws.send_json(
            SessionReady(
                session_id=session_id,
                server_version=SERVER_VERSION,
            ).model_dump()
        )
        logger.info("session %s started for device=%s", session_id, start_msg.device_id)

        # ---- adapters ----
        stt = DeepgramSTT(api_key=settings.deepgram_api_key)
        llm = GroqLLM(api_key=settings.groq_api_key)
        tts = CartesiaTTS(api_key=settings.cartesia_api_key, sample_rate=16000)

        search: Optional[TavilySearch] = None
        responder: Optional[SearchAugmentedResponder] = None
        if settings.tavily_api_key:
            search = TavilySearch(api_key=settings.tavily_api_key)
            responder = SearchAugmentedResponder(llm=llm, search=search)
            logger.info("session %s: web search enabled (tavily)", session_id)

        # Pre-warm Cartesia WS in parallel so the first T2/T3 turn doesn't
        # eat the ~800ms handshake cost.
        asyncio.create_task(tts.prewarm())

        orchestrator = VoiceSessionOrchestrator(
            stt=stt,
            llm=llm,
            tts=tts,
            send_json=ws.send_json,
            send_binary=ws.send_bytes,
            providers_label=TurnProviders(
                stt="deepgram", llm="groq", tts="cartesia"
            ),
            router=router,
            responder=responder,
            tts_sample_rate=16000,
            tts_source="cartesia",
        )

        # ---- per-turn state ----
        audio_queue: Optional[asyncio.Queue[Optional[bytes]]] = None
        current_turn: Optional[asyncio.Task] = None

        async def audio_stream(q: asyncio.Queue[Optional[bytes]]) -> AsyncIterator[bytes]:
            while True:
                item = await q.get()
                if item is None:
                    return
                yield item

        try:
            while True:
                message = await ws.receive()
                msg_type = message.get("type")
                if msg_type == "websocket.disconnect":
                    break

                if "text" in message and message["text"] is not None:
                    try:
                        ctrl = client_adapter.validate_json(message["text"])
                    except ValidationError as e:
                        await ws.send_json(
                            ErrorMessage(
                                code="protocol_violation",
                                message=str(e),
                                fatal=False,
                            ).model_dump()
                        )
                        continue

                    t = ctrl.type
                    if t == "audio.start":
                        if current_turn and not current_turn.done():
                            current_turn.cancel()
                        audio_queue = asyncio.Queue()
                        current_turn = asyncio.create_task(
                            orchestrator.run_turn(
                                ctrl.turn_id, audio_stream(audio_queue)
                            )
                        )
                    elif t == "audio.end":
                        if audio_queue is not None:
                            await audio_queue.put(None)
                    elif t == "tts.cancel":
                        if current_turn and not current_turn.done():
                            current_turn.cancel()
                    elif t == "session.end":
                        break

                elif "bytes" in message and message["bytes"] is not None:
                    if audio_queue is None:
                        await ws.send_json(
                            ErrorMessage(
                                code="protocol_violation",
                                message="binary frame without prior audio.start",
                                fatal=False,
                            ).model_dump()
                        )
                        continue
                    await audio_queue.put(message["bytes"])

        except WebSocketDisconnect:
            logger.info("session %s disconnected", session_id)
        except Exception as e:
            logger.exception("session %s failed: %s", session_id, e)
        finally:
            if current_turn and not current_turn.done():
                current_turn.cancel()
                try:
                    await current_turn
                except (asyncio.CancelledError, Exception):
                    pass
            await stt.aclose()
            await tts.aclose()
            if search is not None:
                await search.aclose()
            await llm.aclose()
            try:
                await ws.close()
            except Exception:
                pass
            logger.info("session %s closed", session_id)

    return app


app = create_app()
