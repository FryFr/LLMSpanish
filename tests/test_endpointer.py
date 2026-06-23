"""Tests del SilenceEndpointer — máquina de estados aislada con un VAD fake."""

from __future__ import annotations

from electronbot_es.mock.endpointer import SilenceEndpointer

FRAME = b"\x00" * 640  # 20ms @ 16kHz mono s16le; el contenido no importa con FakeVAD


class FakeVAD:
    """VAD determinístico: devuelve el próximo bool del script en cada llamada."""

    def __init__(self, script: list[bool]) -> None:
        self._script = list(script)
        self._i = 0

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        v = self._script[self._i] if self._i < len(self._script) else False
        self._i += 1
        return v


def _run(script: list[bool], **kw) -> list[bool]:
    """Procesa un frame por entrada del script; devuelve la lista de resultados."""
    ep = SilenceEndpointer(vad=FakeVAD(script), frame_ms=20, **kw)
    return [ep.process(FRAME) for _ in script]


def test_speech_then_silence_endpoints_after_hangover() -> None:
    # min_speech=60ms→3 frames, hangover=100ms→5 frames.
    script = [True] * 3 + [False] * 5
    out = _run(script, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000)
    assert out[:7] == [False] * 7
    assert out[7] is True  # 5to frame de silencio dispara el corte


def test_silence_only_never_endpoints_before_max() -> None:
    script = [False] * 20
    out = _run(script, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000)
    assert out == [False] * 20  # nunca hubo voz → no corta (max=500 frames)


def test_continuous_speech_hits_max_cap() -> None:
    # max=100ms → 5 frames; voz continua nunca genera silencio.
    script = [True] * 10
    out = _run(script, min_speech_ms=60, hangover_ms=100, max_utterance_ms=100)
    assert out[:4] == [False] * 4
    assert out[4] is True  # tope duro al 5to frame


def test_pause_mid_speech_resets_silence_run() -> None:
    # Pausa corta en medio NO debe cortar: la voz reinicia el conteo de silencio.
    script = [True] * 3 + [False] * 3 + [True] * 1 + [False] * 5
    out = _run(script, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000)
    assert True not in out[:11]
    assert out[11] is True  # recién el 5to silencio CONTINUO tras la pausa


def test_reset_returns_to_initial_state() -> None:
    ep = SilenceEndpointer(
        vad=FakeVAD([True, True, True, False, False, False, False, False]),
        frame_ms=20, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000,
    )
    results = [ep.process(FRAME) for _ in range(8)]
    assert results[-1] is True
    ep.reset()
    # Tras reset, frames de silencio no deben cortar de inmediato.
    ep2_vad_silence = [ep.process(FRAME) for _ in range(5)]
    assert ep2_vad_silence == [False] * 5
