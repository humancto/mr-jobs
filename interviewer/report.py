"""
Interview Report — Formats evaluation results for CLI and dashboard display.
"""

import json
from typing import Optional


def format_text_report(session_data: dict) -> str:
    """
    Format a full interview session into a readable text report.

    Args:
        session_data: Output from InterviewSession.get_session_data()

    Returns:
        Formatted text report string
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  INTERVIEW REPORT")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Role:       {session_data.get('job_title', 'N/A')}")
    lines.append(f"  Company:    {session_data.get('company', 'N/A')}")
    lines.append(f"  Type:       {session_data.get('interview_type', 'N/A')}")
    lines.append(f"  Difficulty: {session_data.get('difficulty', 'N/A')}")
    lines.append(f"  Duration:   {session_data.get('duration_minutes', 0):.1f} min")
    lines.append(f"  Questions:  {session_data.get('questions_asked', 0)}")
    lines.append(f"  Video:      {'Yes' if session_data.get('video_enabled') else 'No'}")
    lines.append(f"  Date:       {session_data.get('started_at', 'N/A')}")
    lines.append("")

    # Transcript
    transcript = session_data.get("transcript", [])
    if transcript:
        lines.append("-" * 60)
        lines.append("  TRANSCRIPT")
        lines.append("-" * 60)
        for entry in transcript:
            role = "Interviewer" if entry["role"] == "interviewer" else "You"
            lines.append(f"\n  {role}:")
            lines.append(f"  {entry['text']}")
        lines.append("")

    # Evaluation
    evaluation = session_data.get("evaluation")
    if evaluation and "error" not in evaluation:
        lines.append("=" * 60)
        lines.append("  EVALUATION")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"  Overall Score:    {evaluation.get('overall_score', 'N/A')}/5")
        lines.append(f"  Recommendation:   {evaluation.get('recommendation', 'N/A')}")

        # Dimensions
        dims = evaluation.get("dimensions", {})
        if dims:
            lines.append("")
            lines.append("  SCORES BY DIMENSION:")
            for key, dim in dims.items():
                name = key.replace("_", " ").title()
                score = dim.get("score", "?")
                lines.append(f"    {name:<20} {score}/5")
                if dim.get("evidence"):
                    lines.append(f"      Evidence: {dim['evidence']}")
                if dim.get("feedback"):
                    lines.append(f"      Advice:   {dim['feedback']}")

        # Strengths
        strengths = evaluation.get("strengths", [])
        if strengths:
            lines.append("")
            lines.append("  STRENGTHS:")
            for s in strengths:
                lines.append(f"    + {s}")

        # Areas for improvement
        areas = evaluation.get("improvements", evaluation.get("areas_for_improvement", []))
        if areas:
            lines.append("")
            lines.append("  AREAS TO IMPROVE:")
            for a in areas:
                lines.append(f"    - {a}")

        # Detailed feedback
        feedback = evaluation.get("detailed_feedback", "")
        if feedback:
            lines.append("")
            lines.append("  DETAILED FEEDBACK:")
            lines.append(f"  {feedback}")

        # Practice suggestions
        practice = evaluation.get("practice_suggestions", evaluation.get("suggested_practice", []))
        if practice:
            lines.append("")
            lines.append("  SUGGESTED PRACTICE:")
            for p in practice:
                lines.append(f"    * {p}")

    # Engagement data
    engagement = session_data.get("engagement_scores", [])
    if engagement:
        avg = sum(e["score"] for e in engagement) / len(engagement)
        lines.append("")
        lines.append(f"  VIDEO ENGAGEMENT: {avg:.1f}/5 avg ({len(engagement)} observations)")
        for e in engagement:
            if e.get("notes"):
                lines.append(f"    - {e['notes']}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def format_evaluation_summary(evaluation: dict) -> dict:
    """
    Format evaluation into a dashboard-friendly summary.

    Args:
        evaluation: Raw evaluation dict from InterviewSession.evaluate()

    Returns:
        Simplified dict for dashboard display
    """
    if not evaluation or "error" in evaluation:
        return {
            "has_evaluation": False,
            "error": evaluation.get("error", "No evaluation available"),
        }

    dims = evaluation.get("dimensions", {})
    dim_scores = {}
    for key, dim in dims.items():
        dim_scores[key] = {
            "score": dim.get("score", 0),
            "feedback": dim.get("feedback", ""),
        }

    rec = evaluation.get("recommendation", "")
    rec_labels = {
        "strong_hire": "Strong Hire",
        "hire": "Hire",
        "lean_hire": "Lean Hire",
        "lean_no": "Lean No",
        "no_hire": "No Hire",
    }
    rec_colors = {
        "strong_hire": "#10b981",
        "hire": "#22d3ee",
        "lean_hire": "#f59e0b",
        "lean_no": "#f97316",
        "no_hire": "#f43f5e",
    }

    return {
        "has_evaluation": True,
        "overall_score": evaluation.get("overall_score", 0),
        "recommendation": rec,
        "recommendation_label": rec_labels.get(rec, rec),
        "recommendation_color": rec_colors.get(rec, "#94a3b8"),
        "dimensions": dim_scores,
        "strengths": evaluation.get("strengths", []),
        "areas_for_improvement": evaluation.get("areas_for_improvement",
                                                  evaluation.get("improvements", [])),
        "improvements": evaluation.get("improvements",
                                        evaluation.get("areas_for_improvement", [])),
        "detailed_feedback": evaluation.get("detailed_feedback", ""),
        "suggested_practice": evaluation.get("suggested_practice",
                                              evaluation.get("practice_suggestions", [])),
        "practice_suggestions": evaluation.get("practice_suggestions",
                                                evaluation.get("suggested_practice", [])),
        "readiness": evaluation.get("readiness", ""),
        "tone_analysis": evaluation.get("tone_analysis", {}),
        "engagement_summary": evaluation.get("engagement_summary", {}),
    }


def format_full_report(session_data: dict) -> dict:
    """
    Format a complete interview session into a comprehensive dashboard report.

    Includes radar chart data, timeline, comparison fields, and all evaluation
    details. This is the primary data structure consumed by the dashboard's
    post-interview evaluation card.

    Args:
        session_data: Full session dict (from get_session_data() or DB row)

    Returns:
        Dict suitable for dashboard rendering with chart data
    """
    evaluation = session_data.get("evaluation", {})
    summary = format_evaluation_summary(evaluation)

    # Radar chart data: labels + values for Chart.js
    dims = evaluation.get("dimensions", {})
    dim_order = [
        "communication", "technical_depth", "problem_solving",
        "leadership", "tone_and_delivery", "engagement",
    ]
    dim_labels = {
        "communication": "Communication",
        "technical_depth": "Technical Depth",
        "problem_solving": "Problem Solving",
        "leadership": "Leadership",
        "tone_and_delivery": "Tone & Delivery",
        "engagement": "Engagement",
    }

    radar_labels = []
    radar_scores = []
    for key in dim_order:
        if key in dims:
            radar_labels.append(dim_labels.get(key, key))
            radar_scores.append(dims[key].get("score", 0))

    # Engagement timeline data
    engagement_scores = session_data.get("engagement_scores", [])
    engagement_timeline = []
    started_at = session_data.get("started_at", "")
    for e in engagement_scores:
        ts = e.get("timestamp", 0)
        engagement_timeline.append({
            "timestamp": ts,
            "score": e.get("score", 0),
            "notes": e.get("notes", ""),
        })

    return {
        **summary,
        "session_id": session_data.get("session_id", ""),
        "job_title": session_data.get("job_title", ""),
        "company": session_data.get("company", ""),
        "interview_type": session_data.get("interview_type", ""),
        "difficulty": session_data.get("difficulty", ""),
        "duration_minutes": session_data.get("duration_minutes", 0),
        "questions_asked": session_data.get("questions_asked", 0),
        "started_at": started_at,
        "provider": session_data.get("provider", ""),
        "mode": session_data.get("mode", "text"),
        "has_recording": bool(session_data.get("recording_path")),
        "radar_chart": {
            "labels": radar_labels,
            "scores": radar_scores,
        },
        "engagement_timeline": engagement_timeline,
        "transcript": session_data.get("transcript", []),
    }
