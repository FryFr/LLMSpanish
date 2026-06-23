# Búsqueda web vía tool-calling (T3)

**Fecha:** 2026-06-22
**Estado:** Aprobado, pendiente de plan de implementación
**Proyecto:** Michibot (electronbot-es) — Frente B+ (capacidad: "averiguar" info actual)

## Problema

El bot no puede responder sobre información en tiempo real (clima de hoy, noticias,
precios, resultados actuales) porque solo tiene el conocimiento paramétrico de
Llama 3.3 70B. Para esos casos necesita **buscar en la web**. El adapter de LLM
actual ([llm_groq.py](../../../src/electronbot_es/adapters/llm_groq.py)) es solo
streaming de texto, sin tool-calling.

> Nota: el problema del bot diciendo "no sé" para cosas que SÍ sabe se resolvió
> aparte (regla del persona). Esta spec cubre solo la info genuinamente actual.

## Objetivo

Que el bot, en el tier T3, decida por sí mismo cuándo buscar en la web, ejecute la
búsqueda y responda incorporando los resultados — sin romper el pipeline de
speculative TTS ni regresar la latencia de los turnos que NO necesitan buscar.

## Decisiones de diseño (aprobadas)

- **Cuándo buscar:** el modelo decide vía **tool-calling** (no heurística, no
  clasificador). Con tool-calling en streaming, el turno común (sin búsqueda)
  streamea la respuesta directo y casi no paga latencia extra; solo los turnos que
  buscan pagan el costo.
- **Proveedor:** **Tavily** (snippets ya resumidos, listos para el modelo; free
  tier). Queda detrás de un `SearchAdapter` para poder swapear.
- **Alcance:** **un solo search por turno**. Sin multi-hop, sin leer páginas
  completas, sin búsquedas de seguimiento.

## Arquitectura

```
T3: STT → SearchAugmentedResponder ──► speculative TTS (sin cambios)
                 │ usa
                 ├─ GroqLLM.stream_with_tools(...)  (primitivo tool-aware)
                 └─ SearchAdapter → TavilySearch
```

El `SearchAugmentedResponder` expone `respond_stream(messages) -> AsyncIterator[str]`,
la MISMA forma que `LLMAdapter.generate_stream` hoy, así el orquestador y el
speculative TTS no cambian su lógica de consumo.

## Componentes

### 1. `SearchAdapter` (Protocol) + `TavilySearch`

En [protocols.py](../../../src/electronbot_es/core/protocols.py), nuevo Protocol
siguiendo el patrón STT/LLM/TTS:

```python
@runtime_checkable
class SearchAdapter(Protocol):
    async def search(self, query: str) -> str: ...   # texto limpio para el modelo
    async def aclose(self) -> None: ...
```

`adapters/search_tavily.py` — `TavilySearch`: llama a la API de Tavily vía `httpx`
(ya es dependencia), devuelve un resumen de texto (el campo `answer` de Tavily +
los snippets top, concatenados). Timeout configurable (~3s). API key desde
`TAVILY_API_KEY`.

### 2. Primitivo tool-aware en el adapter de Groq

`GroqLLM` gana un método que streamea eventos tipados:

```python
async def stream_with_tools(
    self, messages: list[ChatMessage], tools: list[dict]
) -> AsyncIterator[TextDelta | ToolCallRequest]: ...
```

- `TextDelta(text: str)` — el modelo está respondiendo directo (no buscó).
- `ToolCallRequest(id: str, name: str, arguments: dict)` — el modelo pide buscar;
  se acumulan los deltas de `tool_calls` hasta `finish_reason == "tool_calls"` y se
  parsea el JSON de argumentos.

`TextDelta` y `ToolCallRequest` son dataclasses nuevas (en protocols.py o un módulo
de tipos del LLM). El `generate_stream` existente NO se toca (compat con los
adapters locales que no soportan tools).

### 3. `SearchAugmentedResponder` (core/agentic.py)

