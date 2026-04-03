"""
Interview CLI — Command-line interface for text, voice, and video mock interviews.

Usage:
    python main.py interview --mode text --role "SWE" --company "Stripe" --type behavioral
    python main.py interview --mode voice --type technical --difficulty senior
    python main.py interview --mode video --job-id abc123
    python main.py interview --mode text --output report.json

Dependencies:
  - Text mode: zero deps (just ClaudeBrain)
  - Voice mode: sounddevice, numpy, openai-whisper
  - Video mode: voice deps + opencv-python
"""

import asyncio
import sys
import time
import json
import logging
from pathlib import Path

logger = logging.getLogger("interviewer.cli")


def _print_banner(mode: str, config: dict):
    """Print interview session banner."""
    print("\n" + "=" * 60)
    print("  MR.Jobs AI INTERVIEWER")
    print("=" * 60)
    print(f"  Mode:       {mode.upper()}")
    print(f"  Role:       {config['job_title']}")
    print(f"  Company:    {config['company']}")
    print(f"  Type:       {config['interview_type']}")
    print(f"  Difficulty: {config['difficulty']}")
    print(f"  Duration:   {config['duration']} minutes")
    print("=" * 60)

    if mode == "text":
        print("\n  Type your answers. Press Enter to submit.")
        print("  Type 'quit' or 'exit' to end the interview early.")
    elif mode == "voice":
        print("\n  Speak your answers. The interviewer will listen.")
        print("  Press Ctrl+C to end the interview early.")
    elif mode == "video":
        print("\n  Speak your answers. Webcam is active for engagement analysis.")
        print("  Press Ctrl+C to end the interview early.")

    print("-" * 60 + "\n")


def _print_interviewer(text: str):
    """Print interviewer's message with formatting."""
    print(f"\n  Interviewer: {text}\n")


def _print_evaluation(evaluation: dict):
    """Print evaluation results nicely."""
    if "error" in evaluation:
        print(f"\n  Evaluation error: {evaluation['error']}")
        return

    print("\n" + "=" * 60)
    print("  INTERVIEW EVALUATION")
    print("=" * 60)

    score = evaluation.get("overall_score", "N/A")
    rec = evaluation.get("recommendation", "N/A")
    print(f"\n  Overall Score: {score}/5")
    print(f"  Recommendation: {rec}")

    # Dimension scores
    dims = evaluation.get("dimensions", {})
    if dims:
        print("\n  Dimension Scores:")
        for key, dim in dims.items():
            name = key.replace("_", " ").title()
            s = dim.get("score", "?")
            print(f"    {name}: {s}/5")
            if dim.get("feedback"):
                print(f"      {dim['feedback']}")

    # Strengths
    strengths = evaluation.get("strengths", [])
    if strengths:
        print("\n  Strengths:")
        for s in strengths:
            print(f"    + {s}")

    # Areas for improvement
    areas = evaluation.get("areas_for_improvement", [])
    if areas:
        print("\n  Areas to Improve:")
        for a in areas:
            print(f"    - {a}")

    # Detailed feedback
    feedback = evaluation.get("detailed_feedback", "")
    if feedback:
        print(f"\n  Feedback:\n  {feedback}")

    # Practice suggestions
    practice = evaluation.get("suggested_practice", [])
    if practice:
        print("\n  Suggested Practice:")
        for p in practice:
            print(f"    * {p}")

    print("\n" + "=" * 60)


async def _run_text_interview(session):
    """Run a text-based interview (stdin/stdout)."""
    # Start interview
    opening = session.start()
    _print_interviewer(opening)

    # Conversation loop
    while session.state == "active":
        try:
            answer = input("  You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  [Interview ended by candidate]")
            break

        if not answer:
            continue
        if answer.lower() in ("quit", "exit", "q"):
            print("\n  [Ending interview...]")
            break

        response = session.respond(answer)
        _print_interviewer(response)

    # End and evaluate
    closing = session.end()
    if closing:
        _print_interviewer(closing)

    print("\n  Evaluating your performance...\n")
    evaluation = session.evaluate()
    _print_evaluation(evaluation)

    return evaluation


