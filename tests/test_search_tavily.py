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


def test_aclose_does_not_close_injected_client() -> None:
    inner = _client(lambda r: httpx.Response(200, json={}))
    s = TavilySearch(api_key="x", client=inner)
    _run(s.aclose())
    assert inner.is_closed is False
