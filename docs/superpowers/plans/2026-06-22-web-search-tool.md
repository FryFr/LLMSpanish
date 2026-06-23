# Búsqueda web vía tool-calling — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el bot, en T3, decida vía tool-calling cuándo buscar en la web (Tavily), ejecute la búsqueda y responda con los resultados, sin romper el speculative TTS ni regresar la latencia de los turnos sin búsqueda.

**Architecture:** Un `SearchAdapter` (Tavily) detrás del patrón de adapters; un primitivo tool-aware en `GroqLLM` que streamea texto o un pedido de tool-call; un `SearchAugmentedResponder` (core) que corre el loop de un paso y expone un `AsyncIterator[str]` idéntico al del LLM de hoy, así el orquestador no cambia su consumo.

**Tech Stack:** Python 3.12, httpx (ya dependencia), groq SDK (tools, OpenAI-compatible), pytest. Tests async vía `asyncio.run()` en funciones sync (el repo no usa pytest-asyncio markers).

---

## Estructura de archivos

- **Modificar** `src/electronbot_es/core/protocols.py` — `SearchAdapter` Protocol + dataclasses `TextDelta` y `ToolCallRequest`.
- **Crear** `src/electronbot_es/adapters/search_tavily.py` — `TavilySearch`.
- **Crear** `tests/test_search_tavily.py`.
- **Modificar** `src/electronbot_es/adapters/llm_groq.py` — `_events_from_chunks` + `GroqLLM.stream_with_tools`.
- **Crear** `tests/test_llm_groq_tools.py`.
- **Crear** `src/electronbot_es/core/agentic.py` — `SearchAugmentedResponder` + `SEARCH_TOOL`.
- **Crear** `tests/test_agentic.py`.
- **Modificar** `src/electronbot_es/core/config.py` — `tavily_api_key`.
- **Modificar** `src/electronbot_es/core/messages.py` — estado `"searching"` en `LlmStatus`.
- **Modificar** `tests/test_ws_protocol.py` — incluir `"searching"`.
- **Modificar** `src/electronbot_es/core/orchestrator.py` — usar el responder en T3.
- **Modificar** `src/electronbot_es/server/app.py` — cablear Tavily + responder si hay key.

---

## Task 1: SearchAdapter Protocol + TavilySearch

**Files:**
- Modify: `src/electronbot_es/core/protocols.py`
- Create: `src/electronbot_es/adapters/search_tavily.py`
- Test: `tests/test_search_tavily.py`

- [ ] **Step 1: Agregar el Protocol**

En `src/electronbot_es/core/protocols.py`, al final del archivo, agregar:

```python
@runtime_checkable
class SearchAdapter(Protocol):
    async def search(self, query: str) -> str: ...

    async def aclose(self) -> None: ...
```

- [ ] **Step 2: Escribir el test que falla**

Crear `tests/test_search_tavily.py`:

```python
"""Tests de TavilySearch — parseo del resumen y manejo de errores, sin red."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from electronbot_es.adapters.search_tavily import TavilySearch


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(coro):
    return asyncio.run(coro)


def test_summarizes_answer_and_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "answer": "Hace 20°C en Bogotá.",
                "results": [
                    {"content": "Bogotá: 20°C, nublado."},
                    {"content": "Pronóstico estable."},
                ],
            },
        )

    s = TavilySearch(api_key="x", client=_client(handler))
    out = _run(s.search("clima bogota"))
    assert "20°C en Bogotá" in out
    assert "nublado" in out


def test_empty_results_returns_sentinel() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"answer": "", "results": []})

    s = TavilySearch(api_key="x", client=_client(handler))
    out = _run(s.search("nada"))
    assert out == "Sin resultados."


def test_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    s = TavilySearch(api_key="x", client=_client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        _run(s.search("x"))
```

- [ ] **Step 3: Correr el test y verificar que falla**

Run: `uv run pytest tests/test_search_tavily.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'electronbot_es.adapters.search_tavily'`

- [ ] **Step 4: Implementar el adapter**

Crear `src/electronbot_es/adapters/search_tavily.py`:

