"""Day 3 LLM benchmark — Spanish LATAM conversational prompts.

Runs 10 fixed prompts through each configured LLM adapter and measures:
- time-to-first-token (ms)
- time-to-last-token (ms)
- output tokens (approximate, by whitespace split)
- tokens/sec
- estimated cost USD

Usage:
    uv run python scripts/bench_llm.py                  # all models
    uv run python scripts/bench_llm.py --only groq      # just one
    uv run python scripts/bench_llm.py --out docs/llm-benchmark.md

Models are skipped if their API key or Ollama model is missing.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from electronbot_es.adapters.llm_claude import ClaudeLLM
from electronbot_es.adapters.llm_groq import GroqLLM
from electronbot_es.adapters.llm_ollama import OllamaLLM
from electronbot_es.core.config import get_settings
from electronbot_es.core.persona import build_messages
from electronbot_es.core.protocols import LLMAdapter


PROMPTS: list[str] = [
    "Hola Michi, ¿cómo estás hoy?",
    "¿Me podés recomendar una película para ver con mi familia?",
    "Explícame en pocas palabras qué es la fotosíntesis.",
    "Cuéntame un chiste corto que sea gracioso de verdad.",
    "Si tuvieras que elegir entre playa o montaña, ¿qué elegís y por qué?",
    "¿Qué puedo cocinar rápido con huevo, tomate y cebolla?",
    "Dame un consejo breve para dormir mejor esta noche.",
    "¿Cuál es la diferencia entre un caimán y un cocodrilo?",
    "Necesito una idea creativa para el regalo de cumpleaños de mi mamá.",
    "¿Qué opinás del café con leche por la mañana?",
]


# Cost per 1M tokens (input, output) — update as providers change pricing.
PRICING = {
    "groq/llama-3.3-70b-versatile": (0.59, 0.79),
    "anthropic/claude-haiku-4-5-20251001": (1.00, 5.00),
    "ollama/llama3.2:1b": (0.0, 0.0),
    "ollama/qwen2.5:3b": (0.0, 0.0),
}


@dataclass
class PromptResult:
    prompt: str
    first_token_ms: float
    total_ms: float
    output: str
    out_tokens: int

    @property
    def tokens_per_sec(self) -> float:
        if self.total_ms <= 0:
            return 0.0
        return self.out_tokens / (self.total_ms / 1000)


@dataclass
class ModelResult:
    name: str
    pricing_key: str
    per_prompt: list[PromptResult] = field(default_factory=list)
    error: str | None = None

    def _agg(self, fn: Callable[[PromptResult], float]) -> tuple[float, float]:
        values = [fn(r) for r in self.per_prompt]
        if not values:
            return 0.0, 0.0
        return statistics.median(values), max(values)

    def summary(self) -> dict:
        if not self.per_prompt:
            return {"name": self.name, "error": self.error}
        p50_ftt, max_ftt = self._agg(lambda r: r.first_token_ms)
        p50_tot, max_tot = self._agg(lambda r: r.total_ms)
        avg_tps = statistics.mean(r.tokens_per_sec for r in self.per_prompt)
        avg_out = statistics.mean(r.out_tokens for r in self.per_prompt)
        in_price, out_price = PRICING.get(self.pricing_key, (0.0, 0.0))
        # Approximate: assume ~80 input tokens per turn (system + few-shots + user)
        avg_cost = (80 * in_price + avg_out * out_price) / 1_000_000
        return {
            "name": self.name,
            "p50_first_token_ms": round(p50_ftt, 0),
            "max_first_token_ms": round(max_ftt, 0),
            "p50_total_ms": round(p50_tot, 0),
            "max_total_ms": round(max_tot, 0),
            "avg_tokens_out": round(avg_out, 1),
            "avg_tokens_per_sec": round(avg_tps, 1),
            "avg_cost_usd_per_turn": round(avg_cost, 6),
        }


async def run_prompt(adapter: LLMAdapter, prompt: str) -> PromptResult:
    messages = build_messages(prompt)
    start = time.perf_counter()
    first_ms: float | None = None
    buf = ""
    async for token in adapter.generate_stream(messages):
        if first_ms is None:
            first_ms = (time.perf_counter() - start) * 1000
        buf += token
    total_ms = (time.perf_counter() - start) * 1000
    return PromptResult(
        prompt=prompt,
        first_token_ms=first_ms or total_ms,
        total_ms=total_ms,
        output=buf.strip(),
        out_tokens=max(1, len(buf.split())),
    )


async def run_model(name: str, pricing_key: str, adapter: LLMAdapter) -> ModelResult:
    result = ModelResult(name=name, pricing_key=pricing_key)
    print(f"\n=== {name} ===")
    try:
        for i, prompt in enumerate(PROMPTS, 1):
            r = await run_prompt(adapter, prompt)
            result.per_prompt.append(r)
            preview = r.output[:70].replace("\n", " ").encode("ascii", "replace").decode("ascii")
            print(
                f"  [{i:2d}/10] ftt={r.first_token_ms:6.0f}ms "
                f"total={r.total_ms:6.0f}ms out={r.out_tokens:3d}tok "
                f"-> {preview}..."
            )
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
        print(f"  ERROR: {result.error}")
    finally:
        await adapter.aclose()
    return result


def render_markdown(results: list[ModelResult]) -> str:
    lines = [
        "# LLM Benchmark — Spanish LATAM (Day 3)",
        "",
        f"Prompts: {len(PROMPTS)} fixed conversational turns with Michi persona + 3 few-shots.",
        "Metrics are per-model aggregates across all prompts.",
        "",
        "| Model | p50 first token | p50 total | avg tok/s | avg out tok | avg cost/turn |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        s = r.summary()
        if "error" in s:
            lines.append(f"| {s['name']} | — | — | — | — | ERROR: {s['error']} |")
            continue
        lines.append(
            f"| {s['name']} "
            f"| {s['p50_first_token_ms']:.0f} ms "
            f"| {s['p50_total_ms']:.0f} ms "
            f"| {s['avg_tokens_per_sec']:.1f} "
            f"| {s['avg_tokens_out']:.0f} "
            f"| ${s['avg_cost_usd_per_turn']:.6f} |"
        )
    lines.append("")
    lines.append("## Sample outputs (first prompt only)")
    lines.append("")
    for r in results:
        if r.per_prompt:
            out = r.per_prompt[0].output.replace("\n", " ")
            lines.append(f"- **{r.name}**: _{out}_")
    lines.append("")
    return "\n".join(lines)


async def main(only: str | None, out_path: Path | None) -> None:
    settings = get_settings()
    adapters: list[tuple[str, str, Callable[[], LLMAdapter]]] = []

    if settings.groq_api_key and (not only or only == "groq"):
        adapters.append((
            "Groq Llama 3.3 70B",
            "groq/llama-3.3-70b-versatile",
            lambda: GroqLLM(api_key=settings.groq_api_key),
        ))
    if settings.anthropic_api_key and (not only or only == "claude"):
        adapters.append((
            "Claude Haiku 4.5",
            "anthropic/claude-haiku-4-5-20251001",
            lambda: ClaudeLLM(api_key=settings.anthropic_api_key),
        ))
    if not only or only == "ollama-llama":
        adapters.append((
            "Ollama Llama 3.2 1B",
            "ollama/llama3.2:1b",
            lambda: OllamaLLM(model="llama3.2:1b"),
        ))
    if not only or only == "ollama-qwen":
        adapters.append((
            "Ollama Qwen 2.5 3B",
            "ollama/qwen2.5:3b",
            lambda: OllamaLLM(model="qwen2.5:3b"),
        ))

    if not adapters:
        print("No adapters to run. Check your .env keys or --only flag.")
        sys.exit(1)

    results: list[ModelResult] = []
    for name, key, factory in adapters:
        results.append(await run_model(name, key, factory()))

    md = render_markdown(results)
    print("\n" + "=" * 70)
    print(md)

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--only", choices=["groq", "claude", "ollama-llama", "ollama-qwen"])
    p.add_argument("--out", type=Path, default=Path("docs/llm-benchmark.md"))
    args = p.parse_args()
    asyncio.run(main(args.only, args.out))
