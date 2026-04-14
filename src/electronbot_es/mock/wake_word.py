"""Wake word detector for the MockESP32.

Week 1 placeholder: uses openWakeWord's pre-trained `hey_jarvis_v0.1` as
stand-in for the real "Hola Michi" model. The real model (microWakeWord
trained on our own samples) is Week 2 work — see engram topic
`week-2/deferred-priorities`.

API contract: feed 16kHz mono int16 PCM frames of any size; the detector
internally batches to openWakeWord's 80ms window. `process(frame)` returns
True exactly once per detection event (rising edge over threshold), with a
short cooldown to avoid retriggering on the same utterance.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# openWakeWord expects 16kHz mono int16 chunks of 1280 samples (80ms).
_OWW_CHUNK_SAMPLES = 1280


class WakeWordDetector:
    def __init__(
        self,
        *,
        keyword: str = "hey_jarvis",
        threshold: float = 0.5,
        cooldown_s: float = 2.0,
    ) -> None:
        import openwakeword
        from openwakeword.model import Model

        paths = [
            p for p in openwakeword.get_pretrained_model_paths() if keyword in p
        ]
        if not paths:
            raise RuntimeError(
                f"no pretrained openWakeWord model matches keyword={keyword!r}. "
                f"Run: python -c 'import openwakeword; openwakeword.utils.download_models()'"
            )
        self._model = Model(wakeword_model_paths=paths)
        self._keys = list(self._model.models.keys())
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._last_fire_at: float = 0.0
        self._buf = np.zeros(0, dtype=np.int16)
        logger.info("wake word detector loaded: %s thr=%.2f", self._keys, threshold)

    def process(self, pcm: bytes) -> bool:
        """Feed a PCM chunk. Returns True on a fresh detection."""
        if not pcm:
            return False
        samples = np.frombuffer(pcm, dtype=np.int16)
        self._buf = np.concatenate([self._buf, samples]) if self._buf.size else samples
        fired = False
        while self._buf.size >= _OWW_CHUNK_SAMPLES:
            chunk = self._buf[:_OWW_CHUNK_SAMPLES]
            self._buf = self._buf[_OWW_CHUNK_SAMPLES:]
            scores = self._model.predict(chunk)
            peak = max(scores.values()) if scores else 0.0
            if peak >= self._threshold:
                now = time.perf_counter()
                if now - self._last_fire_at >= self._cooldown_s:
                    self._last_fire_at = now
                    fired = True
                    logger.info("wake word fired: score=%.3f", peak)
        return fired

    def reset(self) -> None:
        self._buf = np.zeros(0, dtype=np.int16)
        self._model.reset()