```python
"""TavilySearch — SearchAdapter sobre la API de Tavily.

Devuelve un resumen de texto listo para inyectar al LLM: el campo `answer`
de Tavily más el contenido de los primeros resultados. El cliente httpx es
inyectable para testear sin red.
"""

from __future__ import annotations

from typing import Optional

import httpx

TAVILY_URL = "https://api.tavily.com/search"


def _summarize(data: dict) -> str:
    parts: list[str] = []
    answer = (data.get("answer") or "").strip()
    if answer:
        parts.append(answer)
    for r in (data.get("results") or [])[:3]:
        snippet = (r.get("content") or "").strip()
        if snippet:
            parts.append(f"- {snippet}")
    return "\n".join(parts) if parts else "Sin resultados."


class TavilySearch:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_s: float = 3.0,
        max_results: int = 3,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._api_key = api_key
        self._max_results = max_results
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_client = client is None

    async def search(self, query: str) -> str:
        resp = await self._client.post(
            TAVILY_URL,
            json={
                "api_key": self._api_key,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "max_results": self._max_results,
            },
        )
        resp.raise_for_status()
        return _summarize(resp.json())

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `uv run pytest tests/test_search_tavily.py -q`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/electronbot_es/core/protocols.py src/electronbot_es/adapters/search_tavily.py tests/test_search_tavily.py
git commit -m "feat: add SearchAdapter protocol and TavilySearch"
```

---

## Task 2: Tool-aware streaming en GroqLLM

**Files:**
- Modify: `src/electronbot_es/core/protocols.py`
- Modify: `src/electronbot_es/adapters/llm_groq.py`
- Test: `tests/test_llm_groq_tools.py`

- [ ] **Step 1: Agregar los tipos de evento**

En `src/electronbot_es/core/protocols.py`, junto a las otras dataclasses (después de `ChatMessage`), agregar:

```python
@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict
```

- [ ] **Step 2: Escribir el test que falla**

Crear `tests/test_llm_groq_tools.py`:

```python
"""Tests de _events_from_chunks — acumulación de tool-calls, sin red."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from electronbot_es.adapters.llm_groq import _events_from_chunks
from electronbot_es.core.protocols import TextDelta, ToolCallRequest


def _collect(agen):
    async def run():
        return [x async for x in agen]

    return asyncio.run(run())


async def _aiter(items):
    for it in items:
        yield it


def _chunk(content=None, tool_calls=None, finish_reason=None):
    delta = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(delta=delta, finish_reason=finish_reason)])


def test_text_chunks_become_text_deltas() -> None:
    chunks = [
        _chunk(content="Hola"),
        _chunk(content=" mundo"),
        _chunk(finish_reason="stop"),
    ]
    out = _collect(_events_from_chunks(_aiter(chunks)))
    assert out == [TextDelta(text="Hola"), TextDelta(text=" mundo")]


def test_tool_call_deltas_accumulate_into_one_request() -> None:
    tc1 = NS(id="call_1", function=NS(name="buscar_web", arguments='{"query":'))
    tc2 = NS(id=None, function=NS(name=None, arguments='"clima hoy"}'))
    chunks = [
        _chunk(tool_calls=[tc1]),
        _chunk(tool_calls=[tc2]),
        _chunk(finish_reason="tool_calls"),
    ]
    out = _collect(_events_from_chunks(_aiter(chunks)))
    assert out == [
        ToolCallRequest(id="call_1", name="buscar_web", arguments={"query": "clima hoy"})
    ]
```

- [ ] **Step 3: Correr el test y verificar que falla**

Run: `uv run pytest tests/test_llm_groq_tools.py -q`
Expected: FAIL con `ImportError: cannot import name '_events_from_chunks'`

- [ ] **Step 4: Implementar el transform y el método**

En `src/electronbot_es/adapters/llm_groq.py`, agregar el import de `json` arriba y los tipos:

```python
import json
```

y al import de protocols:

```python
from electronbot_es.core.protocols import ChatMessage, TextDelta, ToolCallRequest
```

Agregar la función pura a nivel de módulo (después de los imports, antes de la clase):

```python
async def _events_from_chunks(chunks):
    """Convierte el stream de chunks de Groq en TextDelta / ToolCallRequest.

    Acumula los deltas de tool_calls (id + name + fragmentos de arguments)
    hasta finish_reason == 'tool_calls', parsea el JSON y emite un único
    ToolCallRequest. Los chunks de texto se emiten como TextDelta.
    """
    tool_id = None
    tool_name = None
    tool_args = ""
    saw_tool = False
    async for chunk in chunks:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        content = getattr(delta, "content", None)
        if content:
            yield TextDelta(text=content)
        tcs = getattr(delta, "tool_calls", None)
        if tcs:
            saw_tool = True
            for tc in tcs:
                if getattr(tc, "id", None):
                    tool_id = tc.id
                fn = getattr(tc, "function", None)
                if fn is not None:
                    if getattr(fn, "name", None):
                        tool_name = fn.name
                    if getattr(fn, "arguments", None):
                        tool_args += fn.arguments
        if choice.finish_reason == "tool_calls" and saw_tool:
            try:
                args = json.loads(tool_args) if tool_args else {}
            except json.JSONDecodeError:
                args = {}
            yield ToolCallRequest(id=tool_id or "", name=tool_name or "", arguments=args)
            return
```

