# Auto-Endpointing por VAD en el cliente mock

**Fecha:** 2026-06-22
**Estado:** Aprobado, pendiente de plan de implementación
**Proyecto:** Michibot (electronbot-es) — Week 2, frente A ("como Alexa, sin botones")

## Problema

El cliente mock (`mock_esp32.py`) corta la grabación con un **ENTER manual**
([mock_esp32.py:283](../../../src/electronbot_es/mock/mock_esp32.py#L283)). Esto
tiene dos consecuencias:

1. **No simula al dispositivo real.** El ESP32-S3 con ESP-SR AFE va a detectar el
   fin del habla on-device. El mock existe para imitar al dispositivo, y hoy no lo
   hace.
2. **Contamina la medición de latencia.** `stt_final` (~3000ms) y `tts_first_chunk`
   (~3600ms) arrastran el tiempo de reacción humana al apretar la tecla. No son
   latencia del sistema — son el dedo del operador.

Hallazgo adicional: el campo `wake_to_first_audio` está mal nombrado. Se calcula
como `tts_first_chunk - stt_final`
([orchestrator.py:260-262](../../../src/electronbot_es/core/orchestrator.py#L260)),
o sea **fin-de-habla → primer audio**. Irónicamente es el número MÁS honesto que
hay hoy (~827ms = cómputo puro del servidor), pero el nombre miente.

El endpointing del servidor ya existe pero está vestigial: Deepgram está
configurado con `endpointing=300ms` y `utterance_end_ms=1000`
([stt_deepgram.py:60-62](../../../src/electronbot_es/adapters/stt_deepgram.py#L60)),
con handler `on_utterance_end`, pero como el cliente nunca suelta el micrófono
hasta el ENTER, nunca llega a actuar.

## Objetivo

Reemplazar el corte manual por **detección automática de fin de habla (VAD) en el
cliente**, de modo que:
- El comportamiento sea "wake word → hablás → corta solo al detectar silencio →
  responde → vuelve a dormir" (un turno por wake word, estilo Alexa por defecto).
- Las métricas de latencia dejen de estar contaminadas por el tiempo de reacción.
- El mock simule fielmente el VAD on-device del ESP32-S3.

## Decisión de arquitectura

**Approach elegido: VAD del lado del cliente** (descartado: endpointing dirigido
por el servidor, porque no simula al dispositivo y agrega round-trip + cambio de
protocolo).

El endpointing de Deepgram que ya existe se **mantiene como red de seguridad**: si
el VAD del cliente falla, el servidor igual finaliza el turno.

## Componentes

### 1. Módulo nuevo: `mock/endpointer.py`

Una sola responsabilidad: dado un stream de frames PCM de 20ms, decidir cuándo
terminó la frase. Testeable en aislamiento, sin micrófono ni servidor.

**Interfaz:**
```python
class SilenceEndpointer:
    def __init__(
        self,
        *,
        aggressiveness: int = 2,      # webrtcvad 0..3
        sample_rate: int = 16000,
        frame_ms: int = 20,
        min_speech_ms: int = 300,     # voz mínima antes de permitir corte
        hangover_ms: int = 700,       # silencio continuo que dispara el corte
        max_utterance_ms: int = 15000 # tope duro
    ) -> None: ...

    def process(self, frame: bytes) -> bool: ...  # True = endpoint alcanzado
    def reset(self) -> None: ...
```

**Máquina de estados:**
```
WAITING_SPEECH ──(voz)──► SPEAKING ──(hangover_ms de silencio)──► ENDPOINTED
```

**Guardas:**
- `min_speech_ms`: no corta hasta haber acumulado voz real. Evita que un clic o el
  silencio inicial dispare el corte.
- `hangover_ms`: silencio CONTINUO requerido. Es el dial principal del "feeling"
  (ansioso vs lento).
- `max_utterance_ms`: corta sí o sí si el VAD nunca ve silencio.

**Nota técnica:** los frames del mock ya son 20ms @ 16kHz mono s16le = 640 bytes
([mock_esp32.py:45-48](../../../src/electronbot_es/mock/mock_esp32.py#L45)),
tamaño que `webrtcvad.is_speech` acepta directamente.

### 2. Integración en `mock_esp32.py`

- Reemplazar `await wait_key("[recording... press ENTER to stop]")`
  ([mock_esp32.py:283](../../../src/electronbot_es/mock/mock_esp32.py#L283)) por un
  corte dirigido por el endpointer.
- El `pump_mic` existente alimenta cada frame al endpointer además de enviarlo al
  WS. Cuando `process()` devuelve True: registrar `speech_end`, frenar la grabación,
  mandar `audio.end`.
- **ENTER queda como override manual** (cortar a mano si hace falta). Lo que dispare
  primero —VAD o tecla— gana.
- `endpointer.reset()` por turno.
- Cero cambios al protocolo: `audio.start`/`audio.end` ya modelan esto.

### 3. Honestidad de las métricas

- El campo del wire `wake_to_first_audio` **no se toca** (protocolo v1 inmutable).
- Relabel en la salida del mock y en `scripts/metrics.py` a `speech_end→first_audio`
  para que se lea como lo que es.
- Agregar medición cliente `speech_end → primer audio` para cross-check con el
  número del servidor.

### 4. Tuning por CLI

Flags nuevas en el mock: `--silence-ms` (→ `hangover_ms`) y `--vad-aggressiveness`.
Ajuste en vivo sin editar código.

## Testing

Test unitario de `endpointer.py` con secuencias sintéticas de frames:
- Silencio puro → nunca corta antes de `min_speech`.
- Voz + silencio → corta tras `hangover_ms`.
- Voz continua sin silencio → corta al llegar a `max_utterance_ms`.

Lógica pura, sin hardware de audio. Encaja con el patrón de `tests/` existente
(pytest). Para frames "con voz" en el test se pueden usar muestras sintéticas que
`webrtcvad` clasifique como voz, o inyectar un VAD fake vía parámetro para aislar
la máquina de estados de la librería.

## Fuera de alcance (YAGNI)

- Barge-in automático (necesita AEC de hardware que la laptop no tiene).
- Modo conversación / follow-up.
- Reentrenamiento del wake word "Hola Michi".
- Cambios al protocolo WebSocket v1.
- Endpointing del servidor (se deja como está, de red de seguridad).

## Criterios de éxito

1. Con `--wake-word`, después de hablar y quedarte callado ~700ms, el robot
   responde **sin** que toques una tecla.
2. `stt_final` y `tts_first_chunk` ya no incluyen tiempo de reacción humana.
3. El número `speech_end→first_audio` del cliente concuerda (~±100ms) con el del
   servidor.
4. `endpointer.py` tiene tests unitarios que pasan sin hardware.
5. `--silence-ms` cambia visiblemente el "feeling" del corte.
