"""
OpenAI Realtime API — Real-time bidirectional audio interview streaming.

Uses OpenAI's Realtime API over WebSocket for native real-time conversations:
- Audio in → Audio + Text out (no separate STT/TTS needed)
- Sub-second latency for natural conversation flow
- Server-side VAD for automatic turn detection

Model: gpt-4o-realtime-preview-2024-12-17
"""

import os
import base64
import json
import logging
from typing import Optional, AsyncIterator

logger = logging.getLogger("interviewer.openai_realtime")

REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"


class OpenAIRealtimeSession:
    """
    Manages an OpenAI Realtime API session for real-time interviews.

    The session handles:
    - Streaming PCM16 24kHz mono audio input from browser mic
    - Receiving audio + text responses from OpenAI
    - System prompt for interview behavior
    - Server-side VAD for automatic turn detection
    """

    def __init__(self, system_prompt: str, voice: str = "alloy", api_key: str = ""):
        self.system_prompt = system_prompt
        self.voice = voice
        self._ws = None
        self._running = False

        # Transcript accumulation
        self.transcript_buffer = ""

        # API key: explicit param > env var
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY not found (set environment variable or pass via profile)")

    async def connect(self):
        """Establish the OpenAI Realtime WebSocket connection."""
        try:
            import websockets
        except ImportError:
            raise RuntimeError("websockets required: pip install websockets")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        self._ws = await websockets.connect(REALTIME_URL, additional_headers=headers)
        self._running = True

        # Configure the session
        session_config = {
            "type": "session.update",
            "session": {
                "instructions": self.system_prompt,
                "voice": self.voice,
                "modalities": ["text", "audio"],
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": {"type": "server_vad"},
                "input_audio_transcription": {"model": "whisper-1"},
            },
        }
        await self._ws.send(json.dumps(session_config))

        # Wait for session.updated confirmation
        try:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if msg.get("type") == "session.updated":
                logger.info("OpenAI Realtime session configured (voice=%s)", self.voice)
            elif msg.get("type") == "error":
                logger.error("Session config error: %s", msg.get("error", {}).get("message", "unknown"))
            else:
                logger.debug("First message type: %s", msg.get("type"))
        except Exception as e:
            logger.warning("Could not read session confirmation: %s", e)

        logger.info("OpenAI Realtime session connected")

    async def send_audio(self, pcm_bytes: bytes):
        """
        Stream raw audio to OpenAI.

        Args:
            pcm_bytes: Raw PCM16 audio at 24kHz mono
        """
        if not self._ws:
            return
        try:
            audio_b64 = base64.b64encode(pcm_bytes).decode("ascii")
            event = {
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            }
            await self._ws.send(json.dumps(event))
        except Exception as e:
            logger.error("Error sending audio: %s", e)

    async def send_text(self, text: str):
        """Send text input (for hybrid text+voice mode)."""
        if not self._ws:
            return
        try:
            # Create a user message item
            create_event = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": text,
                        }
                    ],
                },
            }
            await self._ws.send(json.dumps(create_event))

            # Request the model to respond
            response_event = {
                "type": "response.create",
            }
            await self._ws.send(json.dumps(response_event))
        except Exception as e:
            logger.error("Error sending text: %s", e)

    async def receive_responses(self) -> AsyncIterator[dict]:
        """
        Async generator that yields response events from OpenAI.

        Yields dicts with:
            {"type": "text", "text": "..."}           — transcript delta
            {"type": "audio", "data": bytes}           — PCM16 audio chunk
            {"type": "turn_complete", "text": "..."}   — full turn transcript
            {"type": "speech_started"}                 — user started speaking
            {"type": "speech_stopped"}                 — user stopped speaking
            {"type": "error", "message": "..."}        — error
        """
        if not self._ws:
            return

        try:
            async for raw in self._ws:
                if not self._running:
                    break

                msg = json.loads(raw)
                event_type = msg.get("type", "")

                # Audio response delta
                if event_type == "response.audio.delta":
                    audio_b64 = msg.get("delta", "")
                    if audio_b64:
                        audio_bytes = base64.b64decode(audio_b64)
                        yield {"type": "audio", "data": audio_bytes}

                # Text transcript delta
                elif event_type == "response.audio_transcript.delta":
                    delta = msg.get("delta", "")
                    if delta:
                        self.transcript_buffer += delta
                        yield {"type": "text", "text": delta}

                # Response completed — turn is done
                elif event_type == "response.done":
                    yield {"type": "turn_complete", "text": self.transcript_buffer}
                    self.transcript_buffer = ""

                # User started speaking (VAD detected speech)
                elif event_type == "input_audio_buffer.speech_started":
                    yield {"type": "speech_started"}

                # User stopped speaking (VAD detected silence)
                elif event_type == "input_audio_buffer.speech_stopped":
                    yield {"type": "speech_stopped"}

                # Error from server
                elif event_type == "error":
                    error_msg = msg.get("error", {}).get("message", "unknown error")
                    logger.error("OpenAI Realtime error: %s", error_msg)
                    yield {"type": "error", "message": error_msg}

        except Exception as e:
            if self._running:
                logger.error("OpenAI Realtime receive error: %s", e)
            yield {"type": "error", "message": str(e)}

    async def close(self):
        """Close the OpenAI Realtime session."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("OpenAI Realtime session closed")

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._running


def is_openai_realtime_available() -> bool:
    """Check if OpenAI Realtime API is available (API key set + websockets installed)."""
    if not os.environ.get("OPENAI_API_KEY"):
        return False
    try:
        import websockets
        return True
    except ImportError:
        return False