Agregar el método a la clase `GroqLLM` (después de `generate_stream`):

```python
    async def stream_with_tools(self, messages: list[dict], tools: list[dict]):
        """Streamea eventos tipados. messages son dicts en formato OpenAI."""
        kwargs = dict(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            stream=True,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        stream = await self._client.chat.completions.create(**kwargs)
        async for ev in _events_from_chunks(stream):
            yield ev
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `uv run pytest tests/test_llm_groq_tools.py -q`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add src/electronbot_es/core/protocols.py src/electronbot_es/adapters/llm_groq.py tests/test_llm_groq_tools.py
git commit -m "feat: add tool-aware streaming primitive to GroqLLM"
```

---

## Task 3: SearchAugmentedResponder

**Files:**
- Create: `src/electronbot_es/core/agentic.py`
- Test: `tests/test_agentic.py`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_agentic.py`:

```python
"""Tests del SearchAugmentedResponder con LLM y search fakes, sin red."""

from __future__ import annotations

import asyncio

from electronbot_es.core.agentic import SearchAugmentedResponder
from electronbot_es.core.protocols import ChatMessage, TextDelta, ToolCallRequest


def _collect(agen):
    async def run():
        return [x async for x in agen]

    return asyncio.run(run())


class FakeToolLLM:
    """Devuelve una secuencia de eventos scripteada por cada llamada sucesiva."""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = []

    def stream_with_tools(self, messages, tools):
        self.calls.append((messages, tools))
        events = self._scripts.pop(0)

        async def gen():
            for ev in events:
                yield ev

        return gen()


class FakeSearch:
    def __init__(self, result="RESULTADO", fail=False):
        self._result = result
        self._fail = fail
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        if self._fail:
            raise RuntimeError("boom")
        return self._result

    async def aclose(self):
        pass


MSGS = [ChatMessage(role="user", content="hola")]


def test_no_search_streams_directly() -> None:
    llm = FakeToolLLM([[TextDelta(text="Capital "), TextDelta(text="de Australia: Canberra.")]])
    search = FakeSearch()
    out = _collect(SearchAugmentedResponder(llm=llm, search=search).respond_stream(MSGS))
    assert "".join(out) == "Capital de Australia: Canberra."
    assert search.queries == []
    assert len(llm.calls) == 1


def test_search_then_final_answer() -> None:
    llm = FakeToolLLM([
        [ToolCallRequest(id="c1", name="buscar_web", arguments={"query": "clima bogota hoy"})],
        [TextDelta(text="Hoy en Bogotá: "), TextDelta(text="20°C.")],
    ])
    search = FakeSearch(result="Bogotá 20°C nublado")
    out = _collect(SearchAugmentedResponder(llm=llm, search=search).respond_stream(MSGS))
    assert "".join(out) == "Hoy en Bogotá: 20°C."
    assert search.queries == ["clima bogota hoy"]
    second_msgs = llm.calls[1][0]
    assert any(
        m.get("role") == "tool" and "20°C" in (m.get("content") or "")
        for m in second_msgs
    )


def test_search_failure_still_answers() -> None:
    llm = FakeToolLLM([
        [ToolCallRequest(id="c1", name="buscar_web", arguments={"query": "x"})],
        [TextDelta(text="No pude averiguarlo ahora.")],
    ])
    search = FakeSearch(fail=True)
    out = _collect(SearchAugmentedResponder(llm=llm, search=search).respond_stream(MSGS))
    assert "".join(out) == "No pude averiguarlo ahora."
    second_msgs = llm.calls[1][0]
    assert any(
        m.get("role") == "tool" and "no está disponible" in (m.get("content") or "").lower()
        for m in second_msgs
    )


