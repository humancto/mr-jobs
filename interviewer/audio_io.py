"""
Audio I/O — Microphone capture and audio playback for voice interviews.

Dependencies (pip install):
  - sounddevice   (mic capture)
  - numpy         (audio processing)

These are standard packages, no external skill imports.
"""

import wave
import time
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("interviewer.audio_io")

# Default audio config
SAMPLE_RATE = 16000   # 16kHz — Whisper's native rate
CHANNELS = 1          # Mono
SAMPLE_WIDTH = 2      # 16-bit PCM
CHUNK_DURATION = 0.1  # 100ms chunks for VAD


def _import_sounddevice():
    """Import sounddevice with helpful error message."""
    try:
        import sounddevice as sd
        return sd
    except ImportError:
        raise ImportError(
            "sounddevice not installed. Required for voice mode:\n"
            "  pip install sounddevice\n"
            "On macOS, you may also need: brew install portaudio"
        )


def _import_numpy():
    """Import numpy with helpful error message."""
    try:
        import numpy as np
        return np
    except ImportError:
        raise ImportError("numpy not installed: pip install numpy")


def record_until_silence(
    silence_threshold: float = 0.01,
    silence_duration: float = 2.0,
    max_duration: float = 120.0,
    sample_rate: int = SAMPLE_RATE,
    pre_speech_timeout: float = 30.0,
) -> Optional[str]:
    """
    Record audio from microphone until silence is detected.

    Uses energy-based Voice Activity Detection (VAD):
    - Waits for speech to start (energy above threshold)
    - Records until silence lasts longer than silence_duration
    - Saves to a temporary WAV file

    Args:
        silence_threshold: RMS energy threshold (0.0-1.0) — lower = more sensitive
        silence_duration: Seconds of silence before stopping
        max_duration: Maximum recording duration in seconds
        sample_rate: Audio sample rate (16kHz for Whisper)
        pre_speech_timeout: Max seconds to wait before speech starts

    Returns:
        Path to recorded WAV file, or None if no speech detected
    """
    sd = _import_sounddevice()
    np = _import_numpy()

    chunk_samples = int(sample_rate * CHUNK_DURATION)
    audio_chunks = []
    speech_started = False
    silence_start = None
    recording_start = time.time()

    logger.info("Listening... (speak when ready)")

    def _rms(data):
        """Calculate root mean square energy of audio chunk."""
        return float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) / 32768.0

    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype="int16",
            blocksize=chunk_samples,
        ) as stream:
            while True:
                elapsed = time.time() - recording_start

                # Read audio chunk
                data, overflowed = stream.read(chunk_samples)
                if overflowed:
                    logger.debug("Audio buffer overflow")

                energy = _rms(data)

                if not speech_started:
                    # Waiting for speech to begin
                    if energy > silence_threshold:
                        speech_started = True
                        silence_start = None
                        audio_chunks.append(data.copy())
                        logger.info("Speech detected, recording...")
                    elif elapsed > pre_speech_timeout:
                        logger.warning("No speech detected within timeout")
                        return None
                else:
                    # Recording speech
                    audio_chunks.append(data.copy())

                    if energy < silence_threshold:
                        if silence_start is None:
                            silence_start = time.time()
                        elif time.time() - silence_start >= silence_duration:
                            logger.info("Silence detected, stopping recording.")
                            break
                    else:
                        silence_start = None

                    if elapsed >= max_duration:
                        logger.info("Max recording duration reached.")
                        break

    except Exception as e:
        logger.error(f"Recording error: {e}")
        return None

    if not audio_chunks:
        return None

    # Combine chunks and save to WAV
    audio_data = np.concatenate(audio_chunks)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
        with wave.open(f, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_data.tobytes())

    duration = len(audio_data) / sample_rate
    logger.info(f"Recorded {duration:.1f}s of audio → {wav_path}")
    return wav_path


def check_microphone() -> dict:
    """
    Check if a microphone is available and working.

    Returns:
        Dict with "available" (bool), "device" (str), "error" (str or None)
    """
    try:
        sd = _import_sounddevice()
        devices = sd.query_devices()
        default_input = sd.query_devices(kind="input")
        return {
            "available": True,
            "device": default_input.get("name", "Unknown"),
            "sample_rate": int(default_input.get("default_samplerate", SAMPLE_RATE)),
            "error": None,
        }
    except ImportError as e:
        return {"available": False, "device": None, "error": str(e)}
    except Exception as e:
        return {"available": False, "device": None, "error": f"No microphone: {e}"}


def cleanup_temp_audio(wav_path: str):
    """Delete temporary audio file."""
    try:
        Path(wav_path).unlink(missing_ok=True)
    except Exception:
        pass
