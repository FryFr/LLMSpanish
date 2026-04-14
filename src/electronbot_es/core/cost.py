"""Per-turn cost estimation for the 3-tier router.

Rates are from the providers' public pricing pages as of April 2026. These
are estimates — actual invoices may differ a few percent due to rounding,
minimum charges, tiering, and retries. The point is NOT accounting-grade
billing; it's "how expensive is an average turn" so we can set subscription
pricing and spot runaway costs.

Constants live in one place so swapping providers / renegotiating rates is
a one-line change.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------- rates (USD) ----------

# Deepgram Nova-3 streaming — https://deepgram.com/pricing
DEEPGRAM_USD_PER_SECOND = 0.0043 / 60

# Groq Llama 3.3 70B — https://groq.com/pricing
GROQ_USD_PER_INPUT_TOKEN = 0.59 / 1_000_000
GROQ_USD_PER_OUTPUT_TOKEN = 0.79 / 1_000_000

# Cartesia Sonic — https://cartesia.ai/pricing (≈ $65 / 1M chars for Sonic)
CARTESIA_USD_PER_CHAR = 65.0 / 1_000_000


# ---------- estimator ----------


@dataclass
class TurnCost:
    """Breakdown of the USD spent on one turn."""

    stt: float = 0.0
    llm_in: float = 0.0
    llm_out: float = 0.0
    tts: float = 0.0

    @property
    def total(self) -> float:
        return self.stt + self.llm_in + self.llm_out + self.tts


def cost_t1() -> TurnCost:
    """T1 canned response — zero provider calls, always $0."""
    return TurnCost()


def cost_t2(*, stt_seconds: float, tts_chars: int) -> TurnCost:
    """T2 template — STT was used to transcribe, TTS synthesizes the reply.

    No LLM in this path.
    """
    return TurnCost(
        stt=stt_seconds * DEEPGRAM_USD_PER_SECOND,
        tts=tts_chars * CARTESIA_USD_PER_CHAR,
    )


def cost_t3(
    *,
    stt_seconds: float,
    llm_tokens_in: int,
    llm_tokens_out: int,
    tts_chars: int,
) -> TurnCost:
    """T3 full pipeline — STT + LLM + TTS."""
    return TurnCost(
        stt=stt_seconds * DEEPGRAM_USD_PER_SECOND,
        llm_in=llm_tokens_in * GROQ_USD_PER_INPUT_TOKEN,
        llm_out=llm_tokens_out * GROQ_USD_PER_OUTPUT_TOKEN,
        tts=tts_chars * CARTESIA_USD_PER_CHAR,
    )
