from __future__ import annotations

from typing import AsyncIterator

from groq import AsyncGroq

from electronbot_es.core.protocols import ChatMessage


DEFAULT_MODEL = "llama-3.3-70b-versatile"


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

    async def aclose(self) -> None:
        try:
            await self._client.close()
        except Exception:
            pass
