<div align="center">

# 🐱 Michibot

### El primer asistente de voz conversacional en español LATAM que se siente humano

*Un robot de escritorio que te escucha, te entiende y te responde en menos de un segundo.*

[![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![ESP32-S3](https://img.shields.io/badge/ESP32--S3-firmware-E7352C?logo=espressif&logoColor=white)](https://www.espressif.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Week 1](https://img.shields.io/badge/status-week%201%20complete-brightgreen)]()
[![LATAM](https://img.shields.io/badge/español-LATAM-yellow)]()

**[Quick Start](#-quick-start) · [Arquitectura](#-arquitectura) · [Performance](#-performance) · [Roadmap](#-roadmap) · [Contribuir](#-contribuir)**

</div>

---

## 💡 ¿De qué se trata?

La mayoría de los asistentes de voz DIY son **lentos**. Entre que terminás de hablar
y escuchás la primera palabra del robot pasan 2 a 4 segundos. Arriba de 1.5 s
se siente robot-lento. Arriba de 3 s el usuario abandona.

**Michibot tiene un solo objetivo**: que la primera palabra del robot sea audible
en **menos de 900 ms** en turnos complejos, y en **menos de 200 ms** en respuestas
conocidas. A esa velocidad la conversación deja de sentirse como hablarle a una
máquina.

No es magia — es la suma de decisiones de arquitectura que la mayoría de los
proyectos DIY no toman: streaming end-to-end desde el primer día, wake word
on-device, un router de 3 tiers que evita llamar al LLM en el 70 % de los turnos,
proveedores cloud especializados por etapa, y pre-warming de todas las conexiones.

> Michibot es la fusión de **[ElectronBot](https://github.com/peng-zhihui/ElectronBot)**
> (cuerpo + servos + display) y **[EchoEar](https://github.com/espressif/esp-box)**
> (audio ESP32-S3), con un backend en Python que lo hace conversacional.

---

## ⚡ Performance

Medido en condiciones reales — desktop LATAM contra PoPs US-East, red doméstica:

<table>
<thead>
<tr>
<th align="left">Tier</th>
<th align="left">Qué hace</th>
<th align="right">p50 first-audio</th>
<th align="right">Costo / turno</th>
<th align="left">% de turnos</th>
</tr>
</thead>
<tbody>
<tr>
<td><b>T1</b> · <i>Canned</i></td>
<td>Respuesta WAV pre-sintetizada desde disco</td>
<td align="right"><b>~15 ms</b></td>
<td align="right"><b>$0.000000</b></td>
<td>~40 %</td>
</tr>
<tr>
<td><b>T2</b> · <i>Template</i></td>
<td>Datos locales → Cartesia streaming</td>
<td align="right"><b>~235 ms</b></td>
<td align="right">~$0.0019</td>
<td>~30 %</td>
</tr>
<tr>
<td><b>T3</b> · <i>LLM full</i></td>
<td>STT → Groq Llama 70B → Cartesia, con speculative TTS</td>
<td align="right"><b>~586 ms</b></td>
<td align="right">~$0.0065</td>
<td>~30 %</td>
</tr>
</tbody>
</table>

**Costo promedio ponderado**: `~$0.0027 por turno`. Con 600 turnos/mes el costo
variable es `~$1.60` — margen suficiente para una subscripción de `$9.99/mes`.

> 🔬 El T2 bajó de 935 ms → 235 ms (−700 ms) al hacer la conexión WebSocket a
> Cartesia persistente. Ver [`adapters/tts_cartesia.py`](src/electronbot_es/adapters/tts_cartesia.py).

---

## ✨ Highlights

- 🎯 **Intent Router 3-tier** — el 70 % de los turnos se responden **sin llamar al LLM**
- ⚡ **Streaming end-to-end** desde el primer día (speculative TTS por frase)
- 🛑 **Barge-in nativo** — interrumpís al robot hablando encima, se calla al toque
- 🔌 **Adapter pattern puro** — swapeás proveedores editando un YAML, cero `if provider ==`
- ☁️ **Dual-mode cloud / local** — misma interfaz, Deepgram/Groq/Cartesia o whisper.cpp/Ollama/Piper
- 🔥 **Pre-warming de WebSockets** — la primera respuesta no paga el handshake
- 📊 **Cost tracker + métricas JSONL** — p50/p95 y costo por tier con un script
- 🐱 **Wake word "Hola Michi"** — entrenable, on-device vía microWakeWord en Week 2
- 🤖 **Un solo chip** — ESP32-S3 maneja audio + WiFi + servos + display (Week 2-3)

---

## 🏗️ Arquitectura

```
┌──────────────────── DEVICE (ESP32-S3 — Week 2-3) ───────────────────┐
│                                                                       │
│   mic I2S ──▶ ESP-SR AFE ──▶ microWakeWord("Hola Michi")             │
│                                          │                            │
│                                          ▼                            │
│                                  ACK filler <50 ms                    │
│                                          │                            │
│                                          ▼                            │
│                               WebSocket client                        │
│   speaker I2S ◀── audio playback ◀── WebSocket client                 │
│   display GC9A01 ◀── face animation ◀── event handler                 │
│   servos (portados del STM32 original)                                │
└────┬──────────────────────────────────────────────────────────────────┘
     │   WebSocket binario (PCM 16 kHz) + JSON control
     ▼
┌──────────────────── BACKEND (FastAPI · async) ───────────────────────┐
│                                                                        │
│   ┌───────────────────────────────────────────────────────┐           │
│   │         VoiceSessionOrchestrator                      │           │
│   │    STT stream ──▶ Intent Router ──▶ response stream   │           │
│   │                     │   │   │                          │           │
│   │                     ▼   ▼   ▼                           │           │
│   │              ┌─────────────────────────────┐            │           │
│   │              │  T1  Canned     ~15 ms      │  ~40 %    │           │
│   │              │  T2  Template   ~235 ms     │  ~30 %    │           │
│   │              │  T3  LLM full   ~586 ms     │  ~30 %    │           │
│   │              └─────────────────────────────┘            │           │
│   └───────────────────────────────────────────────────────┘           │
│                  ▲            ▲            ▲                            │
│                  │            │            │                            │
│              Deepgram      Groq 70B     Cartesia                        │
│              Nova-3        500 tok/s    Sonic                           │
└────────────────────────────────────────────────────────────────────────┘
```

### El truco está en el router

Cada turno pasa primero por un **Intent Router** que clasifica en 3 tiers:

| Tier | Cuándo dispara | Qué ejecuta | Costo |
|------|----------------|-------------|-------|
| **T1** | Match regex contra ~18 frases canned (saludos, cortesías, afirmaciones) | Lee un WAV del disco y lo stremea al cliente | **$0** |
| **T2** | Match contra handlers de template (ej: "¿qué hora es?") | Genera texto con datos locales, manda a TTS streaming | Solo TTS |
| **T3** | Fallback: cualquier cosa que no matchee T1/T2 | Pipeline completo STT → Groq Llama 70B → Cartesia con speculative TTS | Los 3 |

El ~70 % de los turnos de una conversación real caen en T1/T2. Por eso la
latencia percibida promedio es muchísimo más baja que el peor caso.

---

## 🚀 Quick Start

### Prerrequisitos

- **Python 3.11+**
- **[uv](https://github.com/astral-sh/uv)** — gestor de dependencias rápido
- Micrófono y parlantes funcionando
- 3 API keys (las 3 tienen free tier):
  - [Deepgram](https://deepgram.com) · STT Nova-3 español LATAM
  - [Groq](https://console.groq.com) · LLM Llama 3.3 70B
  - [Cartesia](https://cartesia.ai) · TTS Sonic

### Instalación

```bash
git clone https://github.com/<tu-user>/michibot.git
cd michibot
uv sync
cp env.example.txt .env
# Editá .env y pegá las 3 API keys
```

### Correr

```bash
# Terminal 1 — backend
uv run uvicorn electronbot_es.server.app:app --host 127.0.0.1 --port 8000

# Terminal 2 — cliente mock con wake word
uv run python -m electronbot_es.mock.mock_esp32 --wake-word

# Decí "hey jarvis" (placeholder — el real "Hola Michi" llega en Week 2)
```

### Ver métricas

```bash
uv run python scripts/metrics.py --last 20
```

<details>
<summary><b>Ejemplo de salida</b></summary>

```
=== Michibot metrics — 20 turns ===

tier    n     %   first_audio p50   p95   tts_first p50   p95    avg_cost   total
---------------------------------------------------------------------------------
T1      8   40%            15 ms   30 ms          15 ms   30 ms  $0.000000 $0.0000
T2      6   30%           235 ms  280 ms         235 ms  280 ms  $0.001878 $0.0113
T3      6   30%           586 ms  820 ms         586 ms  820 ms  $0.006514 $0.0391
---------------------------------------------------------------------------------
total cost: $0.0504   avg/turn: $0.002520
T3 tokens avg: in=225  out=41
T1+T2 match rate: 70%  (target >60%)
```

</details>

---

## 🧱 Estructura del repo

```
michibot/
├── src/electronbot_es/
│   ├── core/
│   │   ├── orchestrator.py     # VoiceSessionOrchestrator + speculative TTS
│   │   ├── cost.py             # Estimación de costo por turno
│   │   ├── obs.py              # Logger JSONL de métricas
│   │   ├── messages.py         # Schemas pydantic del protocolo WebSocket
│   │   ├── protocols.py        # STT / LLM / TTS como Protocol
│   │   ├── persona.py          # System prompt LATAM del LLM
│   │   └── config.py
│   ├── router/
│   │   ├── intent_router.py    # Decisión T1 / T2 / T3
│   │   ├── canned_responses.yaml
│   │   └── templates/          # Handlers T2 (hora, clima, etc.)
│   ├── adapters/
│   │   ├── stt_deepgram.py      ·  stt_whisper.py
│   │   ├── llm_groq.py          ·  llm_claude.py   ·  llm_ollama.py
│   │   └── tts_cartesia.py      ·  tts_piper.py
│   ├── server/
│   │   └── app.py              # FastAPI + /ws/voice
│   └── mock/
│       ├── mock_esp32.py       # Cliente de desarrollo (mic + speaker + WS)
│       └── wake_word.py        # openWakeWord wrapper
├── docs/
│   ├── protocol.md             # Contrato WebSocket v1 (inmutable)
│   ├── roadmap.md              # Weeks 2-12+
│   ├── hardware.md             # Decisiones de hardware
│   ├── cloud-providers.md      # Setup Deepgram / Groq / Cartesia
│   └── llm-benchmark.md        # Benchmark español LATAM
├── assets/canned/              # WAVs pre-sintetizados (T1)
├── scripts/                    # generate_canned, metrics, benchmarks, ...
└── tests/                      # pytest · 40+ tests
```

---

## 🛠️ Stack técnico

<table>
<tr><td>

**Backend**
- FastAPI + Uvicorn (async)
- Pydantic v2 (schemas WS)
- structlog / JSONL
- pytest + pytest-asyncio

</td><td>

**Cloud providers**
- Deepgram Nova-3 (STT)
- Groq Llama 3.3 70B (LLM)
- Cartesia Sonic (TTS)
- Anthropic Haiku (fallback)

</td><td>

**Local / dev**
- whisper.cpp (STT)
- Ollama Llama 3.2 3B (LLM)
- Piper es_MX (TTS)
- openWakeWord (baseline)

</td></tr>
<tr><td>

**Device (Week 2+)**
- ESP32-S3 (≥8 MB PSRAM)
- ESP-IDF 5.x
- ESP-SR AFE (AEC + VAD)
- microWakeWord (TFLM)

</td><td>

**Mobile (Week 7+)**
- React Native + Expo
- Supabase (auth + DB)
- RevenueCat (subs)

</td><td>

**Tooling**
- uv (deps)
- ruff + mypy
- GitHub Actions (CI)

</td></tr>
</table>

---

## 📈 Roadmap

| Semana | Objetivo | Estado |
|-------:|----------|:------:|
| **W1** | Backend + mock + router + observabilidad |  ✅ |
| **W2** | Firmware ESP32-S3 + wake word real "Hola Michi" |  🔜 |
| **W3** | Servos + display GC9A01 + personalidad física |  ⏳ |
| **W4** | Integración end-to-end + aceptación MVP |  ⏳ |
| **W5-6** | Multi-tenant (Supabase + auth + billing) |  ⏳ |
| **W7-8** | App móvil React Native + Expo |  ⏳ |
| **W9** | Deploy producción + stores + beta cerrada |  ⏳ |
| **W10-12** | Iteración + launch público |  ⏳ |

Detalles semana por semana en [`docs/roadmap.md`](docs/roadmap.md).

---

## 📚 Documentación

- [`docs/protocol.md`](docs/protocol.md) · Contrato WebSocket (inmutable)
- [`docs/hardware.md`](docs/hardware.md) · Por qué ESP32-S3 único y por qué no LLM on-device
- [`docs/cloud-providers.md`](docs/cloud-providers.md) · Setup de las 3 API keys
- [`docs/roadmap.md`](docs/roadmap.md) · Plan semana por semana hasta launch
- [`docs/llm-benchmark.md`](docs/llm-benchmark.md) · Comparativa de LLMs en español LATAM

---

## 🤝 Contribuir

Michibot es un proyecto en desarrollo activo — Week 1 recién cerró. Si querés
aportar:

1. **Issues** — reportá bugs, pedí features, comentá ideas
2. **Benchmarks** — si tenés una GPU decente y querés correr el modo local,
   mandá tus números
3. **Canned responses LATAM** — falta ampliar el catálogo T1 con más variantes
   regionales (Argentina, México, Colombia, Chile, Perú…)
4. **Voces TTS** — experimentar con Cartesia Voice Cloning para acentos
   regionales (la voz actual es neutro LATAM)

---

## 📜 Licencia

MIT. Hecho con cariño en LATAM.

<div align="center">

---

*Si Michibot te parece interesante, tirale una ⭐ — ayuda un montón.*

</div>
