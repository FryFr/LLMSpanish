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
