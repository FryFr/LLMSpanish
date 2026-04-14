# ElectronBot-ES

> Asistente de voz en **español neutro LATAM** para un robot de escritorio —
> fusión de [ElectronBot](https://github.com/peng-zhihui/ElectronBot) (cuerpo + servos + display)
> y [EchoEar](https://github.com/espressif/esp-box) (audio ESP32-S3), con backend cloud
> para lograr conversación fluida **<900 ms wake-to-first-audio**.

Este repo es el **backend de Week 1** + cliente mock para desarrollo en PC.
El firmware real del ESP32-S3 llega en Week 2-3.

---

## ¿Por qué existe esto?

La mayoría de los asistentes de voz DIY son lentos: 2-4 segundos entre que
terminás de hablar y escuchás la primera palabra del robot. Arriba de 1.5 s
la conversación se siente robot-lenta, y arriba de 3 s el usuario abandona.

**Objetivo**: primera palabra audible en menos de 900 ms en la mayoría de los
turnos, y menos de 200 ms en respuestas canned. La única forma de lograrlo
con LLMs grandes es con proveedores cloud especializados (Deepgram / Groq /
Cartesia) + un orquestador que hace streaming end-to-end desde el primer día.

---

## Arquitectura

```
┌──────────────────── DEVICE (ESP32-S3 — futuro) ────────────────────┐
│  mic ──▶ AFE ──▶ wake word "Hola Michi" ──▶ ACK filler <50ms       │
│                                                  │                  │
│                                                  ▼                  │
│                                           WebSocket client          │
│  speaker ◀── audio playback ◀── WebSocket client                    │
└────┬────────────────────────────────────────────────────────────────┘
     │  WebSocket binario + JSON control
     ▼
┌──────────────────── BACKEND (FastAPI async) ─────────────────────────┐
│                                                                       │
│   VoiceSessionOrchestrator                                            │
│     STT stream ──▶ Intent Router ──▶ response stream                  │
│                     │   │   │                                         │
│                     ▼   ▼   ▼                                         │
│                ┌───────────────────────────────────┐                  │
│                │  T1  Canned    (<200 ms)  ~40%    │                  │
│                │  T2  Template  (<400 ms)  ~30%    │                  │
│                │  T3  LLM full  (<900 ms)  ~30%    │                  │
│                └───────────────────────────────────┘                  │
│                                                                       │
│              Deepgram  ──▶  Groq 70B  ──▶  Cartesia Sonic             │
└───────────────────────────────────────────────────────────────────────┘
```

### Principios

- **Adapter pattern puro**: STT/LLM/TTS son interfaces async streaming,
  los proveedores se swapean por config. Cero `if provider == "..."`.
- **Streaming end-to-end**: TTS arranca a reproducir con la primera frase
  del LLM, sin esperar a que termine toda la generación (speculative TTS).
- **Intent Router 3-tier**: ~70 % de los turnos se responden **sin llamar
  al LLM completo**. Canned y Template matan la latencia percibida.
- **Barge-in**: el usuario interrumpe al robot hablando encima, el robot
  se calla al toque.
- **WebSocket persistente a Cartesia** (pre-warming): primera respuesta
  TTS sin pagar el ~700 ms de handshake.
- **Wake word on-device**: `microWakeWord` entrenable, corre en el ESP32-S3
  detrás del AFE de Espressif. En Week 1 se usa `hey_jarvis` como placeholder
  vía openWakeWord en el mock.

---

## Quick start

### 1. Prerrequisitos

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (gestor de dependencias)
- API keys (las 3 tienen free tier):
  - [Deepgram](https://deepgram.com) — STT (Nova-3)
  - [Groq](https://console.groq.com) — LLM (Llama 3.3 70B)
  - [Cartesia](https://cartesia.ai) — TTS (Sonic)
- Micrófono y parlantes funcionando

### 2. Setup

```bash
git clone <este-repo>
cd LLMSpanish
uv sync
cp .env.example .env
# Editar .env y pegar las 3 API keys
```

### 3. Correr backend + mock

En una terminal:
```bash
uv run uvicorn electronbot_es.server.app:app --host 127.0.0.1 --port 8000
```

En otra terminal:
```bash
# Modo manual (ENTER para hablar):
uv run python -m electronbot_es.mock.mock_esp32

# Modo wake word (decís "hey jarvis" — placeholder Week 1):
uv run python -m electronbot_es.mock.mock_esp32 --wake-word
```

### 4. Ver métricas

Cada turno queda loggeado en `logs/session.jsonl`. Para agregarlo:

```bash
uv run python scripts/metrics.py
uv run python scripts/metrics.py --last 20
```

Salida típica:
```
=== ElectronBot-ES metrics — 20 turns ===
tier    n     %   first_audio p50   p95   tts_first p50   p95   avg_cost   total
T1      8   40%            15 ms   30 ms          15 ms   30 ms  $0.000000  $0.0000
T2      6   30%           235 ms  280 ms         235 ms  280 ms  $0.001878  $0.0113
T3      6   30%           586 ms  820 ms         586 ms  820 ms  $0.006514  $0.0391
total cost: $0.0504   avg/turn: $0.002520
T1+T2 match rate: 70%  (target >60%)
```

---

## Estructura del repo

```
src/electronbot_es/
├── core/
│   ├── orchestrator.py     # VoiceSessionOrchestrator + speculative TTS
│   ├── cost.py             # Estimación de costo por turno
│   ├── obs.py              # Logger JSONL de métricas
│   ├── messages.py         # Schemas pydantic del protocolo WS
│   ├── protocols.py        # STT/LLM/TTS como Protocol
│   ├── persona.py          # System prompt del LLM
│   └── config.py
├── router/
│   ├── intent_router.py    # Decisión T1/T2/T3
│   ├── canned_responses.yaml
│   └── templates/          # Handlers T2 (hora, etc.)
├── adapters/
│   ├── stt_deepgram.py     │ stt_whisper_cpp.py
│   ├── llm_groq.py         │ llm_claude.py    │ llm_ollama.py
│   └── tts_cartesia.py     │ tts_piper.py
├── server/
│   └── app.py              # FastAPI + /ws/voice
└── mock/
    ├── mock_esp32.py       # Cliente de desarrollo
    └── wake_word.py        # openWakeWord wrapper

docs/
├── protocol.md             # Contrato WebSocket (fijado día 2)
├── roadmap.md              # Weeks 2-12+
├── hardware.md             # Decisiones de hardware
├── cloud-providers.md      # Setup de Deepgram/Groq/Cartesia
└── llm-benchmark.md        # Benchmark español LATAM

assets/canned/              # WAVs pre-sintetizados (T1)
scripts/                    # Tooling: generate_canned, metrics, try_voices, ...
```

---

## Latencias medidas

En modo cloud, desktop LATAM → PoPs US-East, conversación real:

| Tier | Qué hace                    | p50 wake-to-first-audio |
|------|-----------------------------|-------------------------|
| T1   | Lee WAV pre-sintetizado     | ~15 ms (I/O local)      |
| T2   | Template → Cartesia streaming| ~235 ms                |
| T3   | STT → Groq 70B → Cartesia   | ~586 ms                 |

El T2 bajó de ~935 ms a ~235 ms al hacer la conexión WS a Cartesia
persistente (pre-warming). Ver [`src/electronbot_es/adapters/tts_cartesia.py`](src/electronbot_es/adapters/tts_cartesia.py).

## Costo por turno

| Tier | Costo aprox | Componentes                         |
|------|-------------|-------------------------------------|
| T1   | **$0**      | Cero llamadas a providers           |
| T2   | ~$0.0019    | STT + TTS                           |
| T3   | ~$0.0065    | STT + LLM (in+out) + TTS            |

Promedio ponderado ~$0.0027/turno. Con 600 turnos/mes quedan en ~$1.60 de
costo variable — margen suficiente para una subscripción de $9.99/mes.

---

## Roadmap

- **Week 1 (acá estamos)**: backend + mock + router + observabilidad
- **Week 2**: firmware ESP32-S3 base + training real de "Hola Michi" con microWakeWord
- **Week 3**: servos + display + personalidad física (portado del STM32 original)
- **Week 4**: integración end-to-end + aceptación del MVP
- **Week 5-6**: multi-tenant (Supabase + auth + billing con RevenueCat)
- **Week 7-8**: app móvil React Native + Expo (pairing BLE + subscripciones)
- **Week 9**: deploy producción + stores + beta
- **Week 10-12**: iteración + launch público

Detalles en [`docs/roadmap.md`](docs/roadmap.md).

---

## Estado actual (Week 1)

- [x] Adapters cloud: Deepgram / Groq / Cartesia
- [x] Adapters local: whisper.cpp / Ollama / Piper
- [x] Protocolo WebSocket + schemas pydantic (inmutable)
- [x] Orchestrator con streaming end-to-end + speculative TTS
- [x] Intent Router 3-tier (T1 canned, T2 template, T3 LLM)
- [x] Pre-warming de Cartesia (−700 ms en T2)
- [x] Cost tracker por turno
- [x] Wake word baseline (placeholder `hey_jarvis`, real en Week 2)
- [x] Observabilidad JSONL + script de métricas
- [ ] Validación formal de 20 turnos mixtos
- [ ] Firmware ESP32-S3 (Week 2)

---

## Licencia

MIT. Hecho con cariño en LATAM.
