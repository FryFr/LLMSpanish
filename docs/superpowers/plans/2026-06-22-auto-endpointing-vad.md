# Auto-Endpointing por VAD — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar el corte manual por ENTER del cliente mock con detección automática de fin de habla (VAD), para simular el ESP32-S3 y descontaminar las métricas de latencia.

**Architecture:** Un módulo aislado `SilenceEndpointer` (máquina de estados sobre `webrtcvad`, VAD inyectable para testear) decide cuándo terminó la frase. El loop de grabación del mock alimenta cada frame de 20ms al endpointer; al detectar silencio sostenido, corta y envía `audio.end`. El endpointing de Deepgram queda como red de seguridad. Cero cambios al protocolo v1.

**Tech Stack:** Python 3.12, `webrtcvad-wheels` (ya instalado), pytest, asyncio, sounddevice/websockets (mock).

---

## Estructura de archivos

- **Crear** `src/electronbot_es/mock/endpointer.py` — `SilenceEndpointer`: una sola responsabilidad, decidir fin de habla.
- **Crear** `tests/test_endpointer.py` — tests unitarios con `FakeVAD` determinístico.
- **Modificar** `src/electronbot_es/mock/mock_esp32.py` — integrar el endpointer, medir `speech_end`, flags CLI, override por ENTER solo en Windows.
- **Modificar** `scripts/metrics.py` — relabelar la columna para que se lea como `speech_end→first_audio`.

---

## Task 1: SilenceEndpointer (módulo + tests)

**Files:**
- Create: `src/electronbot_es/mock/endpointer.py`
- Test: `tests/test_endpointer.py`

- [ ] **Step 1: Escribir el test que falla**

