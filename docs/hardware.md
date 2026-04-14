# Hardware — decisiones y razonamiento

## Un solo chip: ESP32-S3

El proyecto **ElectronBot** original usa un **STM32F405** como master de servos +
display y un **ESP32-C3** auxiliar sólo para WiFi. **EchoEar** usa un **ESP32-S3**
con soporte de audio nativo, PSRAM y el framework `esp-sr` (AEC + VAD + wake word).

Para ElectronBot-ES decidimos **descartar el STM32 y usar un único ESP32-S3**
que maneje audio + WiFi + servos + display. Razones:

1. **Unificación de la stack de desarrollo** — todo corre en ESP-IDF, un solo toolchain.
2. **Audio nativo** — I2S, AFE de Espressif y microWakeWord son ciudadanos
   de primera clase en el S3.
3. **PSRAM suficiente** — 8 MB permiten buffers grandes de audio y framebuffer
   del display GC9A01.
4. **Costo y BOM más simple** — un chip menos, menos interconexiones.

**Consecuencia**: el firmware de servos de ElectronBot (que está en STM32 HAL)
**no se reutiliza** — hay que portarlo a ESP-IDF. Eso es Week 2-3.

Nota: los **servos individuales** de ElectronBot tienen su propio STM32F042
dentro del encoder/driver — esos no cambian. Lo que migra es el **master** que
les habla por I2C.

## ¿Por qué no LLM on-device?

Porque no entra. El ESP32-S3 tiene 8 MB de PSRAM como máximo. Un LLM 3B Q4
pesa ~2 GB, es 250× más grande que lo que el chip puede alojar.

Lo único que corre on-device es:

- **Wake word** (microWakeWord, ~50 KB)
- **AFE** (noise suppression + AEC + VAD, ~1 MB de código)
- **Keyword spotting limitado** (opcional, vía ESP-SR)

Todo lo pesado (STT, LLM, TTS) vive en el backend cloud.

## Hardware físico previsto

| Componente        | Modelo                    | Notas                                       |
|-------------------|---------------------------|---------------------------------------------|
| MCU               | ESP32-S3 (≥8 MB PSRAM)    | ESP32-S3-DevKitC-1 para desarrollo          |
| Micrófono         | INMP441 (I2S)             | Mono, 16 kHz                                |
| Amplificador audio| MAX98357A (I2S)           | Mono, clase D                               |
| Parlante          | 4Ω 3 W                    |                                             |
| Display           | GC9A01 240×240 redondo    | SPI, usado como "cara" del robot            |
| Servos            | Los de ElectronBot        | STM32F042 interno, hablan I2C al master     |
| Alimentación      | 5 V ≥ 3 A                 | Servos consumen picos — no alimentar por USB|

## Alimentación — advertencia

Los servos pueden generar picos de corriente que **resetean el ESP32** si
comparten la misma línea de 5 V con alimentación inadecuada. Fuente separada
o batería LiPo con regulador dedicado. Resolvelo **antes** de debuguear
cualquier otro problema de firmware.
