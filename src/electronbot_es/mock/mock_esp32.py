"""MockESP32 — Python client simulating ESP32-S3 device over WS v1.

States:
    IDLE       : waiting for a wake word or ENTER to start a turn
    RECORDING  : mic frames streamed as binary to the server; recording
                 auto-stops on silence via SilenceEndpointer (client-side
                 VAD), simulating the ESP32-S3's on-device endpointing.
                 On Windows, ENTER or SPACE can also force-stop recording.
    SPEAKING   : TTS playing back; barge-in cancels the turn.

Barge-in note: in this mock, barge-in is triggered by a manual keypress
(Windows-only). The real ESP32-S3 will use ESP-SR AFE (hardware AEC + VAD)
to detect user voice over TTS and fire tts.cancel automatically — AEC
requires dedicated hardware, so that path is not emulated here.

Usage:
    uv run python -m electronbot_es.mock.mock_esp32
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import uuid
from enum import Enum
from typing import Optional

import numpy as np
import sounddevice as sd
import websockets

from electronbot_es.mock.wake_word import WakeWordDetector
from electronbot_es.mock.endpointer import SilenceEndpointer

try:
    import msvcrt  # Windows only
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False


SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2


class ClientState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    SPEAKING = "speaking"


class AudioPlayer:
    """Sequential PCM player with a persistent byte buffer."""

    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream: Optional[sd.OutputStream] = None

    def start(self) -> None:
        def callback(outdata, frames, time_info, status):
            needed = frames * 2
            with self._lock:
                available = min(len(self._buf), needed)
                out_bytes = bytes(self._buf[:available])
                del self._buf[:available]
            if available < needed:
                out_bytes += b"\x00" * (needed - available)
            outdata[:] = np.frombuffer(out_bytes, dtype=np.int16).reshape(-1, 1)

        self._stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            callback=callback,
        )
        self._stream.start()

    def write(self, pcm: bytes) -> None:
        with self._lock:
            self._buf.extend(pcm)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def pending_bytes(self) -> int:
        with self._lock:
            return len(self._buf)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Microphone:
    """Simple mic InputStream that feeds the record queue while active."""

    def __init__(self) -> None:
        self._stream: Optional[sd.InputStream] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.recording: bool = False
        self.record_queue: asyncio.Queue[bytes] = asyncio.Queue()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

        def cb(indata, frames, time_info, status):
            if self.recording:
                self._loop.call_soon_threadsafe(
                    self.record_queue.put_nowait, bytes(indata)
                )

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            callback=cb,
        )
        self._stream.start()

    def drain_queue(self) -> None:
        while not self.record_queue.empty():
            try:
                self.record_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


async def wait_key(prompt: str = "") -> None:
    """Wait for ENTER or SPACE using msvcrt polling (Windows).

    Avoids input() so we don't mix stdin-buffered reads with the
    direct-console reads used for barge-in during playback.
    """
    if prompt:
        print(prompt, end="", flush=True)
    if _HAS_MSVCRT:
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n", " "):
                    print()
                    return
            await asyncio.sleep(0.03)
    else:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, input)


async def run_session(
    uri: str,
    device_id: str,
    wake_enabled: bool = False,
    silence_ms: int = 700,
    vad_aggressiveness: int = 2,
) -> None:
    print(f"Connecting to {uri} ...")
    async with websockets.connect(uri, max_size=None) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "session.start",
                    "protocol_version": "1.0",
                    "device_id": device_id,
                    "locale": "es-419",
                    "audio_in": {"sample_rate": SAMPLE_RATE, "encoding": "pcm_s16le", "channels": 1},
                    "audio_out": {"sample_rate": SAMPLE_RATE, "encoding": "pcm_s16le", "channels": 1},
                    "capabilities": {
                        "ack_filler_local": False,
                        "barge_in": True,
                        "display": False,
                        "servos": False,
                    },
                }
            )
        )
        ready = json.loads(await ws.recv())
        assert ready.get("type") == "session.ready", ready
        print(f"Session ready: {ready['session_id']}")
        print("(manual barge-in: press ENTER during playback to interrupt)")

        player = AudioPlayer()
        player.start()

        mic = Microphone()
        loop = asyncio.get_running_loop()
        mic.start(loop)

        state: ClientState = ClientState.IDLE
        current_turn_id: Optional[str] = None
        barge_in_fired = asyncio.Event()

        detector: Optional[WakeWordDetector] = None
        ack_filler_pcm: bytes = b""
        if wake_enabled:
            print("loading wake word detector (hey_jarvis placeholder for 'Hola Michi')...")
            detector = WakeWordDetector(keyword="hey_jarvis", threshold=0.5)
            try:
                import wave as _wave
                with _wave.open("assets/canned/ack_filler_1.wav", "rb") as w:
                    assert w.getframerate() == SAMPLE_RATE
                    ack_filler_pcm = w.readframes(w.getnframes())
            except Exception as e:
                print(f"[warn] ack filler load failed: {e}")

        endpointer = SilenceEndpointer(
            aggressiveness=vad_aggressiveness,
            sample_rate=SAMPLE_RATE,
            frame_ms=FRAME_MS,
            hangover_ms=silence_ms,
        )

        async def wait_for_wake() -> None:
            """Consume mic frames through the detector until it fires."""
            assert detector is not None
            mic.drain_queue()
            mic.recording = True
            try:
                while True:
                    frame = await mic.record_queue.get()
                    if detector.process(frame):
                        return
            finally:
                mic.recording = False

        async def send_cancel(turn_id: str) -> None:
            await ws.send(
                json.dumps(
                    {
                        "type": "tts.cancel",
                        "turn_id": turn_id,
                        "reason": "barge_in",
                    }
                )
            )

        try:
            while True:
                if wake_enabled:
                    print('\n[say "hey jarvis" to talk (placeholder for Hola Michi), Ctrl+C to quit]')
                    await wait_for_wake()
                    print("  [*] WAKE WORD detected — ack filler + recording")
                    if ack_filler_pcm:
                        player.write(ack_filler_pcm)
                else:
                    await wait_key("\n[press ENTER to talk, Ctrl+C to quit]\n")

                turn_id = f"t_{uuid.uuid4().hex[:10]}"
                current_turn_id = turn_id
                barge_in_fired.clear()
                turn_start = time.perf_counter()

                mic.drain_queue()
                mic.recording = True
                state = ClientState.RECORDING

                await ws.send(
                    json.dumps(
                        {
                            "type": "audio.start",
                            "turn_id": turn_id,
                            "timestamp_ms": int(turn_start * 1000),
                        }
                    )
                )

                endpointer.reset()
                speech_end_at: Optional[float] = None
                print("[recording... auto-stops on silence]")

                async def pump_mic():
                    nonlocal speech_end_at
                    while mic.recording:
                        try:
                            frame = await asyncio.wait_for(
                                mic.record_queue.get(), timeout=0.1
                            )
                        except asyncio.TimeoutError:
                            # Windows: ENTER/space fuerza el corte manual.
                            if _HAS_MSVCRT and msvcrt.kbhit():
                                ch = msvcrt.getwch()
                                if ch in ("\r", "\n", " "):
                                    mic.recording = False
                            continue
                        await ws.send(frame)
                        if endpointer.process(frame):
                            speech_end_at = (time.perf_counter() - turn_start) * 1000
                            mic.recording = False
                            print(f"[{speech_end_at:7.0f}ms] endpoint (silence)")

                pump_task = asyncio.create_task(pump_mic())
                await pump_task
                state = ClientState.IDLE
                if speech_end_at is None:
                    speech_end_at = (time.perf_counter() - turn_start) * 1000

                await ws.send(
                    json.dumps({"type": "audio.end", "turn_id": turn_id})
                )

                first_audio_at: Optional[float] = None
                pending_metrics: Optional[str] = None
                barge_watch_task: Optional[asyncio.Task] = None

                async def watch_barge():
                    """Poll for ENTER/space keypress while SPEAKING."""
                    if not _HAS_MSVCRT:
                        return  # non-Windows: barge-in disabled in mock
                    while state == ClientState.SPEAKING and not barge_in_fired.is_set():
                        if msvcrt.kbhit():
                            ch = msvcrt.getwch()
                            if ch in ("\r", "\n", " "):
                                print("  [!] BARGE-IN (manual) — silencing robot")
                                player.clear()
                                barge_in_fired.set()
                                if current_turn_id:
                                    await send_cancel(current_turn_id)
                                return
                        await asyncio.sleep(0.03)

                while True:
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        if first_audio_at is None:
                            first_audio_at = (time.perf_counter() - turn_start) * 1000
                            print(f"[{first_audio_at:7.0f}ms] FIRST AUDIO OUT")
                            print("[press SPACE/ENTER to interrupt]")
                            state = ClientState.SPEAKING
                            barge_watch_task = asyncio.create_task(watch_barge())
                        if not barge_in_fired.is_set():
                            player.write(msg)
                        continue
                    data = json.loads(msg)
                    t = data.get("type")
                    elapsed = (time.perf_counter() - turn_start) * 1000
                    if t == "stt.partial":
                        print(f"[{elapsed:7.0f}ms] partial: {data['text']}")
                    elif t == "stt.final":
                        print(f"[{elapsed:7.0f}ms] FINAL:   {data['text']}")
                    elif t == "llm.status":
                        print(f"[{elapsed:7.0f}ms] llm:     {data['state']}")
                    elif t == "tts.start":
                        print(f"[{elapsed:7.0f}ms] tts.start (tier={data['tier']}, src={data['source']})")
                    elif t == "tts.end":
                        print(f"[{elapsed:7.0f}ms] tts.end")
                    elif t == "metrics.turn":
                        lat = data["latencies_ms"]
                        tok = data.get("tokens", {})
                        pending_metrics = (
                            f"\n--- METRICS turn {data['turn_id']} (tier={data.get('tier')}) ---\n"
                            f"  stt_final:           {lat.get('stt_final')} ms\n"
                            f"  llm_first_token:     {lat.get('llm_first_token')} ms\n"
                            f"  tts_first_chunk:     {lat.get('tts_first_chunk')} ms\n"
                            f"  wake_to_first_audio: {lat.get('wake_to_first_audio')} ms\n"
                            f"  tokens:              in={tok.get('in', 0)} out={tok.get('out', 0)}\n"
                            f"  cost_usd:            ${data.get('cost_usd', 0):.6f}"
                        )
                        break
                    elif t == "error":
                        print(f"[ERROR] {data['code']}: {data['message']}")
                        if data.get("fatal"):
                            return
                        break

                # Wait for audio tail to drain (or barge-in to fire).
                while player.pending_bytes() > 0 and not barge_in_fired.is_set():
                    await asyncio.sleep(0.05)

                current_turn_id = None
                state = ClientState.IDLE

                # Reset wake word internal state so accumulated features from
                # the previous turn don't poison the next detection window.
                if detector is not None:
                    detector.reset()

                # Cancel the barge watcher if it's still waiting.
                if barge_watch_task and not barge_watch_task.done():
                    barge_watch_task.cancel()
                    try:
                        await barge_watch_task
                    except (asyncio.CancelledError, Exception):
                        pass

                if pending_metrics:
                    print(
                        pending_metrics
                        + ("  (barge-in)" if barge_in_fired.is_set() else "")
                    )
                    if speech_end_at is not None and first_audio_at is not None:
                        print(
                            f"  client speech_end->first_audio: "
                            f"{first_audio_at - speech_end_at:7.0f} ms"
                        )
        except KeyboardInterrupt:
            print("\nbye.")
        finally:
            try:
                await ws.send(json.dumps({"type": "session.end"}))
            except Exception:
                pass
            mic.stop()
            player.stop()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--device-id", default="mock-esp32-001")
    p.add_argument(
        "--wake-word",
        action="store_true",
        help='Enable wake word trigger ("hey jarvis" — Week 1 placeholder for "Hola Michi")',
    )
    p.add_argument(
        "--silence-ms",
        type=int,
        default=700,
        help="Silencio continuo (ms) que corta la grabación (hangover del VAD)",
    )
    p.add_argument(
        "--vad-aggressiveness",
        type=int,
        default=2,
        choices=[0, 1, 2, 3],
        help="Agresividad del webrtcvad (0=permisivo, 3=estricto)",
    )
    args = p.parse_args()
    uri = f"ws://{args.host}:{args.port}/ws/voice"
    try:
        asyncio.run(
            run_session(
                uri,
                args.device_id,
                wake_enabled=args.wake_word,
                silence_ms=args.silence_ms,
                vad_aggressiveness=args.vad_aggressiveness,
            )
        )
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
