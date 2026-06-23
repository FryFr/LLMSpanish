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


def test_tool_call_without_finish_reason_still_emits() -> None:
    # Stream truncado (p.ej. max_tokens) sin finish_reason 'tool_calls':
    # igual debe emitir el ToolCallRequest acumulado, no tragárselo.
    tc = NS(id="call_9", function=NS(name="buscar_web", arguments='{"query":"x"}'))
    chunks = [_chunk(tool_calls=[tc]), _chunk(finish_reason="length")]
    out = _collect(_events_from_chunks(_aiter(chunks)))
    assert out == [
        ToolCallRequest(id="call_9", name="buscar_web", arguments={"query": "x"})
    ]
