from __future__ import annotations

import json
from typing import AsyncIterator

from groq import AsyncGroq

from electronbot_es.core.protocols import ChatMessage, TextDelta, ToolCallRequest


DEFAULT_MODEL = "llama-3.3-70b-versatile"


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


class GroqLLM:
    """Cloud LLM via Groq (primary). HTTP streaming, extremely fast first token."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.6,
        max_tokens: int = 512,
    ) -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def generate_stream(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

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

    async def aclose(self) -> None:
        try:
            await self._client.close()
        except Exception:
            pass
