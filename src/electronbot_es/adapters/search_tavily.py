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
        """timeout_s y max_results aplican al cliente interno; si se inyecta un
        `client` ya configurado, timeout_s no tiene efecto."""
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
