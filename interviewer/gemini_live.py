"""
Gemini Live API — Real-time bidirectional audio+video interview streaming.

Uses Google's Gemini Live API for native real-time conversations:
- Audio in → Audio + Text out (no separate STT/TTS needed)
- Video frames can be sent alongside audio for multimodal analysis
- Sub-second latency for natural conversation flow

Model: gemini-2.0-flash-live-001
"""

import os
import base64
import logging
import json
from typing import Optional, AsyncIterator

logger = logging.getLogger("interviewer.gemini_live")


def _get_gemini_client(api_key: str = ""):
    """Create a Gemini client with API key from param or environment."""
    try:
        from google import genai
    except ImportError:
        raise RuntimeError("google-genai required: pip install google-genai")

    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not found (set environment variable or pass via profile)")

    return genai.Client(api_key=key)


class GeminiLiveSession:
    """
    Manages a Gemini Live API session for real-time interviews.

    The session handles:
    - Streaming audio input from browser mic
    - Streaming video frames from browser webcam
    - Receiving audio + text responses from Gemini
    - System prompt for interview behavior
    - Engagement analysis from video frames
    """

    def __init__(self, system_prompt: str, voice: str = "Puck", api_key: str = ""):
        self.system_prompt = system_prompt
        self.voice = voice
        self._session = None
        self._client = None
        self._ctx_manager = None  # holds the async context manager
        self._running = False
        self._api_key = api_key

        # Transcript accumulation
        self.transcript_buffer = ""
        self.engagement_scores = []

    async def connect(self):
        """Establish the Gemini Live connection."""
        from google.genai import types

        self._client = _get_gemini_client(api_key=self._api_key)

        # live.connect() returns an async context manager — enter it manually
        # so the session stays open for the lifetime of this object
        self._ctx_manager = self._client.aio.live.connect(
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            config=types.LiveConnectConfig(
                response_modalities=["AUDIO"],
                system_instruction=types.Content(
                    parts=[types.Part(text=self.system_prompt)]
                ),
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=self.voice,
                        )
                    )
                ),
                thinking_config=types.ThinkingConfig(
                    thinking_budget=0,
                ),
            ),
        )
        self._session = await self._ctx_manager.__aenter__()
        self._running = True
        logger.info("Gemini Live session connected (voice=%s)", self.voice)

    async def send_audio(self, pcm_bytes: bytes):
        """
        Stream raw audio to Gemini.

        Args:
            pcm_bytes: Raw PCM16 audio at 16kHz mono
        """
        if not self._session:
            return
        try:
            from google.genai import types
            await self._session.send_realtime_input(
                audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm"),
            )
        except Exception as e:
            logger.error("Error sending audio: %s", e)

    async def send_video_frame(self, jpeg_bytes: bytes):
        """
        Send a video frame to Gemini for multimodal analysis.

        Args:
            jpeg_bytes: JPEG-encoded webcam frame
        """
        if not self._session:
            return
        try:
            from google.genai import types
            await self._session.send_realtime_input(
                video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg"),
            )
        except Exception as e:
            logger.error("Error sending video frame: %s", e)

    async def send_text(self, text: str):
        """Send text input (for hybrid text+voice mode)."""
        if not self._session:
            return
        try:
            from google.genai import types
            await self._session.send_client_content(
                turns=[types.Content(
                    role="user",
                    parts=[types.Part(text=text)],
                )],
                turn_complete=True,
            )
        except Exception as e:
            logger.error("Error sending text: %s", e)

    async def send_activity_start(self):
        """Signal that the user started speaking."""
        if not self._session:
            return
        try:
            from google.genai import types
            await self._session.send_realtime_input(
                activity_start=types.ActivityStart(),
            )
        except Exception as e:
            logger.debug("Activity start signal: %s", e)

    async def send_activity_end(self):
        """Signal that the user stopped speaking."""
        if not self._session:
            return
        try:
            from google.genai import types
            await self._session.send_realtime_input(
                activity_end=types.ActivityEnd(),
            )
        except Exception as e:
            logger.debug("Activity end signal: %s", e)

    async def receive_responses(self) -> AsyncIterator[dict]:
        """
        Async generator that yields response events from Gemini.

        Yields dicts with:
            {"type": "text", "text": "..."}
            {"type": "audio", "data": bytes}
            {"type": "turn_complete"}
        """
        if not self._session:
            return

        try:
            async for message in self._session.receive():
                if not self._running:
                    break

                # Check for text and audio in server_content
                server_content = getattr(message, "server_content", None)
                if server_content:
                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn and model_turn.parts:
                        for part in model_turn.parts:
                            # Text response
                            if hasattr(part, "text") and part.text:
                                self.transcript_buffer += part.text
                                yield {"type": "text", "text": part.text}
                            # Audio response (inline_data)
                            if hasattr(part, "inline_data") and part.inline_data:
                                yield {"type": "audio", "data": part.inline_data.data}

                    # Check for turn completion
                    if getattr(server_content, "turn_complete", False):
                        yield {"type": "turn_complete", "text": self.transcript_buffer}
                        self.transcript_buffer = ""

        except Exception as e:
            if self._running:
                logger.error("Gemini Live receive error: %s", e)
            yield {"type": "error", "message": str(e)}

    async def close(self):
        """Close the Gemini Live session."""
        self._running = False
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
        if self._ctx_manager:
            try:
                await self._ctx_manager.__aexit__(None, None, None)
            except Exception:
                pass
            self._ctx_manager = None
        logger.info("Gemini Live session closed")

    @property
    def is_connected(self) -> bool:
        return self._session is not None and self._running


def is_gemini_live_available() -> bool:
    """Check if Gemini Live API is available (API key set + SDK installed)."""
    if not os.environ.get("GEMINI_API_KEY"):
        return False
    try:
        from google import genai
        return True
    except ImportError:
        return False
