"""Tests for the Intent Router — regex matching, normalization, tier decisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from electronbot_es.router.intent_router import (
    IntentRouter,
    RouterDecision,
    normalize,
)
from electronbot_es.router.templates import DEFAULT_TEMPLATES


ROOT = Path(__file__).resolve().parents[1]
CANNED_YAML = ROOT / "src" / "electronbot_es" / "router" / "canned_responses.yaml"
ASSETS_DIR = ROOT / "assets" / "canned"


# ---------- normalize ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hola", "hola"),
        ("¿Cómo estás?", "como estas"),
        ("  ¡Hola, Michi!  ", "hola michi"),
        ("QUÉ MÁS PARCE", "que mas parce"),
        ("á é í ó ú ñ", "a e i o u n"),
        ("", ""),
        ("!!!", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


# ---------- router fixture ----------


@pytest.fixture
def router() -> IntentRouter:
    return IntentRouter.from_yaml(
        CANNED_YAML, ASSETS_DIR, templates=list(DEFAULT_TEMPLATES)
    )


# ---------- T1 canned matches ----------


@pytest.mark.parametrize(
    "transcript,expected_id",
    [
        ("Hola", "greeting_hola"),
        ("hola Michi", "greeting_hola"),
        ("¡Buenas!", "greeting_hola"),
        ("¿Qué más parce?", "greeting_que_mas"),
        ("Quiubo", "greeting_que_mas"),
        ("¿Cómo estás?", "greeting_como_estas"),
        ("Gracias", "thanks"),
        ("muchas gracias", "thanks"),
        ("perdón", "sorry_request"),
        ("sí", "affirm_yes"),
        ("dale", "affirm_yes"),
        ("no", "negate_no"),
        ("chao", "bye"),
        ("nos vemos", "bye"),
        ("buenas noches", "good_night"),
        ("buenos días", "good_morning"),
        ("¿quién eres?", "who_are_you"),
        ("¿cómo te llamas?", "who_are_you"),
        ("¿cuántos años tienes?", "how_old"),
        ("te quiero", "love_you"),
        ("¿estás ahí?", "are_you_there"),
        ("probando", "test_test"),
    ],
)
def test_t1_canned_match(router: IntentRouter, transcript: str, expected_id: str) -> None:
    decision = router.route(transcript)
    assert decision.tier == "T1", f"{transcript!r} routed to {decision.tier}, expected T1"
    assert decision.handler_id == expected_id
    assert decision.canned_text is not None
    assert decision.canned_wav is not None


# ---------- T2 template matches ----------


@pytest.mark.parametrize(
    "transcript,expected_id",
    [
        ("¿Qué hora es?", "time_now"),
        ("Michi, dime la hora", "time_now"),
        ("¿Tienes la hora?", "time_now"),
        ("¿Qué día es hoy?", "date_today"),
        ("¿Qué fecha es hoy?", "date_today"),
    ],
)
def test_t2_template_match(router: IntentRouter, transcript: str, expected_id: str) -> None:
    decision = router.route(transcript)
    assert decision.tier == "T2", f"{transcript!r} routed to {decision.tier}, expected T2"
    assert decision.handler_id == expected_id
    assert decision.template_text is not None
    assert len(decision.template_text) > 5


# ---------- T3 fallback ----------


@pytest.mark.parametrize(
    "transcript",
    [
        "Cuéntame la historia de Colombia",
        "¿Por qué el cielo es azul?",
        "Explícame la teoría de la relatividad",
        "¿Cuál es la capital de Australia?",
    ],
)
def test_t3_fallback(router: IntentRouter, transcript: str) -> None:
    decision = router.route(transcript)
    assert decision.tier == "T3"
    assert decision.handler_id is None


# ---------- Empty / edge cases ----------


def test_empty_transcript_goes_to_t3(router: IntentRouter) -> None:
    assert router.route("").tier == "T3"
    assert router.route("   ").tier == "T3"


def test_canned_wav_path_uses_entry_id(router: IntentRouter) -> None:
    decision = router.route("hola")
    assert decision.canned_wav is not None
    assert decision.canned_wav.name.startswith("greeting_hola_")
    assert decision.canned_wav.suffix == ".wav"