Orquesta el loop de un solo paso:

1. `stream_with_tools(messages, [SEARCH_TOOL])`.
2. Si el primer evento es `TextDelta` → el modelo respondió directo: yield de todos
   los text deltas. Fin. (Caso común, latencia ≈ hoy.)
3. Si es `ToolCallRequest` → acumular la (única) tool call, ejecutar
   `search.search(query)`, agregar a `messages` el mensaje assistant-con-tool-call
   y el mensaje tool-result, y volver a llamar `stream_with_tools(messages, [])`
   (sin tools la 2da vuelta) → yield de los text deltas de la respuesta final.

`SEARCH_TOOL` es el JSON-schema de la herramienta `buscar_web(query: str)`.

### 4. Integración en el orquestador (T3)

En [orchestrator.py](../../../src/electronbot_es/core/orchestrator.py), el tramo T3
usa el responder en vez de `llm.generate_stream` directo, **cuando search está
habilitado**. Si no hay `TAVILY_API_KEY`, el responder no se construye y T3 cae al
`generate_stream` de siempre (cero regresión). El sentence-buffer + speculative TTS
quedan igual.

### 5. UX: enmascarar la latencia de búsqueda

Cuando el responder dispara una búsqueda, el orquestador emite
`LlmStatus(state="searching")` (estado nuevo del mensaje `llm.status` ya existente;
el cliente lo muestra/usa para un filler "dejame ver…" o animación de cara). Los
clientes que no lo conozcan lo ignoran. Esto NO rompe el protocolo v1 (solo agrega
un valor de estado).

## Manejo de errores (degradación elegante)

- **Sin `TAVILY_API_KEY`** → search deshabilitado; T3 funciona como hoy.
- **Tavily timeout (~3s) o error** → el responder NO cuelga el turno. Como la API
  exige un tool-result después de cada tool-call, se agrega un tool-result con
  contenido tipo "búsqueda no disponible ahora mismo" y se hace la 2da llamada
  igual; el modelo responde con lo que sabe y avisa que no pudo averiguarlo. El
  turno siempre termina.
- **El modelo pide una tool desconocida** → se ignora y se trata como respuesta
  directa.

## Latencia esperada

- Turno T3 sin búsqueda: ≈ hoy (~586ms first audio) + overhead mínimo del schema.
- Turno T3 con búsqueda: ~1.5–2.5s (decisión + Tavily ~0.5–1s + 2da llamada LLM),
  enmascarado por el filler "searching".

## Testing

- `TavilySearch`: test con cliente HTTP mockeado (respuesta Tavily fake) → verifica
  parseo del resumen y manejo de timeout/error.
- `SearchAugmentedResponder`: con un LLM fake (scripteable para emitir `TextDelta`
  o `ToolCallRequest`) y un `SearchAdapter` fake:
  - caso sin búsqueda → streamea directo, NO llama a search.
  - caso con búsqueda → llama a search con el query correcto, reinyecta y streamea
    la respuesta final.
  - caso search falla → el turno termina igual con una respuesta del modelo.
- Mismo patrón de fakes determinísticos que `FakeVAD`. Sin red en los tests.

## Fuera de alcance (YAGNI)

- Multi-hop / múltiples búsquedas por turno.
- Leer páginas completas (scraping).
- Cache de resultados.
- Cost tracking detallado del search (se puede sumar después a cost.py).
- Cambios al protocolo más allá del valor de estado "searching".

## Criterios de éxito

1. Pregunta de info actual ("¿qué tiempo hace hoy en Bogotá?") → el bot busca y
   responde con datos de la búsqueda, no "no sé".
2. Pregunta de conocimiento general ("¿capital de Australia?") → responde directo
   SIN buscar (no se dispara la tool).
3. Sin `TAVILY_API_KEY` → T3 sigue funcionando exactamente como hoy.
4. Tavily caído → el turno termina con una respuesta, no se cuelga.
5. Tests del responder y del adapter pasan sin red.