```python
# tests/test_endpointer.py
"""Tests del SilenceEndpointer — máquina de estados aislada con un VAD fake."""

from __future__ import annotations

from electronbot_es.mock.endpointer import SilenceEndpointer

FRAME = b"\x00" * 640  # 20ms @ 16kHz mono s16le; el contenido no importa con FakeVAD


class FakeVAD:
    """VAD determinístico: devuelve el próximo bool del script en cada llamada."""

    def __init__(self, script: list[bool]) -> None:
        self._script = list(script)
        self._i = 0

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        v = self._script[self._i] if self._i < len(self._script) else False
        self._i += 1
        return v


def _run(script: list[bool], **kw) -> list[bool]:
    """Procesa un frame por entrada del script; devuelve la lista de resultados."""
    ep = SilenceEndpointer(vad=FakeVAD(script), frame_ms=20, **kw)
    return [ep.process(FRAME) for _ in script]


def test_speech_then_silence_endpoints_after_hangover() -> None:
    # min_speech=60ms→3 frames, hangover=100ms→5 frames.
    script = [True] * 3 + [False] * 5
    out = _run(script, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000)
    assert out[:7] == [False] * 7
    assert out[7] is True  # 5to frame de silencio dispara el corte


def test_silence_only_never_endpoints_before_max() -> None:
    script = [False] * 20
    out = _run(script, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000)
    assert out == [False] * 20  # nunca hubo voz → no corta (max=500 frames)


def test_continuous_speech_hits_max_cap() -> None:
    # max=100ms → 5 frames; voz continua nunca genera silencio.
    script = [True] * 10
    out = _run(script, min_speech_ms=60, hangover_ms=100, max_utterance_ms=100)
    assert out[:4] == [False] * 4
    assert out[4] is True  # tope duro al 5to frame


def test_pause_mid_speech_resets_silence_run() -> None:
    # Pausa corta en medio NO debe cortar: la voz reinicia el conteo de silencio.
    script = [True] * 3 + [False] * 3 + [True] * 1 + [False] * 5
    out = _run(script, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000)
    assert True not in out[:11]
    assert out[11] is True  # recién el 5to silencio CONTINUO tras la pausa


def test_reset_returns_to_initial_state() -> None:
    ep = SilenceEndpointer(
        vad=FakeVAD([True, True, True, False, False, False, False, False]),
        frame_ms=20, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000,
    )
    results = [ep.process(FRAME) for _ in range(8)]
    assert results[-1] is True
    ep.reset()
    # Tras reset, frames de silencio no deben cortar de inmediato.
    ep2_vad_silence = [ep.process(FRAME) for _ in range(5)]
    assert ep2_vad_silence == [False] * 5
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `uv run pytest tests/test_endpointer.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'electronbot_es.mock.endpointer'`

- [ ] **Step 3: Implementar el módulo**

```python
# src/electronbot_es/mock/endpointer.py
"""SilenceEndpointer — decide cuándo terminó de hablar la persona.

Máquina de estados sobre un VAD por frames:

    WAITING_SPEECH ──(voz)──► SPEAKING ──(hangover de silencio)──► ENDPOINTED

El VAD es inyectable: por defecto construye `webrtcvad.Vad`, pero los tests
pasan un fake determinístico para probar la lógica en aislamiento.

Guardas:
- min_speech_ms : voz mínima acumulada antes de permitir un corte (mata clics
                  y el silencio inicial).
- hangover_ms   : silencio CONTINUO que dispara el corte (el "feeling").
- max_utterance_ms : tope duro por si el VAD nunca ve silencio.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol


class _VAD(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


class _State(Enum):
    WAITING_SPEECH = "waiting_speech"
    SPEAKING = "speaking"
    ENDPOINTED = "endpointed"


class SilenceEndpointer:
    def __init__(
        self,
        *,
        aggressiveness: int = 2,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        min_speech_ms: int = 300,
        hangover_ms: int = 700,
        max_utterance_ms: int = 15000,
        vad: Optional[_VAD] = None,
    ) -> None:
        if vad is None:
            import webrtcvad

            vad = webrtcvad.Vad(aggressiveness)
        self._vad = vad
        self._sample_rate = sample_rate
        self._frame_bytes = sample_rate * frame_ms // 1000 * 2
        self._min_speech_frames = max(1, min_speech_ms // frame_ms)
        self._hangover_frames = max(1, hangover_ms // frame_ms)
        self._max_frames = max(1, max_utterance_ms // frame_ms)
        self.reset()

    def reset(self) -> None:
        self._state = _State.WAITING_SPEECH
        self._speech_frames = 0
        self._silence_run = 0
        self._total_frames = 0

    def process(self, frame: bytes) -> bool:
        """Devuelve True cuando se alcanzó el fin de habla. Idempotente tras True."""
        if self._state is _State.ENDPOINTED:
            return True
        # Frame de tamaño inesperado: ignorar para el VAD (webrtcvad exige
        # exactamente 10/20/30ms), no romper el turno.
        if len(frame) != self._frame_bytes:
            return False

        self._total_frames += 1
        voiced = self._vad.is_speech(frame, self._sample_rate)

        if self._state is _State.WAITING_SPEECH:
            if voiced:
                self._speech_frames += 1
                if self._speech_frames >= self._min_speech_frames:
                    self._state = _State.SPEAKING
                    self._silence_run = 0
        elif self._state is _State.SPEAKING:
            if voiced:
                self._silence_run = 0
            else:
                self._silence_run += 1
                if self._silence_run >= self._hangover_frames:
                    self._state = _State.ENDPOINTED
                    return True

        if self._total_frames >= self._max_frames:
            self._state = _State.ENDPOINTED
            return True
        return False
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `uv run pytest tests/test_endpointer.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/electronbot_es/mock/endpointer.py tests/test_endpointer.py
git commit -m "feat: add SilenceEndpointer VAD state machine with tests"
```

---

## Task 2: Integrar el endpointer en el mock

**Files:**
- Modify: `src/electronbot_es/mock/mock_esp32.py`

No tiene test automatizado: es glue de I/O en vivo (mic + WS). La lógica testeable
ya está cubierta en Task 1. Verificación = smoke manual + import limpio.

- [ ] **Step 1: Importar el endpointer**

En `src/electronbot_es/mock/mock_esp32.py`, junto al import existente
(`from electronbot_es.mock.wake_word import WakeWordDetector`, línea 36), agregar:

```python
from electronbot_es.mock.endpointer import SilenceEndpointer
```

- [ ] **Step 2: Extender la firma de `run_session` con los parámetros de tuning**

Reemplazar la firma (línea 167):

```python
async def run_session(uri: str, device_id: str, wake_enabled: bool = False) -> None:
```

por:

```python
async def run_session(
    uri: str,
    device_id: str,
    wake_enabled: bool = False,
    silence_ms: int = 700,
    vad_aggressiveness: int = 2,
) -> None:
```

- [ ] **Step 3: Construir el endpointer una vez por sesión**

Después del bloque que crea el `WakeWordDetector` (tras la línea 216, justo antes de
`async def wait_for_wake()`), agregar:

```python
        endpointer = SilenceEndpointer(
            aggressiveness=vad_aggressiveness,
            sample_rate=SAMPLE_RATE,
            frame_ms=FRAME_MS,
            hangover_ms=silence_ms,
        )
```

- [ ] **Step 4: Reescribir el loop de grabación con corte por VAD**

Reemplazar este bloque (líneas 271-290):

```python
                async def pump_mic():
                    while mic.recording:
                        try:
                            frame = await asyncio.wait_for(
                                mic.record_queue.get(), timeout=0.1
                            )
                            await ws.send(frame)
                        except asyncio.TimeoutError:
                            continue

                pump_task = asyncio.create_task(pump_mic())

                await wait_key("[recording... press ENTER to stop]\n")
                mic.recording = False
                state = ClientState.IDLE
                await pump_task

                await ws.send(
                    json.dumps({"type": "audio.end", "turn_id": turn_id})
                )
```

por:

```python
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
```

- [ ] **Step 5: Cross-check cliente speech_end → first_audio**

En el bloque que imprime las métricas pendientes (líneas 376-380), reemplazar:

```python
                if pending_metrics:
                    print(
                        pending_metrics
                        + ("  (barge-in)" if barge_in_fired.is_set() else "")
                    )
```

por:

```python
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
```

- [ ] **Step 6: Exponer las flags CLI**

En `main()`, después del `add_argument("--wake-word", ...)` (línea 397-401), agregar:

```python
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
```

Y reemplazar la llamada (línea 405):

```python
        asyncio.run(run_session(uri, args.device_id, wake_enabled=args.wake_word))
```

por:

```python
        asyncio.run(
            run_session(
                uri,
                args.device_id,
                wake_enabled=args.wake_word,
                silence_ms=args.silence_ms,
                vad_aggressiveness=args.vad_aggressiveness,
            )
        )
```

- [ ] **Step 7: Verificar import limpio**

Run: `uv run python -c "import electronbot_es.mock.mock_esp32"`
Expected: sin output, sin error (exit 0)

- [ ] **Step 8: Smoke manual (requiere backend corriendo en otra terminal)**

Run: `uv run python -m electronbot_es.mock.mock_esp32 --wake-word --silence-ms 700`
Esperado: tras decir "hey jarvis" y hablar, al quedarte callado ~700ms aparece
`endpoint (silence)` y el robot responde **sin tocar ninguna tecla**. La línea
`client speech_end->first_audio` debe estar cerca del `speech_end→first_audio` del
servidor (±~100ms).

- [ ] **Step 9: Commit**

```bash
git add src/electronbot_es/mock/mock_esp32.py
git commit -m "feat: auto-endpoint mock recording via VAD, add tuning flags"
```

---

## Task 3: Relabelar las métricas para que no mientan

**Files:**
- Modify: `scripts/metrics.py`

- [ ] **Step 1: Renombrar la columna y aclarar el docstring**

En `scripts/metrics.py`, en el docstring (línea 9) reemplazar:

```python
    table with: turns per tier, p50/p95 of wake_to_first_audio and
```

por:

```python
    table with: turns per tier, p50/p95 of speech_end->first_audio (el campo
    wire se llama wake_to_first_audio por compat v1) and
```

En el header de la tabla (línea 72) reemplazar:

```python
    header = f"{'tier':<4} {'n':>4} {'%':>5}  {'first_audio p50':>16} {'p95':>7}  {'tts_first p50':>14} {'p95':>7}  {'avg_cost':>10} {'total':>10}"
```

por:

```python
    header = f"{'tier':<4} {'n':>4} {'%':>5}  {'spEnd->aud p50':>16} {'p95':>7}  {'tts_first p50':>14} {'p95':>7}  {'avg_cost':>10} {'total':>10}"
```

- [ ] **Step 2: Verificar que el script sigue corriendo**

Run: `uv run python scripts/metrics.py --last 5`
Expected: imprime la tabla con la columna `spEnd->aud p50` (o `no metrics file`
si todavía no hay turnos — ambos son OK, no debe romper).

- [ ] **Step 3: Commit**

```bash
git add scripts/metrics.py
git commit -m "docs: relabel metrics column to speech_end->first_audio"
```

---

## Self-review

- **Cobertura del spec:** endpointer aislado y testeable (Task 1) ✓; integración con
  corte por silencio + ENTER override solo Windows (Task 2) ✓; medición
  `speech_end→first_audio` cliente (Task 2 step 5) ✓; flags de tuning (Task 2 step 6) ✓;
  relabel de métricas (Task 3) ✓; protocolo intacto ✓.
- **Criterios de éxito del spec:** (1) responde sin tecla → Task 2 step 8; (2) métricas
  descontaminadas → consecuencia del corte por VAD; (3) cross-check cliente/servidor →
  Task 2 step 5; (4) tests sin hardware → Task 1; (5) `--silence-ms` cambia el feeling →
  Task 2 steps 6/8.
- **Consistencia de tipos:** `SilenceEndpointer(vad=, frame_ms=, min_speech_ms=,
  hangover_ms=, max_utterance_ms=, aggressiveness=, sample_rate=)` idéntico en tests
  (Task 1) y construcción del mock (Task 2). `process()/reset()` consistentes.
- **Sin placeholders:** todos los pasos traen código o comando concreto.
```
