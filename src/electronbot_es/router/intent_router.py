"""Intent router — decide tier (T1/T2/T3) for a transcribed turn.

Week 1 uses regex + normalization. No ML classifier. Simple, fast, auditable.
If match rate is too low in production (<50%), upgrade to a small LM in Week 2+.
"""

from __future__ import annotations

import random
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, Optional

import yaml


Tier = Literal["T1", "T2", "T3"]


# ---------- Normalization ----------


_PUNCT_EDGE = re.compile(r"^[^\wáéíóúñ]+|[^\wáéíóúñ]+$", re.IGNORECASE | re.UNICODE)
_MULTI_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip accents, strip edge punctuation, collapse spaces.

    The patterns in the YAML catalog are matched against this normalized form.
    Accents are removed so the user saying "cómo estás" matches a pattern
    written as "como estas" (and vice versa).
    """
    if not text:
        return ""
    text = text.lower().strip()
    # Strip accents (NFD decomposition, drop combining marks).
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Strip punctuation at edges (keep internal for patterns like "¿que mas?").
    text = _PUNCT_EDGE.sub("", text)
    # Drop remaining punctuation entirely — router patterns are bare tokens.
    text = re.sub(r"[^\w\s]", " ", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text


# ---------- Data structures ----------


@dataclass(frozen=True)
class CannedEntry:
    """A T1 canned response: regex patterns + pre-synthesized WAV variants."""

    id: str
    patterns: tuple[re.Pattern, ...]
    text_variants: tuple[str, ...]  # original text (used at synthesis time)
    wav_paths: tuple[Path, ...]  # resolved file paths, one per variant

    def random_variant(self) -> tuple[str, Path]:
        idx = random.randrange(len(self.text_variants))
        return self.text_variants[idx], self.wav_paths[idx]


TemplateBuilder = Callable[[re.Match], str]


@dataclass(frozen=True)
class TemplateEntry:
    """A T2 handler: regex patterns + a function that builds the reply text."""

    id: str
    patterns: tuple[re.Pattern, ...]
    build: TemplateBuilder


@dataclass(frozen=True)
class RouterDecision:
    """The router's verdict for a turn."""

    tier: Tier
    handler_id: Optional[str] = None
    # T1 only: the pre-synthesized wav to play and the text (for logs/metrics).
    canned_text: Optional[str] = None
    canned_wav: Optional[Path] = None
    # T2 only: the text to synthesize through TTS streaming.
    template_text: Optional[str] = None


# ---------- Router ----------


@dataclass
class IntentRouter:
    canned: list[CannedEntry] = field(default_factory=list)
    templates: list[TemplateEntry] = field(default_factory=list)

    @classmethod
    def from_yaml(
        cls, yaml_path: Path, assets_dir: Path, templates: list[TemplateEntry]
    ) -> "IntentRouter":
        """Load canned entries from YAML and pair with template handlers.

        YAML shape:
            canned:
              - id: greeting_hola
                patterns:
                  - "^hola\\b"
                  - "^buenas\\b"
                variants:
                  - "¡Hola parce!"
                  - "¡Ey, qué más!"
        """
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        canned: list[CannedEntry] = []
        for entry in raw.get("canned", []):
            entry_id = entry["id"]
            patterns = tuple(
                re.compile(p, re.IGNORECASE) for p in entry["patterns"]
            )
            variants = tuple(entry["variants"])
            wav_paths = tuple(
                assets_dir / f"{entry_id}_{i}.wav" for i in range(len(variants))
            )
            canned.append(
                CannedEntry(
                    id=entry_id,
                    patterns=patterns,
                    text_variants=variants,
                    wav_paths=wav_paths,
                )
            )
        return cls(canned=canned, templates=list(templates))

    def route(self, transcript: str) -> RouterDecision:
        norm = normalize(transcript)
        if not norm:
            return RouterDecision(tier="T3")

        # T1: canned response.
        for entry in self.canned:
            for pattern in entry.patterns:
                if pattern.search(norm):
                    text, wav = entry.random_variant()
                    return RouterDecision(
                        tier="T1",
                        handler_id=entry.id,
                        canned_text=text,
                        canned_wav=wav,
                    )

        # T2: template handler.
        for entry in self.templates:
            for pattern in entry.patterns:
                m = pattern.search(norm)
                if m:
                    reply = entry.build(m)
                    return RouterDecision(
                        tier="T2",
                        handler_id=entry.id,
                        template_text=reply,
                    )

        # T3: full LLM pipeline.
        return RouterDecision(tier="T3")
