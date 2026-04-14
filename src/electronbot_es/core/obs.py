"""Lightweight per-turn JSONL observability.

Writes one JSON line per completed turn to `logs/session.jsonl`. The file
is append-only, unrotated — rotation is out of scope for Week 1. Downstream
analysis (p50/p95, cost aggregation) lives in `scripts/metrics.py`.

Design notes:
- We write in a background thread via a plain file handle with line
  buffering. No async file I/O, no structlog pipeline — this is called
  once per turn, the volume is trivial.
- Failure to log must NEVER break the turn. All errors are swallowed and
  printed to stderr once per process.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOG_DIR = Path("logs")
_LOG_PATH = _LOG_DIR / "session.jsonl"

_lock = threading.Lock()
_handle: Optional[Any] = None
_warned = False


def _get_handle():
    global _handle, _warned
    if _handle is not None:
        return _handle
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _handle = open(_LOG_PATH, "a", encoding="utf-8", buffering=1)
    except Exception as e:
        if not _warned:
            print(f"[obs] failed to open {_LOG_PATH}: {e}", file=sys.stderr)
            _warned = True
        return None
    return _handle


def log_turn(record: dict) -> None:
    """Append one turn record as JSONL. Never raises."""
    global _warned
    enriched = {"ts": time.time(), **record}
    try:
        with _lock:
            h = _get_handle()
            if h is None:
                return
            h.write(json.dumps(enriched, ensure_ascii=False) + "\n")
    except Exception as e:
        if not _warned:
            print(f"[obs] log_turn failed: {e}", file=sys.stderr)
            _warned = True
