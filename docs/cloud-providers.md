# Cloud providers — setup

Las 3 keys son **obligatorias** para el modo cloud. Las 3 tienen free tier
suficiente para Week 1 (~$0 de costo real en desarrollo).

## Deepgram — STT

- **Modelo**: Nova-3 streaming, `es-419` (español LATAM)
- **Latencia típica**: 150–250 ms
- **Precio**: $0.0043/min
- **Free tier**: $200 USD de crédito al crear la cuenta

Signup: https://deepgram.com
Key: Dashboard → API Keys → Create key
Pegar en `.env` como `DEEPGRAM_API_KEY`.

## Groq — LLM

- **Modelo**: `llama-3.3-70b-versatile`
- **Latencia típica**: primer token 200–400 ms, ~500 tok/s en generación
- **Precio**: $0.59 / M input tokens, $0.79 / M output tokens
- **Free tier**: generoso, varios miles de llamadas/día

Signup: https://console.groq.com
Key: API Keys → Create API Key
Pegar en `.env` como `GROQ_API_KEY`.

## Cartesia — TTS

- **Modelo**: Sonic, voz Juanita (español LATAM femenino)
- **Latencia típica**: first chunk ~100-200 ms (con pre-warming ~50 ms)
- **Precio**: ~$65 / 1 M characters (~$0.02/min hablados)
- **Free tier**: para desarrollo

Signup: https://cartesia.ai
Key: Dashboard → API Keys
Pegar en `.env` como `CARTESIA_API_KEY`.

Nota: el **pre-warming** del WebSocket a Cartesia es crítico para el T2.
Sin él, la primera respuesta de un template tarda ~935 ms; con él baja a
~235 ms. Está implementado en [`src/electronbot_es/adapters/tts_cartesia.py`](../src/electronbot_es/adapters/tts_cartesia.py).

## Anthropic (opcional, fallback)

- **Modelo**: Claude Haiku 4.5 — mejor calidad en español que Groq, un
  poco más lento, más caro. Se usa como fallback cuando Groq falle.
- **Precio**: $1 / M input, $5 / M output
- Pegar en `.env` como `ANTHROPIC_API_KEY`.

## Formato del `.env`

```dotenv
DEEPGRAM_API_KEY=...
GROQ_API_KEY=...
CARTESIA_API_KEY=...
ANTHROPIC_API_KEY=...   # opcional
```

## Costo esperado en Week 1

Con desarrollo normal (~200 turnos de prueba en la semana), el costo real
debería ser **$0** — todo absorbido por los free tiers. Si lo supera hay
que revisar si hay un loop infinito llamando al LLM.
