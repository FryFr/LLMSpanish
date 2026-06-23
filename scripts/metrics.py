"""Aggregate per-turn metrics from logs/session.jsonl.

Usage:
    uv run python scripts/metrics.py
    uv run python scripts/metrics.py --last 50
    uv run python scripts/metrics.py --path logs/session.jsonl

Reads the JSONL written by `electronbot_es.core.obs.log_turn` and prints a
table with: turns per tier, p50/p95 of speech_end->first_audio (el campo
    wire se llama wake_to_first_audio por compat v1) and
tts_first_chunk, average tokens, average cost, total cost.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Iterable


DEFAULT_PATH = Path("logs/session.jsonl")


def _load(path: Path, last: int | None) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"no metrics file at {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if last is not None:
        records = records[-last:]
    return records


def _pct(values: Iterable[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        return 0.0
    # Type-1 percentile (nearest-rank), good enough for small N.
    k = max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))
    return xs[k]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--last", type=int, default=None, help="only the last N turns")
    args = ap.parse_args()

    records = _load(args.path, args.last)
    if not records:
        print("no turn records found")
        return

    by_tier: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_tier[r.get("tier", "?")].append(r)

    total_cost = 0.0
    total_turns = len(records)

    print(f"\n=== ElectronBot-ES metrics — {total_turns} turns ===\n")
    header = f"{'tier':<4} {'n':>4} {'%':>5}  {'spEnd->aud p50':>16} {'p95':>7}  {'tts_first p50':>14} {'p95':>7}  {'avg_cost':>10} {'total':>10}"
    print(header)
    print("-" * len(header))

    for tier in ("T1", "T2", "T3"):
        turns = by_tier.get(tier, [])
        n = len(turns)
        pct = 100 * n / total_turns if total_turns else 0
        if n == 0:
            print(f"{tier:<4} {n:>4} {pct:>4.0f}%  {'-':>16} {'-':>7}  {'-':>14} {'-':>7}  {'-':>10} {'-':>10}")
            continue
        wake_vals = [t["latencies_ms"].get("wake_to_first_audio", 0) for t in turns]
        tts_vals = [t["latencies_ms"].get("tts_first_chunk", 0) for t in turns]
        costs = [t.get("cost_usd", 0.0) for t in turns]
        tier_total = sum(costs)
        total_cost += tier_total
        print(
            f"{tier:<4} {n:>4} {pct:>4.0f}%  "
            f"{int(_pct(wake_vals, 50)):>13} ms {int(_pct(wake_vals, 95)):>4} ms  "
            f"{int(_pct(tts_vals, 50)):>11} ms {int(_pct(tts_vals, 95)):>4} ms  "
            f"${statistics.mean(costs):>8.6f} ${tier_total:>8.4f}"
        )

    print("-" * len(header))
    print(f"total cost: ${total_cost:.4f}   avg/turn: ${total_cost / total_turns:.6f}")

    t3_turns = by_tier.get("T3", [])
    if t3_turns:
        toks_in = [t.get("tokens", {}).get("in", 0) for t in t3_turns]
        toks_out = [t.get("tokens", {}).get("out", 0) for t in t3_turns]
        print(
            f"T3 tokens avg: in={statistics.mean(toks_in):.0f}  out={statistics.mean(toks_out):.0f}"
        )

    match_rate = 100 * (len(by_tier.get("T1", [])) + len(by_tier.get("T2", []))) / total_turns
    print(f"T1+T2 match rate: {match_rate:.0f}%  (target >60%)")


if __name__ == "__main__":
    main()
