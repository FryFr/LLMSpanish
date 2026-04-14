"""Record a 5-second mono 16kHz WAV from the default mic.

Usage:
    uv run python scripts/record_sample.py [seconds] [output.wav]
"""

from __future__ import annotations

import sys
from pathlib import Path

import sounddevice as sd
import soundfile as sf


def main(seconds: float, output: Path) -> None:
    sample_rate = 16000
    print(f"Grabando {seconds:.1f}s a {sample_rate} Hz mono...")
    print("Hablá ahora.")
    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
    )
    sd.wait()
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), audio, sample_rate, subtype="PCM_16")
    print(f"Guardado en {output}")


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("assets/test/sample.wav")
    main(seconds, output)