async def _run_voice_interview(session):
    """Run a voice-based interview (mic + TTS)."""
    from interviewer.stt import load_model, transcribe_file
    from interviewer.tts import speak, stop as stop_tts
    from interviewer.audio_io import record_until_silence, check_microphone, cleanup_temp_audio

    # Check microphone
    mic_check = check_microphone()
    if not mic_check["available"]:
        print(f"\n  Microphone error: {mic_check['error']}")
        print("  Falling back to text mode.\n")
        return await _run_text_interview(session)

    print(f"  Microphone: {mic_check['device']}")

    # Load Whisper model
    print("  Loading speech recognition model...")
    model = load_model("base")
    print("  STT ready.\n")

    # Start interview
    opening = session.start()
    _print_interviewer(opening)
    speak(opening)

    # Conversation loop
    while session.state == "active":
        try:
            print("  [Listening...]")
            wav_path = record_until_silence()

            if not wav_path:
                print("  [No speech detected. Try again or press Ctrl+C to end.]")
                continue

            # Transcribe
            print("  [Transcribing...]")
            text = transcribe_file(wav_path, model=model)
            cleanup_temp_audio(wav_path)

            if not text or not text.strip():
                print("  [Could not understand. Please try again.]")
                continue

            print(f"  You: {text}")

            # Get interviewer response
            response = session.respond(text)
            _print_interviewer(response)

            # Stop any previous TTS and speak new response
            stop_tts()
            speak(response)

        except KeyboardInterrupt:
            print("\n\n  [Interview ended by candidate]")
            stop_tts()
            break

    # End and evaluate
    closing = session.end()
    if closing:
        _print_interviewer(closing)
        speak(closing)

    print("\n  Evaluating your performance...\n")
    evaluation = session.evaluate()
    _print_evaluation(evaluation)

    return evaluation


async def _run_video_interview(session):
    """Run a video-based interview (voice + webcam engagement analysis)."""
    from interviewer.vision import check_webcam, WebcamMonitor

    # Check webcam
    cam_check = check_webcam()
    if not cam_check["available"]:
        print(f"\n  Webcam error: {cam_check['error']}")
        print("  Falling back to voice mode (no video).\n")
        session.video_enabled = False
        return await _run_voice_interview(session)

    print(f"  Webcam: {cam_check['resolution']}")

    # Start webcam monitor
    monitor = WebcamMonitor(
        brain=session.brain,
        on_engagement=lambda score, notes: session.add_engagement_score(score, notes),
        interval=15.0,
    )
    monitor.start()

    try:
        # Run the voice interview loop (with webcam running in background)
        result = await _run_voice_interview(session)
    finally:
        monitor.stop()

    return result


async def cmd_interview(args, profile: dict, brain):
    """
    Main interview command handler — called from main.py.

    Args:
        args: Parsed argparse arguments
        profile: User's profile.yaml dict
        brain: ClaudeBrain instance
    """
    from interviewer.engine import InterviewSession

    mode = getattr(args, "mode", "text")
    session = InterviewSession(brain=brain, profile=profile)

    # Build config from args or job context
    job_title = getattr(args, "role", None) or "Software Engineer"
    company = getattr(args, "company", None) or "Tech Company"
    interview_type = getattr(args, "type", None) or "mixed"
    difficulty = getattr(args, "difficulty", None) or "mid"
    duration = getattr(args, "duration", None) or 30
    job_description = ""
    resume_text = ""
    job_id = getattr(args, "job_id", None) or ""

    # Pull context from tracked job if job_id provided
    if job_id:
        from utils.tracker import get_job_by_id
        job = get_job_by_id(job_id)
        if job:
            job_title = job.get("title", job_title)
            company = job.get("company", company)
            job_description = job.get("description", "")
            print(f"  Loaded job context: {job_title} @ {company}")
        else:
            print(f"  Warning: Job ID '{job_id}' not found in tracker")

    # Load resume
    try:
        from utils.resume_parser import extract_resume_text
        resume_text = extract_resume_text(profile.get("resume_path", ""))
    except Exception:
        pass

    # Configure session
    session.configure(
        job_title=job_title,
        company=company,
        interview_type=interview_type,
        difficulty=difficulty,
        duration_minutes=duration,
        job_description=job_description,
        resume_text=resume_text,
        video_enabled=(mode == "video"),
        job_id=job_id,
    )

    config = {
        "job_title": job_title,
        "company": company,
        "interview_type": interview_type,
        "difficulty": difficulty,
        "duration": duration,
    }

    _print_banner(mode, config)

    # Run interview in the selected mode
    if mode == "voice":
        evaluation = await _run_voice_interview(session)
    elif mode == "video":
        evaluation = await _run_video_interview(session)
    else:
        evaluation = await _run_text_interview(session)

    # Save session
    output_path = getattr(args, "output", None)
    saved_path = session.save(output_path)
    print(f"\n  Session saved: {saved_path}")

    # Save to tracker DB
    try:
        from utils.tracker import save_interview_session
        save_interview_session(session.get_session_data())
        print("  Session stored in database.")
    except Exception as e:
        logger.debug(f"Could not save to tracker: {e}")

    return evaluation
