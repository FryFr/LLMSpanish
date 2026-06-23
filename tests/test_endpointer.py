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
    # Frames inválidos se descartan: nunca cortan ni avanzan el estado, aunque
    # el VAD inyectado diría "voz" si llegara a consultarse.
    for _ in range(6):
        assert ep.process(bad) is False


# ---------- warmup / lead-in (fix del transitorio del wake word) ----------


def test_without_warmup_wake_transient_causes_false_endpoint() -> None:
    # Reproduce el bug: el transitorio del wake (cola de voz + filler) arma el
    # endpointer y la pausa posterior dispara un corte ANTES de que el usuario
    # diga su comando.
    transient = [True] * 15  # ~300ms de "voz" del arranque → alcanza min_speech
    pause = [False] * 35  # 700ms de pausa antes de hablar
    ep = SilenceEndpointer(
        vad=FakeVAD(transient + pause),
        frame_ms=20, start_delay_ms=0, min_speech_ms=300,
        hangover_ms=700, max_utterance_ms=20000,
    )
    out = [ep.process(FRAME) for _ in transient + pause]
    assert True in out  # bug presente sin warmup


def test_warmup_prevents_false_endpoint_from_transient() -> None:
    # Con warmup que cubre el transitorio, ese mismo arranque NO debe cortar.
    transient = [True] * 15
    pause = [False] * 35
    ep = SilenceEndpointer(
        vad=FakeVAD(transient + pause),
        frame_ms=20, start_delay_ms=400, min_speech_ms=300,
        hangover_ms=700, max_utterance_ms=20000,
    )
    out = [ep.process(FRAME) for _ in transient + pause]
    assert True not in out  # warmup ignora el transitorio → no corta


def test_warmup_then_real_speech_endpoints() -> None:
    # Tras el warmup, voz real + silencio sí debe cortar normalmente.
    # El warmup NO consulta el VAD, así que el script sólo cubre los frames
    # post-warmup (la voz real + el silencio); los frames de warmup se cuentan
    # aparte.
    speech = [True] * 15 + [False] * 35  # comando real + silencio
    ep = SilenceEndpointer(
        vad=FakeVAD(speech),
        frame_ms=20, start_delay_ms=200, min_speech_ms=300,
        hangover_ms=700, max_utterance_ms=20000,
    )
    warmup_frames = 200 // 20  # 10 frames de lead-in (ignorados, sin VAD)
    out = [ep.process(FRAME) for _ in range(warmup_frames + len(speech))]
    assert out[-1] is True


def test_speaking_property_tracks_detection() -> None:
    # `speaking` queda False hasta que se detecta voz real (para el aviso visual).
    ep = SilenceEndpointer(
        vad=FakeVAD([False, False] + [True] * 15),
        frame_ms=20, min_speech_ms=300, hangover_ms=700, max_utterance_ms=20000,
    )
    ep.process(FRAME)
    assert ep.speaking is False  # silencio inicial: aún no oyó
    for _ in range(16):
        ep.process(FRAME)
    assert ep.speaking is True  # ya acumuló los 15 frames de voz
