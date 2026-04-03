"""
Text-to-Speech — Speaks interviewer responses aloud.

Strategy:
  1. macOS `say` command (zero deps, works immediately)
  2. pyttsx3 fallback (cross-platform, pip install pyttsx3)

Streams text sentence-by-sentence for low perceived latency.
"""

import subprocess
import platform
import logging
import re
import threading
from typing import Optional

logger = logging.getLogger("interviewer.tts")

# Active speech process (for interruption)
_active_process = None
_speech_lock = threading.Lock()


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _split_sentences(text: str) -> list:
    """Split text into sentences for streaming TTS."""
    # Split on sentence-ending punctuation followed by space or end
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p for p in parts if p.strip()]


def speak(text: str, voice: str = "Samantha", rate: int = 185, block: bool = True):
    """
    Speak text aloud using the best available TTS engine.

    Args:
        text: Text to speak
        voice: Voice name (macOS: Samantha, Alex, Daniel, Karen, etc.)
        rate: Speech rate in words per minute
        block: If True, wait for speech to complete
    """
    global _active_process

    if not text or not text.strip():
        return

    if _is_macos():
        _speak_macos(text, voice=voice, rate=rate, block=block)
    else:
        _speak_pyttsx3(text, rate=rate, block=block)


def _speak_macos(text: str, voice: str = "Samantha", rate: int = 185, block: bool = True):
    """Speak using macOS `say` command."""
    global _active_process

    sentences = _split_sentences(text)

    for sentence in sentences:
        with _speech_lock:
            try:
                _active_process = subprocess.Popen(
                    ["say", "-v", voice, "-r", str(rate), sentence],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if block:
                    _active_process.wait()
            except FileNotFoundError:
                logger.warning("macOS `say` not available, falling back to pyttsx3")
                _speak_pyttsx3(text, rate=rate, block=block)
                return
            except Exception as e:
                logger.error(f"TTS error: {e}")
            finally:
                _active_process = None


def _speak_pyttsx3(text: str, rate: int = 185, block: bool = True):
    """Speak using pyttsx3 (cross-platform fallback)."""
    try:
        import pyttsx3
    except ImportError:
        logger.error(
            "No TTS engine available. Install pyttsx3:\n"
            "  pip install pyttsx3"
        )
        # Fallback: just print
        print(f"\n[TTS unavailable] {text}")
        return

    engine = pyttsx3.init()
    engine.setProperty("rate", rate)

    if block:
        engine.say(text)
        engine.runAndWait()
    else:
        def _speak_async():
            engine.say(text)
            engine.runAndWait()
        threading.Thread(target=_speak_async, daemon=True).start()


def speak_to_bytes(text: str, voice: str = "Samantha", rate: int = 185) -> bytes:
    """
    Convert text to WAV audio bytes (for streaming to browser).

    Returns WAV data as bytes, or empty bytes if TTS is unavailable.
    """
    if not text or not text.strip():
        return b""

    if _is_macos():
        return _speak_to_bytes_macos(text, voice=voice, rate=rate)
    return _speak_to_bytes_pyttsx3(text, rate=rate)


def _speak_to_bytes_macos(text: str, voice: str = "Samantha", rate: int = 185) -> bytes:
    """Generate WAV bytes using macOS `say` command."""
    import tempfile
    import wave

    with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp_aiff:
        aiff_path = tmp_aiff.name

    wav_path = aiff_path.replace(".aiff", ".wav")

    try:
        # say outputs AIFF by default
        subprocess.run(
            ["say", "-v", voice, "-r", str(rate), "-o", aiff_path, text],
            capture_output=True, timeout=30,
        )

        # Convert AIFF to WAV using afconvert (built into macOS)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", aiff_path, wav_path],
            capture_output=True, timeout=10,
        )

        with open(wav_path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.error(f"TTS to bytes error: {e}")
        return b""
    finally:
        import os
        for p in (aiff_path, wav_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _speak_to_bytes_pyttsx3(text: str, rate: int = 185) -> bytes:
    """Generate WAV bytes using pyttsx3."""
    import tempfile

    try:
        import pyttsx3
    except ImportError:
        logger.error("No TTS engine available for byte output")
        return b""

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    try:
        engine = pyttsx3.init()
        engine.setProperty("rate", rate)
        engine.save_to_file(text, wav_path)
        engine.runAndWait()

        with open(wav_path, "rb") as f:
            return f.read()
    except Exception as e:
        logger.error(f"pyttsx3 to bytes error: {e}")
        return b""
    finally:
        import os
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def stop():
    """Stop any currently playing speech."""
    global _active_process
    with _speech_lock:
        if _active_process and _active_process.poll() is None:
            _active_process.terminate()
            _active_process = None


def list_voices() -> list:
    """List available voices (macOS only)."""
    if not _is_macos():
        return []

    try:
        result = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True, text=True, timeout=5,
        )
        voices = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                # Format: "Name    language_code    # Sample text"
                parts = line.split()
                if parts:
                    voices.append(parts[0])
        return voices
    except Exception:
        return []
