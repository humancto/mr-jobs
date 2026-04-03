"""
MR.Jobs AI Interviewer — Mock interview engine powered by Claude Brain.

Modes:
  - text:  CLI text chat (zero dependencies beyond ClaudeBrain)
  - voice: Real-time voice with Whisper STT + TTS
  - video: Voice + webcam frame analysis for engagement scoring

Integration:
  - Uses existing whisper-transcribe skill for STT
  - Uses existing video-analyzer skill for frame capture
  - Uses ClaudeBrain (claude -p) for interview logic + evaluation
  - Stores results in applications.db alongside job data
"""
