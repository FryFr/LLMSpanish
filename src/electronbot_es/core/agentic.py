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
