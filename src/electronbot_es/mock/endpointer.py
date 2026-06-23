"""SilenceEndpointer — decide cuándo terminó de hablar la persona.

Máquina de estados sobre un VAD por frames:

    WAITING_SPEECH ──(voz)──► SPEAKING ──(hangover de silencio)──► ENDPOINTED

El VAD es inyectable: por defecto construye `webrtcvad.Vad`, pero los tests
pasan un fake determinístico para probar la lógica en aislamiento.

Guardas:
- start_delay_ms : ventana de lead-in al inicio del turno donde NO se evalúa el
                   corte. Ignora el transitorio post-wake (cola de la palabra de
                   activación + ack filler que se cuela por el parlante sin AEC),
                   que si no armaría el endpointer y cortaría en la pausa previa
                   al comando real. Los frames igual se streamean al servidor.
- min_speech_ms : voz mínima acumulada antes de permitir un corte (mata clics
                  y el silencio inicial).
- hangover_ms   : silencio CONTINUO que dispara el corte (el "feeling").
- max_utterance_ms : tope duro por si el VAD nunca ve silencio.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Protocol


class _VAD(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


class _State(Enum):
    WAITING_SPEECH = "waiting_speech"
    SPEAKING = "speaking"
    ENDPOINTED = "endpointed"


class SilenceEndpointer:
    def __init__(
        self,
        *,
        aggressiveness: int = 2,
        sample_rate: int = 16000,
        frame_ms: int = 20,
        start_delay_ms: int = 0,
        min_speech_ms: int = 300,
        hangover_ms: int = 700,
        max_utterance_ms: int = 15000,
        vad: Optional[_VAD] = None,
    ) -> None:
        if vad is None:
            import webrtcvad

            vad = webrtcvad.Vad(aggressiveness)
        self._vad = vad
        self._sample_rate = sample_rate
        self._frame_bytes = sample_rate * frame_ms // 1000 * 2
        self._warmup_frames = start_delay_ms // frame_ms
        self._min_speech_frames = max(1, min_speech_ms // frame_ms)
        self._hangover_frames = max(1, hangover_ms // frame_ms)
        self._max_frames = max(1, max_utterance_ms // frame_ms)
        self.reset()

    def reset(self) -> None:
        self._state = _State.WAITING_SPEECH
        self._speech_frames = 0
        self._silence_run = 0
        self._total_frames = 0
        self._warmup_seen = 0

    @property
    def speaking(self) -> bool:
        """True una vez que se detectó voz real (para feedback visual)."""
        return self._state in (_State.SPEAKING, _State.ENDPOINTED)

    def process(self, frame: bytes) -> bool:
        """Devuelve True cuando se alcanzó el fin de habla. Idempotente tras True."""
        if self._state is _State.ENDPOINTED:
            return True
        # Frame de tamaño inesperado: ignorar para el VAD (webrtcvad exige
        # exactamente 10/20/30ms), no romper el turno.
        if len(frame) != self._frame_bytes:
            return False

        # Lead-in: ignorar el transitorio del arranque (cola del wake + filler).
        # Los frames ya se streamean al servidor; sólo no evaluamos el corte.
        if self._warmup_seen < self._warmup_frames:
            self._warmup_seen += 1
            return False

        self._total_frames += 1
        voiced = self._vad.is_speech(frame, self._sample_rate)

        if self._state is _State.WAITING_SPEECH:
            if voiced:
                self._speech_frames += 1
                if self._speech_frames >= self._min_speech_frames:
                    self._state = _State.SPEAKING
                    self._silence_run = 0
        elif self._state is _State.SPEAKING:
            if voiced:
                self._silence_run = 0
            else:
                self._silence_run += 1
                if self._silence_run >= self._hangover_frames:
                    self._state = _State.ENDPOINTED
                    return True

        if self._total_frames >= self._max_frames:
            self._state = _State.ENDPOINTED
            return True
        return False