def test_on_search_callback_fires() -> None:
    llm = FakeToolLLM([
        [ToolCallRequest(id="c1", name="buscar_web", arguments={"query": "x"})],
        [TextDelta(text="ok")],
    ])
    search = FakeSearch()
    fired = []

    async def on_search():
        fired.append(True)

    async def run():
        return [
            x
            async for x in SearchAugmentedResponder(llm=llm, search=search).respond_stream(
                MSGS, on_search=on_search
            )
        ]

    asyncio.run(run())
    assert fired == [True]
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `uv run pytest tests/test_agentic.py -q`
Expected: FAIL con `ModuleNotFoundError: No module named 'electronbot_es.core.agentic'`

- [ ] **Step 3: Implementar el responder**

Crear `src/electronbot_es/core/agentic.py`:

```python
"""SearchAugmentedResponder — loop de tool-calling de un paso para T3.

Expone respond_stream(messages) -> AsyncIterator[str], la misma forma que
LLMAdapter.generate_stream, así el orquestador y el speculative TTS no
cambian. Si el modelo pide buscar, ejecuta el SearchAdapter, reinyecta el
resultado y streamea la respuesta final.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Awaitable, Callable, Optional, Protocol

from electronbot_es.core.protocols import (
    ChatMessage,
    SearchAdapter,
    TextDelta,
    ToolCallRequest,
)


SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "buscar_web",
        "description": (
            "Busca información actual en internet: clima de hoy, noticias, "
            "precios, resultados deportivos, datos que cambian con el tiempo. "
            "NO la uses para conocimiento general que ya conoces."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "La búsqueda a realizar"}
            },
            "required": ["query"],
        },
    },
}

_SEARCH_UNAVAILABLE = "La búsqueda no está disponible ahora mismo."


class ToolStreamingLLM(Protocol):
    def stream_with_tools(self, messages: list[dict], tools: list[dict]) -> AsyncIterator: ...


class SearchAugmentedResponder:
    def __init__(self, *, llm: ToolStreamingLLM, search: SearchAdapter) -> None:
        self._llm = llm
        self._search = search

    async def respond_stream(
        self,
        messages: list[ChatMessage],
        *,
        on_search: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> AsyncIterator[str]:
        raw: list[dict] = [{"role": m.role, "content": m.content} for m in messages]

        tool_req: Optional[ToolCallRequest] = None
        yielded_text = False
        async for ev in self._llm.stream_with_tools(raw, [SEARCH_TOOL]):
            if isinstance(ev, TextDelta):
                yielded_text = True
                yield ev.text
            elif isinstance(ev, ToolCallRequest) and not yielded_text:
                tool_req = ev
                break

        if tool_req is None:
            return  # el modelo respondió directo, sin búsqueda

        if on_search is not None:
            await on_search()

        query = tool_req.arguments.get("query", "")
        try:
            result = await self._search.search(query)
        except Exception:
            result = _SEARCH_UNAVAILABLE

        raw.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tool_req.id,
                        "type": "function",
                        "function": {
                            "name": tool_req.name,
                            "arguments": json.dumps(tool_req.arguments),
                        },
                    }
                ],
            }
        )
        raw.append({"role": "tool", "tool_call_id": tool_req.id, "content": result})

        async for ev in self._llm.stream_with_tools(raw, []):
            if isinstance(ev, TextDelta):
                yield ev.text
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `uv run pytest tests/test_agentic.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/electronbot_es/core/agentic.py tests/test_agentic.py
git commit -m "feat: add SearchAugmentedResponder one-shot tool loop"
```

---

## Task 4: Integración (config, protocolo, orquestador, server)

**Files:**
- Modify: `src/electronbot_es/core/config.py`
- Modify: `src/electronbot_es/core/messages.py`
- Modify: `tests/test_ws_protocol.py`
- Modify: `src/electronbot_es/core/orchestrator.py`
- Modify: `src/electronbot_es/server/app.py`

- [ ] **Step 1: Agregar la API key a Settings**

En `src/electronbot_es/core/config.py`, después de `elevenlabs_api_key`:

```python
    tavily_api_key: str | None = Field(default=None)
```

- [ ] **Step 2: Agregar el estado "searching" (test primero)**

En `tests/test_ws_protocol.py`, en `test_llm_status_states`, cambiar la tupla:

```python
    for state in ("thinking", "generating", "done", "searching"):
```

Run: `uv run pytest tests/test_ws_protocol.py::test_llm_status_states -q`
Expected: FAIL (validación rechaza "searching")

En `src/electronbot_es/core/messages.py`, en `LlmStatus`, ampliar el Literal
(extensión ADITIVA, v1-compatible — no cambia la forma del mensaje):

```python
    state: Literal["thinking", "generating", "done", "searching"]
