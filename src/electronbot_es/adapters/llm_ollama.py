from __future__ import annotations

from typing import AsyncIterator

from ollama import AsyncClient

from electronbot_es.core.protocols import ChatMessage


DEFAULT_MODEL = "llama3.2:1b"


class OllamaLLM:
    """Local LLM via Ollama HTTP API. Dev fallback / privacy mode."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        host: str = "http://localhost:11434",
        temperature: float = 0.6,
        num_predict: int = 512,
    ) -> None:
        self._client = AsyncClient(host=host)
        self._model = model
        self._temperature = temperature
        self._num_predict = num_predict

    async def generate_stream(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[str]:
        stream = await self._client.chat(
            model=self._model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            stream=True,
            options={
                "temperature": self._temperature,
                "num_predict": self._num_predict,
            },
        )
        async for chunk in stream:
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content

    async def aclose(self) -> None:
        return
