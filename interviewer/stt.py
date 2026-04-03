"""
Speech-to-Text — Wraps existing Whisper transcription skill for interview use.

Strategy: File-based transcription (record → WAV → Whisper → text).
Reuses load_whisper_model() from whisper-transcribe skill.
"""

import sys
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("interviewer.stt")

# Cached model instance (load once, reuse across transcriptions)
_whisper_model = None
_model_name = None


def _get_whisper():
    """Import the whisper module, trying skill path first then direct import."""
    skill_path = Path.home() / ".claude/skills/whisper-transcribe/scripts"
    if skill_path.exists() and str(skill_path) not in sys.path:
        sys.path.insert(0, str(skill_path))

    try:
        import whisper
        return whisper
    except ImportError:
        raise ImportError(
            "OpenAI Whisper not installed. Install with:\n"
            "  pip install openai-whisper\n"
            "Or for faster inference:\n"
            "  pip install whisper-at"
        )


def load_model(model_name: str = "base") -> object:
    """
    Load Whisper model (cached singleton).

    Args:
        model_name: Whisper model size — "tiny", "base", "small", "medium", "large"
                    "base" is recommended for interviews (fast, good English accuracy)
    """
    global _whisper_model, _model_name

    if _whisper_model is not None and _model_name == model_name:
        return _whisper_model

    whisper = _get_whisper()
    logger.info(f"Loading Whisper model '{model_name}'...")
    _whisper_model = whisper.load_model(model_name)
    _model_name = model_name
    logger.info(f"Whisper model '{model_name}' loaded.")
    return _whisper_model


def transcribe_file(wav_path: str, model: object = None, language: str = "en") -> str:
    """
    Transcribe a WAV file to text using Whisper.

    Args:
        wav_path: Path to WAV audio file (16kHz mono recommended)
        model: Pre-loaded Whisper model (loads default if None)
        language: Language hint (default "en" for English)

    Returns:
        Transcribed text string
    """
    if model is None:
        model = load_model()

    logger.info(f"Transcribing {wav_path}...")
    result = model.transcribe(
        str(wav_path),
        language=language,
        fp16=False,  # Safe for CPU/MPS
    )
    text = result.get("text", "").strip()
    logger.info(f"Transcribed: {text[:80]}...")
    return text


def transcribe_audio_bytes(audio_bytes: bytes, sample_rate: int = 16000,
                           model: object = None, language: str = "en") -> str:
    """
    Transcribe raw audio bytes (PCM int16) to text.

    Args:
        audio_bytes: Raw PCM audio data
        sample_rate: Sample rate of the audio
        model: Pre-loaded Whisper model
        language: Language hint

    Returns:
        Transcribed text string
    """
    import wave

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
        with wave.open(f, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(audio_bytes)

    try:
        return transcribe_file(tmp_path, model=model, language=language)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def get_device_info() -> dict:
    """Get info about available compute devices for Whisper."""
    info = {"device": "cpu", "gpu": False}
    try:
        import torch
        if torch.cuda.is_available():
            info["device"] = "cuda"
            info["gpu"] = True
            info["gpu_name"] = torch.cuda.get_device_name(0)
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            info["device"] = "mps"
            info["gpu"] = True
            info["gpu_name"] = "Apple Silicon (MPS)"
    except ImportError:
        pass
    return info
