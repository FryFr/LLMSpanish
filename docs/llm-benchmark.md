# LLM Benchmark — Spanish LATAM (Day 3)

Prompts: 10 fixed conversational turns with Michi persona + 3 few-shots.
Metrics are per-model aggregates across all prompts.

| Model | p50 first token | p50 total | avg tok/s | avg out tok | avg cost/turn |
|---|---|---|---|---|---|
| Groq Llama 3.3 70B | 320 ms | 413 ms | 50.6 | 23 | $0.000065 |
| Ollama Llama 3.2 1B | 272 ms | 1319 ms | 17.7 | 26 | $0.000000 |
| Ollama Qwen 2.5 3B | 514 ms | 1406 ms | 6.5 | 10 | $0.000000 |

## Sample outputs (first prompt only)

- **Groq Llama 3.3 70B**: _Estoy bien, gracias. ¿Y tú, cómo te va hoy?_
- **Ollama Llama 3.2 1B**: _Estoy bien, gracias por preguntar. ¿Te gustaría saber qué hay de nuevo aquí?_
- **Ollama Qwen 2.5 3B**: _¡Hola! Hoy estoy como siempre, listo para ayudarte. ¿En qué puedo apoyarte hoy?_

## Winners

- 🏆 **Cloud (production)**: **Groq Llama 3.3 70B** — 320 ms p50 first token, 413 ms p50 total, 50 tok/s, neutral LATAM Spanish across all 10 prompts, cost ~$0.065 per 1000 turns. Well under the 400 ms first-token target from the plan. No close second; Claude Haiku 4.5 is the configured fallback once `ANTHROPIC_API_KEY` is provided.
- 🏆 **Local (dev / privacy fallback)**: **Ollama Llama 3.2 1B** — 272 ms p50 first token (excluding cold load), 1319 ms p50 total, 17.7 tok/s on the desktop GTX 1650 Ti. Surprisingly beats Qwen 2.5 3B in both speed AND coherence on this hardware. Re-benchmark when the MacBook M5 arrives with 8B+ models available.

## Notes & rejected candidates

- **Qwen 2.5 3B was rejected** — on the GTX 1650 Ti it is VRAM-constrained and produced both slower (6.5 tok/s) AND lower-quality output than the smaller Llama 1B. Concrete errors in our 10-prompt run:
  - "El caimán tiene cuernos" (wrong; caimans do not have horns)
  - "Pinta tacos" as a recipe answer (incoherent)
  - Several truncated ≤5-token replies when a 2–3 sentence answer was expected
- **Llama 3.2 1B has factual weaknesses**: it answered caimán vs cocodrilo with "Ambos son serpientes venenosas" — they are reptiles, not snakes. Acceptable for dev / fallback because the **cloud path is the product**; local mode exists for offline work and future privacy opt-in, not production quality.
- **Cold-load penalty**: Ollama's first prompt on each model took 3–5 s extra while the weights paged into VRAM. Subsequent prompts were ~270–560 ms for first token. The Day 5 pre-warming pass will keep the active local model hot on boot.
- **Groq latency from LATAM**: the 320 ms p50 already includes the LATAM → US-East network hop. With persistent connections (Day 5 pre-warming) we expect this to drop another 50–100 ms in the production path.

## Configuration used

- Persona: `src/electronbot_es/core/persona.py` — system prompt enforcing neutral LATAM Spanish, 2-3 sentences max, no markdown, friendly cat personality; 3 few-shot examples.
- Settings: `temperature=0.6`, `max_tokens=512`.
- Prompts: 10 fixed conversational turns covering greetings, recommendations, explanations, jokes, preferences, recipes, advice, factual, creative, opinion. See `scripts/bench_llm.py::PROMPTS`.
