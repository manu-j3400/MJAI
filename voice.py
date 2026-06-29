"""Push-to-talk voice recording + Whisper transcription (local, no API key)."""
import logging
import threading
from typing import Optional

import numpy as np
import sounddevice as sd
import whisper

SAMPLE_RATE = 16000
CHANNELS = 1

log = logging.getLogger(__name__)

# Load once at import time — "tiny" is fast, runs on CPU
_model = whisper.load_model("tiny")
log.info("Whisper model loaded.")


def record(stop_event: threading.Event) -> np.ndarray:
    """Record from mic until stop_event is set. Returns int16 numpy array."""
    chunks = []
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16") as stream:
            while not stop_event.is_set():
                data, _ = stream.read(512)
                chunks.append(data.copy())
    except Exception as e:
        log.warning("Recording error: %s", e)
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype="int16")


def transcribe(audio_arr: np.ndarray) -> Optional[str]:
    """Transcribe int16 audio using local Whisper model."""
    if len(audio_arr) < SAMPLE_RATE * 0.4:
        return None

    # Whisper expects float32 in [-1, 1] at 16kHz
    audio_float = audio_arr.flatten().astype(np.float32) / 32768.0
    # Normalize to 50% full scale so quiet mics still transcribe cleanly
    max_amp = np.abs(audio_float).max()
    if max_amp > 0.001:
        audio_float = audio_float / max_amp * 0.5
    try:
        result = _model.transcribe(audio_float, language="en", fp16=False)
        text = result["text"].strip()
        log.info("Transcribed: %r", text)
        return text or None
    except Exception as e:
        log.warning("Transcription error: %s", e)
        return None
