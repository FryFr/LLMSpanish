# Roadmap — Weeks 1 a 12+

Este documento es un resumen ejecutivo. El plan detallado (con tareas
diarias, prerequisitos, riesgos y verificación por semana) vive en engram
bajo `electronbot-es/architecture/full-plan` y en los checkpoints por
semana (`electronbot-es/roadmap/week-N`).

---

## Week 1 — Backend + Mock ✅

Fundación del producto: backend FastAPI async con adapters para cloud
(Deepgram/Groq/Cartesia) y local (whisper.cpp/Ollama/Piper), orquestador
con streaming end-to-end, Intent Router 3-tier, pre-warming de Cartesia,
cost tracker, wake word baseline con openWakeWord y cliente mock para
desarrollo en PC. Observabilidad JSONL + script de métricas.

**Entregable**: conversación real en <900 ms (p50) en modo cloud.

## Week 2 — Firmware ESP32-S3 + Wake Word custom

- Pipeline de audio en ESP-IDF (I2S + AFE + wake word)
- Training de microWakeWord con "Hola Michi" (dataset propio, ~100 samples)
- Cliente WebSocket en ESP-IDF compatible con el protocolo de Week 1
- Integración end-to-end sobre WiFi apuntando al backend local

**Deferred priorities** (guardadas en engram):

1. **Wake word "Hola Michi" real** — Week 1 usa `hey_jarvis` como placeholder,
   Week 2 entrena el modelo de verdad.
2. **Voz con acento paisa** — Week 1 usa Juanita (neutro LATAM); al
   comercializar en Colombia conviene clonar una voz paisa con ElevenLabs
   o Cartesia Voice Cloning.

## Week 3 — Servos + Display + personalidad física

- Port del firmware de servos STM32 → ESP32-S3 (ESP-IDF)
- Driver del display GC9A01
- Face animations estilo "gato minimalista" (idle / listening / thinking / talking)
- Movimientos coordinados con estados de conversación

## Week 4 — Integración + validación MVP

- Juntar backend + firmware + cuerpo
- Stress testing (30 min continuos, múltiples distancias, ruido)
- Tuning de UX (timing animaciones vs audio, ACK filler, threshold del wake)
- Aceptación: p50 wake-to-first-audio <1.5 s con cloud, 0 crashes en 30 min

## Week 5 — Backend multi-tenant + Supabase

- Schema de DB (`users`, `devices`, `conversations`, `turns`)
- Auth real con JWT de Supabase
- REST API para la app móvil
- Persistencia de conversaciones + rate limiting por quota
- Device pairing protocol
- Deploy a staging

## Week 6 — Pagos + RevenueCat + billing end-to-end

- RevenueCat + Stripe sandbox
- Webhook → backend → actualización de tier
- Quotas dinámicas por tier (Free 50, Basic 500, Pro ilimitado)
- Endpoints para la app

## Week 7 — App móvil: auth + pairing

- React Native + Expo + TypeScript
- Auth con Supabase
- Home + profile
- Device pairing flow (BLE o AP mode)

## Week 8 — App móvil: subscripciones + historial + settings

- Integración RevenueCat SDK
- Paywall + onboarding
- Historial de conversaciones
- Settings del robot (volumen, sensibilidad, personalidad)

## Week 9 — Deploy producción + stores + beta cerrada

- Backend en producción (Railway / Fly.io / Hetzner)
- Monitoring (Sentry + métricas)
- TestFlight + Google Play Internal Testing
- Legal básico (privacy policy, TOS)
- 5-10 beta testers iniciales

## Weeks 10-12 — Iteración + launch público

- Iteración con feedback de beta
- Landing page + marketing assets
- Launch público en stores
- Campaña inicial (Reddit / HN / LinkedIn / medios DIY en español)

---

## Backlog post-launch

- Fine-tuning del LLM con conversaciones reales (con consent)
- Multi-idioma (inglés, portugués BR)
- Home Assistant integration
- Skills / plugins para desarrolladores externos
- Modo familiar (múltiples usuarios por dispositivo con voice recognition)
- Versión solo-software (app móvil "que es Michi" sin hardware)
