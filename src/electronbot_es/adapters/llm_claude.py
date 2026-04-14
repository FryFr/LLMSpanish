from __future__ import annotations

from typing import AsyncIterator

from anthropic import AsyncAnthropic

from electronbot_es.core.protocols import ChatMessage


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class ClaudeLLM:
    """Cloud LLM via Anthropic (fallback). Better Spanish reasoning than Groq."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.6,
        max_tokens: int = 512,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def generate_stream(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[str]:
        # Anthropic API requires system prompt as a top-level field, not in messages.
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]

        async with self._client.messages.stream(
            model=self._model,
            system=system or None,
            messages=convo,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield text

    async def aclose(self) -> None:
        try:
            await self._client.close()
        except Exception:
            pass
