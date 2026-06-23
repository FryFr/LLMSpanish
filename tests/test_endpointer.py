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
    first = [True, True, True, False, False, False, False, False]
    # Tras reset: 2 voz (bajo el umbral de 3) + 5 silencio. Si reset NO zeró
    # _speech_frames, el 1er voiced saltaría a SPEAKING y el silencio cortaría.
    after = [True, True, False, False, False, False, False]
    ep = SilenceEndpointer(
        vad=FakeVAD(first + after),
        frame_ms=20, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000,
    )
    results = [ep.process(FRAME) for _ in first]
    assert results[-1] is True  # el primer run sí corta
    ep.reset()
    post = [ep.process(FRAME) for _ in after]
    assert post == [False] * len(after)  # reset zeró el estado → no corta


def test_wrong_size_frame_is_ignored() -> None:
    # Un frame de tamaño inesperado se ignora: no corta y no avanza el estado.
    ep = SilenceEndpointer(
        vad=FakeVAD([True] * 10),  # el VAD diría "voz", pero no debe consumirse
        frame_ms=20, min_speech_ms=60, hangover_ms=100, max_utterance_ms=10000,
    )
    bad = b"\x00" * 100  # 640 esperados → tamaño inválido
    assert ep.process(bad) is False
    # Tras varios frames inválidos, un frame válido recién empieza a contar voz:
    # no debe haber saltado a SPEAKING por los inválidos.
    for _ in range(5):
        assert ep.process(bad) is False