```

Run: `uv run pytest tests/test_ws_protocol.py -q`
Expected: PASS (incluye el caso "searching" y sigue rechazando "sleeping")

- [ ] **Step 3: Usar el responder en el orquestador**

En `src/electronbot_es/core/orchestrator.py`, agregar el import:

```python
from electronbot_es.core.agentic import SearchAugmentedResponder
```

En `VoiceSessionOrchestrator.__init__`, agregar el parámetro (después de `router`):

```python
        responder: Optional[SearchAugmentedResponder] = None,
```

y guardarlo:

```python
        self._responder = responder
```

En `run_turn`, dentro del tramo T3, reemplazar la línea que crea el productor de
tokens. Buscar:

```python
            async def llm_producer() -> None:
                buffer = ""
                try:
                    async for token in self._llm.generate_stream(built_messages):
```

y reemplazar SOLO el encabezado del bucle por una fuente seleccionable:

```python
            async def llm_producer() -> None:
                buffer = ""
                if self._responder is not None:
                    async def on_search() -> None:
                        await self._send_json(
                            LlmStatus(turn_id=turn_id, state="searching").model_dump()
                        )

                    token_source = self._responder.respond_stream(
                        built_messages, on_search=on_search
                    )
                else:
                    token_source = self._llm.generate_stream(built_messages)
                try:
                    async for token in token_source:
```

(El resto del cuerpo de `llm_producer` queda igual.)

- [ ] **Step 4: Cablear Tavily + responder en el server**

En `src/electronbot_es/server/app.py`, agregar imports:

```python
from electronbot_es.adapters.search_tavily import TavilySearch
from electronbot_es.core.agentic import SearchAugmentedResponder
```

Después de construir los adapters (tras la línea `tts = CartesiaTTS(...)`), agregar:

```python
        search: Optional[TavilySearch] = None
        responder: Optional[SearchAugmentedResponder] = None
        if settings.tavily_api_key:
            search = TavilySearch(api_key=settings.tavily_api_key)
            responder = SearchAugmentedResponder(llm=llm, search=search)
            logger.info("session %s: web search enabled (tavily)", session_id)
```

En la construcción del `VoiceSessionOrchestrator`, agregar el argumento:

```python
            responder=responder,
```

En el bloque `finally`, después de `await tts.aclose()`, agregar:

```python
            if search is not None:
                await search.aclose()
```

- [ ] **Step 5: Verificar import + suite completa**

Run: `uv run python -c "import electronbot_es.server.app"`
Expected: exit 0, sin error.

Run: `uv run pytest -q`
Expected: PASS — todos (incluye los tests nuevos de Tasks 1-3 y el de "searching").

- [ ] **Step 6: Commit**

```bash
git add src/electronbot_es/core/config.py src/electronbot_es/core/messages.py tests/test_ws_protocol.py src/electronbot_es/core/orchestrator.py src/electronbot_es/server/app.py
git commit -m "feat: wire web search responder into T3 with searching status"
```

---

## Self-review

- **Cobertura del spec:** SearchAdapter+Tavily (T1) ✓; primitivo tool-aware Groq (T2) ✓; SearchAugmentedResponder one-shot loop (T3) ✓; integración orquestador + degradación sin key (T4) ✓; estado "searching" para el filler (T4) ✓; manejo de error Tavily con tool-result obligatorio (T3, `_SEARCH_UNAVAILABLE` + 2da llamada) ✓; YAGNI un solo search (T3) ✓.
- **Criterios de éxito:** (1) info actual busca → T3 con tool-call; (2) conocimiento general no busca → `test_no_search_streams_directly`; (3) sin key T3 igual → T4 step 4 (responder=None); (4) Tavily caído no cuelga → `test_search_failure_still_answers`; (5) tests sin red → Tasks 1-3 con fakes/MockTransport.
- **Consistencia de tipos:** `TextDelta(text)`, `ToolCallRequest(id,name,arguments)`, `SearchAdapter.search(query)->str`, `stream_with_tools(messages,tools)`, `respond_stream(messages, *, on_search)` — idénticos entre definición (T1/T2/T3) y uso (T3/T4). `SEARCH_TOOL.function.name == "buscar_web"`.
- **Sin placeholders:** todos los pasos traen código o comando concreto.
```
