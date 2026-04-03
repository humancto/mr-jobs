"""
Video Vision — Webcam capture and engagement analysis for video interviews.

Dependencies (pip install):
  - opencv-python  (webcam capture)

Uses ClaudeBrain for engagement scoring via vision analysis.
No external skill imports — uses cv2 directly.
"""

import base64
import time
import logging
import threading
from typing import Optional, Callable

logger = logging.getLogger("interviewer.vision")

# Engagement analysis prompt for ClaudeBrain
ENGAGEMENT_PROMPT = """Analyze this interview candidate's engagement from the webcam frame.

Rate engagement 1-5:
1 = Disengaged (looking away, distracted, slouching)
2 = Low (minimal eye contact, fidgeting)
3 = Neutral (adequate posture, occasional eye contact)
4 = Engaged (good eye contact, attentive posture, nodding)
5 = Highly engaged (excellent eye contact, animated, leaning in)

Note: body language, facial expressions, posture, eye contact.

Return ONLY valid JSON:
{"score": <1-5>, "notes": "<brief observation>"}"""


def _import_cv2():
    """Import OpenCV with helpful error message."""
    try:
        import cv2
        return cv2
    except ImportError:
        raise ImportError(
            "OpenCV not installed. Required for video mode:\n"
            "  pip install opencv-python"
        )


def check_webcam() -> dict:
    """
    Check if a webcam is available.

    Returns:
        Dict with "available" (bool), "error" (str or None)
    """
    try:
        cv2 = _import_cv2()
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret:
                h, w = frame.shape[:2]
                return {"available": True, "resolution": f"{w}x{h}", "error": None}
        cap.release()
        return {"available": False, "error": "Webcam not accessible"}
    except ImportError as e:
        return {"available": False, "error": str(e)}
    except Exception as e:
        return {"available": False, "error": str(e)}


def capture_frame(quality: int = 70) -> Optional[str]:
    """
    Capture a single frame from the webcam.

    Args:
        quality: JPEG compression quality (1-100)

    Returns:
        Base64-encoded JPEG string, or None on failure
    """
    cv2 = _import_cv2()

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        logger.error("Cannot open webcam")
        return None

    try:
        ret, frame = cap.read()
        if not ret:
            logger.error("Failed to capture frame")
            return None

        # Resize to reduce payload (640px wide max)
        h, w = frame.shape[:2]
        if w > 640:
            scale = 640 / w
            frame = cv2.resize(frame, (640, int(h * scale)))

        # Encode as JPEG
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
        _, buf = cv2.imencode(".jpg", frame, encode_params)
        return base64.b64encode(buf).decode("utf-8")

    finally:
        cap.release()


def analyze_engagement(frame_b64: str, brain) -> dict:
    """
    Analyze candidate engagement from a webcam frame using ClaudeBrain.

    Uses multimodal vision (Gemini/OpenAI) when available, sending the
    actual image bytes for real visual analysis.

    Args:
        frame_b64: Base64-encoded JPEG frame
        brain: ClaudeBrain instance

    Returns:
        {"score": 1-5, "notes": "observation"}
    """
    try:
        # Decode base64 to actual image bytes for vision analysis
        image_bytes = base64.b64decode(frame_b64)
        result = brain.ask_vision_json(
            ENGAGEMENT_PROMPT,
            image_bytes,
            "image/jpeg",
            timeout=20,
            component="interview",
        )
        if result and "score" in result:
            return result
    except Exception as e:
        logger.error(f"Engagement analysis failed: {e}")

    return {"score": 3, "notes": "analysis unavailable"}


class WebcamMonitor:
    """
    Background webcam monitoring thread.

    Captures frames at a configurable interval and runs engagement analysis.
    Results are fed back to the InterviewSession via a callback.
    """

    def __init__(
        self,
        brain,
        on_engagement: Callable[[int, str], None],
        interval: float = 15.0,
    ):
        """
        Args:
            brain: ClaudeBrain instance for vision analysis
            on_engagement: Callback(score, notes) — called with each analysis result
            interval: Seconds between frame captures (default 15s)
        """
        self.brain = brain
        self.on_engagement = on_engagement
        self.interval = interval
        self._running = False
        self._thread = None

    def start(self):
        """Start background webcam monitoring."""
        if self._running:
            return

        # Verify webcam access
        check = check_webcam()
        if not check["available"]:
            logger.error(f"Webcam not available: {check['error']}")
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(f"Webcam monitor started (every {self.interval}s)")

    def stop(self):
        """Stop background monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Webcam monitor stopped")

    def _monitor_loop(self):
        """Background loop: capture → analyze → callback."""
        while self._running:
            try:
                frame = capture_frame()
                if frame:
                    result = analyze_engagement(frame, self.brain)
                    score = result.get("score", 3)
                    notes = result.get("notes", "")
                    self.on_engagement(score, notes)
                    logger.debug(f"Engagement: {score}/5 — {notes}")
            except Exception as e:
                logger.error(f"Monitor error: {e}")

            # Sleep in small increments so stop() is responsive
            for _ in range(int(self.interval * 10)):
                if not self._running:
                    return
                time.sleep(0.1)
