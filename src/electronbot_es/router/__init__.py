"""Intent router package.

Decides whether a transcript should be answered by:
- T1: a pre-synthesized canned WAV (< 200 ms)
- T2: a template handler (dynamic text → streaming TTS, < 400 ms)
- T3: the full STT → LLM → TTS pipeline (600-900 ms)
"""

from electronbot_es.router.intent_router import (
    IntentRouter,
    RouterDecision,
    Tier,
)

__all__ = ["IntentRouter", "RouterDecision", "Tier"]
