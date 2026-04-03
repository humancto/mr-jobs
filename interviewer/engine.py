"""
Interview Engine — Core session state machine powered by ClaudeBrain.

This is the brain of the interviewer. It manages conversation flow,
question generation, follow-ups, scoring, and evaluation. Works in
all three modes (text, voice, video) — the mode only affects I/O,
not the interview logic.
"""

import json
import time
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

logger = logging.getLogger("interviewer.engine")


# ─── System Prompts ──────────────────────────────────────────────────────────

INTERVIEWER_SYSTEM_PROMPT = """You are a senior {interview_type} interviewer at a top tech company.

## Interview Configuration
- Role: {job_title}
- Company: {company}
- Type: {interview_type} interview
- Difficulty: {difficulty}
- Target Duration: {duration} minutes

## Candidate Context
{candidate_context}

## Behavior Rules
1. Start with a warm, brief introduction and explain the interview format.
2. Ask ONE question at a time. Wait for the candidate's full response.
3. Listen actively — reference specific things the candidate said.
4. Ask 1-2 follow-up questions to probe deeper when answers are surface-level.
5. Transition naturally between topics with brief acknowledgments.
6. Keep your responses concise: 2-3 sentences max for questions. No monologues.
7. NEVER reveal scoring or evaluation during the interview.
8. Near the end, ask if the candidate has any questions about the role.
9. End with a warm thank-you and what to expect next.

## Job-Specific Focus Areas
{focus_areas}

## Output Format
Respond ONLY with your spoken words as the interviewer. No scoring, no internal notes, no metadata, no brackets, no stage directions. Just speak naturally as a human interviewer would.

{engagement_instructions}"""

ENGAGEMENT_INSTRUCTIONS_VIDEO = """
## Video Analysis Notes
You will periodically receive engagement observations about the candidate's body language and demeanor. Use these to:
- Adjust your pace if the candidate seems nervous
- Note strong engagement when they're enthusiastic about a topic
- Don't mention the video analysis to the candidate"""

EVALUATION_PROMPT = """You are evaluating a {interview_type} interview for the role of {job_title} at {company}.

## Candidate Profile
{candidate_context}

## Full Interview Transcript
{transcript}

{engagement_summary}

## Evaluation Instructions
Provide a comprehensive, structured evaluation. Be specific — cite exact quotes from the transcript as evidence. Be honest but constructive.

Return ONLY valid JSON with this structure:
{{
    "overall_score": <1-5 float>,
    "recommendation": "<strong_hire|hire|lean_hire|lean_no|no_hire>",
    "dimensions": {{
        "communication": {{
            "score": <1-5 float>,
            "evidence": "<specific quote or observation>",
            "feedback": "<actionable improvement advice>"
        }},
        "technical_depth": {{
            "score": <1-5 float>,
            "evidence": "<specific quote or observation>",
            "feedback": "<actionable improvement advice>"
        }},
        "problem_solving": {{
            "score": <1-5 float>,
            "evidence": "<specific quote or observation>",
            "feedback": "<actionable improvement advice>"
        }},
        "leadership": {{
            "score": <1-5 float>,
            "evidence": "<specific quote or observation>",
            "feedback": "<actionable improvement advice>"
        }},
        "tone_and_delivery": {{
            "score": <1-5 float>,
            "evidence": "<observations about confidence, pacing, filler words, clarity>",
            "feedback": "<actionable improvement advice>"
        }},
        "engagement": {{
            "score": <1-5 float>,
            "evidence": "<observations about attentiveness, body language, eye contact>",
            "feedback": "<actionable improvement advice>"
        }}
    }},
    "strengths": ["<strength 1>", "<strength 2>", "<strength 3>", "<strength 4>", "<strength 5>"],
    "improvements": ["<specific actionable improvement 1>", "<improvement 2>", "<improvement 3>"],
    "practice_suggestions": ["<what to practice next 1>", "<practice 2>"],
    "detailed_feedback": "<2-3 paragraph summary with specific, actionable advice>",
    "readiness": "<overall readiness assessment — e.g. Ready for mid-level interviews>",
    "tone_analysis": {{
        "confidence_level": "<high|medium|low>",
        "pacing": "<appropriate|slightly fast|slightly slow|too fast|too slow>",
        "filler_words": "<none|minimal|moderate|excessive>",
        "vocal_clarity": "<excellent|good|fair|poor>"
    }},
    "engagement_summary": {{
        "avg_score": <1-5 float or null if no video>,
        "trend": "<improving|stable|declining|N/A>",
        "notable": "<most notable engagement observation or N/A>"
    }}
}}"""

QUESTION_GENERATION_PROMPT = """Generate {count} interview questions for a {interview_type} interview.

Role: {job_title}
Company: {company}
Difficulty: {difficulty}

Job Description:
{job_description}

Candidate Resume Summary:
{resume_summary}

Requirements:
- Questions should be specific to this role and company
- Order from warm-up to increasingly challenging
- Include at least one question about the candidate's specific experience
- For behavioral: use "Tell me about a time..." format
- For technical: include real-world scenarios, not textbook questions
- For system design: pick systems relevant to the company's domain

Return ONLY valid JSON array of strings. Each string is one question."""


class InterviewSession:
    """
    Manages a mock interview session from start to finish.

    Lifecycle:
        1. configure() — Set up job context, candidate profile, interview type
        2. start() — Generate opening message
        3. respond(text) — Process candidate answer, return interviewer response
        4. end() — Close interview gracefully
        5. evaluate() — Run post-interview evaluation
    """

    def __init__(self, brain, profile: dict = None):
        """
        Args:
            brain: ClaudeBrain instance for LLM calls
            profile: User's profile.yaml dict (for candidate context)
        """
        self.brain = brain
        self.profile = profile or {}

        # Session state
        self.session_id = f"interview_{int(time.time())}"
        self.started_at = None
        self.ended_at = None
        self.state = "idle"  # idle → active → ended → evaluated

        # Interview config
        self.job_title = ""
        self.company = ""
        self.interview_type = "mixed"
        self.difficulty = "mid"
        self.duration_minutes = 30
        self.job_description = ""
        self.resume_text = ""

        # Conversation
        self.transcript = []  # [{"role": "interviewer"|"candidate", "text": str, "timestamp": float}]
        self.questions_asked = 0
        self.max_questions = 5
        self.follow_up_count = 0

        # Video engagement data (optional)
        self.engagement_scores = []  # [{"timestamp": float, "score": int, "notes": str}]
        self.video_enabled = False

        # Evaluation result
        self.evaluation = None

    def configure(
        self,
        job_title: str = "Software Engineer",
        company: str = "Tech Company",
        interview_type: str = "mixed",
        difficulty: str = "mid",
        duration_minutes: int = 30,
        job_description: str = "",
        resume_text: str = "",
        video_enabled: bool = False,
        job_id: str = "",
    ):
        """Configure the interview session with job and candidate context."""
        self.job_title = job_title
        self.company = company
        self.interview_type = interview_type
        self.difficulty = difficulty
        self.duration_minutes = duration_minutes
        self.job_description = job_description
        self.resume_text = resume_text
        self.video_enabled = video_enabled
        self.job_id = job_id

        from interviewer.rubric import get_interview_config
        config = get_interview_config(interview_type)
        self.max_questions = config["question_count"]

    def _build_system_prompt(self) -> str:
        """Build the system prompt with all context."""
        # Candidate context from profile
        personal = self.profile.get("personal", {})
        skills = self.profile.get("skills", {})

        candidate_parts = []
        name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
        if name:
            candidate_parts.append(f"Name: {name}")
        if skills.get("primary"):
            candidate_parts.append(f"Key Skills: {', '.join(skills['primary'][:10])}")
        if self.resume_text:
            candidate_parts.append(f"Resume Summary:\n{self.resume_text[:2000]}")

        candidate_context = "\n".join(candidate_parts) if candidate_parts else "(No candidate context provided)"

        # Focus areas from job description
        focus_areas = ""
        if self.job_description:
            focus_areas = f"Key areas from the job description to probe:\n{self.job_description[:1500]}"

        engagement_instructions = ENGAGEMENT_INSTRUCTIONS_VIDEO if self.video_enabled else ""

        return INTERVIEWER_SYSTEM_PROMPT.format(
            interview_type=self.interview_type,
            job_title=self.job_title,
            company=self.company,
            difficulty=self.difficulty,
            duration=self.duration_minutes,
            candidate_context=candidate_context,
            focus_areas=focus_areas,
            engagement_instructions=engagement_instructions,
        )

    def _build_messages(self) -> list:
        """Build the message history for the LLM."""
        messages = []
        for entry in self.transcript:
            role = "assistant" if entry["role"] == "interviewer" else "user"
            content = entry["text"]

            # Inject engagement data for video mode
            if role == "user" and self.video_enabled and self.engagement_scores:
                recent = [e for e in self.engagement_scores if e["timestamp"] > entry.get("timestamp", 0) - 30]
                if recent:
                    latest = recent[-1]
                    content += f"\n\n[Engagement observation: {latest.get('notes', 'normal')}]"

            messages.append({"role": role, "content": content})
        return messages

    def start(self) -> str:
        """Start the interview. Returns the interviewer's opening message."""
        self.state = "active"
        self.started_at = time.time()

        system_prompt = self._build_system_prompt()

        # Ask the brain for the opening via multi-turn chat
        opening_prompt = (
            "Begin the interview now. Introduce yourself briefly, explain the format "
            f"({self.interview_type} interview, approximately {self.duration_minutes} minutes), "
            "and ask your first question."
        )

        fallback = (
            f"Hi there! Thanks for joining. I'm excited to learn more about your experience. "
            f"This will be a {self.interview_type} interview for the {self.job_title} role "
            f"at {self.company}. We have about {self.duration_minutes} minutes. "
            f"Let's start — tell me about yourself and what draws you to this role."
        )

        try:
            response = self.brain.ask_chat(
                messages=[{"role": "user", "content": opening_prompt}],
                system=system_prompt,
                timeout=120,
                component="interview",
            )
        except Exception:
            response = None

        if not response:
            response = fallback

        self.transcript.append({
            "role": "interviewer",
            "text": response,
            "timestamp": time.time(),
        })
        self.questions_asked = 1

        return response

    def respond(self, candidate_text: str) -> str:
        """
        Process candidate's response and generate the interviewer's next message.

        Args:
            candidate_text: What the candidate said

        Returns:
            Interviewer's response text
        """
        if self.state != "active":
            return "The interview session is not active."

        # Record candidate response
        self.transcript.append({
            "role": "candidate",
            "text": candidate_text,
            "timestamp": time.time(),
        })

        # Check if we should wrap up
        elapsed = (time.time() - self.started_at) / 60
        should_end = (
            self.questions_asked >= self.max_questions
            or elapsed >= self.duration_minutes
        )

        # Build full context for the brain
        system_prompt = self._build_system_prompt()
        messages = self._build_messages()

        if should_end:
            wrap_up = (
                "\n\n[INTERNAL: This is the final exchange. Thank the candidate warmly, "
                "ask if they have any questions about the role, then close the interview "
                "professionally. Do NOT ask another interview question.]"
            )
            system_prompt += wrap_up

        try:
            response = self.brain.ask_chat(
                messages=messages,
                system=system_prompt,
                timeout=120,
                component="interview",
            )
        except Exception:
            response = None

        if not response:
            if should_end:
                response = (
                    "That's a great note to end on. Thank you so much for your time today. "
                    "You've given me a lot to think about. Do you have any questions for me "
                    "about the role or the team?"
                )
            else:
                response = "That's interesting. Could you elaborate on that a bit more?"

        self.transcript.append({
            "role": "interviewer",
            "text": response,
            "timestamp": time.time(),
        })

        if not should_end:
            self.questions_asked += 1

        return response

    def add_engagement_score(self, score: int, notes: str = ""):
        """Add a video engagement observation (called by video pipeline)."""
        self.engagement_scores.append({
            "timestamp": time.time(),
            "score": score,
            "notes": notes,
        })

    def engagement_summary(self) -> dict:
        """Aggregate engagement scores into a summary dict."""
        if not self.engagement_scores:
            return {"avg_score": None, "trend": "N/A", "notable": "N/A"}

        scores = [e["score"] for e in self.engagement_scores]
        avg = sum(scores) / len(scores)

        # Determine trend by comparing first half to second half
        if len(scores) >= 4:
            mid = len(scores) // 2
            first_half = sum(scores[:mid]) / mid
            second_half = sum(scores[mid:]) / (len(scores) - mid)
            diff = second_half - first_half
            if diff > 0.3:
                trend = "improving"
            elif diff < -0.3:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # Find most notable observation
        notable = "N/A"
        best = max(self.engagement_scores, key=lambda e: e["score"])
        if best.get("notes"):
            notable = best["notes"]

        return {"avg_score": round(avg, 1), "trend": trend, "notable": notable}

    def end(self) -> str:
        """End the interview session. Returns closing message if needed."""
        if self.state != "active":
            return ""

        self.state = "ended"
        self.ended_at = time.time()

        # If the last message was from the candidate, generate a closing
        if self.transcript and self.transcript[-1]["role"] == "candidate":
            system_prompt = self._build_system_prompt()
            system_prompt += (
                "\n\n[INTERNAL: The interview time is up. Give a brief, warm closing. "
                "Thank them and explain next steps.]"
            )
            messages = self._build_messages()

            closing = self.brain.ask_chat(
                messages=messages,
                system=system_prompt,
                timeout=120,
                component="interview",
            )
            if closing:
                self.transcript.append({
                    "role": "interviewer",
                    "text": closing,
                    "timestamp": time.time(),
                })
                return closing

        return ""

    def evaluate(self) -> dict:
        """
        Run post-interview evaluation over the full transcript.
        Returns structured evaluation JSON.
        """
        if self.state not in ("ended", "active"):
            return {"error": "Interview not completed"}

        if self.state == "active":
            self.end()

        # Build transcript text
        transcript_text = ""
        for entry in self.transcript:
            role = "Interviewer" if entry["role"] == "interviewer" else "Candidate"
            transcript_text += f"{role}: {entry['text']}\n\n"

        # Engagement summary for video mode
        engagement_summary = ""
        if self.engagement_scores:
            avg_score = sum(e["score"] for e in self.engagement_scores) / len(self.engagement_scores)
            engagement_summary = (
                f"## Video Engagement Data\n"
                f"Average engagement score: {avg_score:.1f}/5\n"
                f"Total observations: {len(self.engagement_scores)}\n"
            )
            for e in self.engagement_scores:
                if e.get("notes"):
                    engagement_summary += f"- {e['notes']}\n"

        # Candidate context
        personal = self.profile.get("personal", {})
        skills = self.profile.get("skills", {})
        candidate_parts = []
        name = f"{personal.get('first_name', '')} {personal.get('last_name', '')}".strip()
        if name:
            candidate_parts.append(f"Name: {name}")
        if skills.get("primary"):
            candidate_parts.append(f"Skills: {', '.join(skills['primary'][:10])}")
        if self.resume_text:
            candidate_parts.append(f"Resume:\n{self.resume_text[:1500]}")
        candidate_context = "\n".join(candidate_parts) or "(No context)"

        prompt = EVALUATION_PROMPT.format(
            interview_type=self.interview_type,
            job_title=self.job_title,
            company=self.company,
            candidate_context=candidate_context,
            transcript=transcript_text,
            engagement_summary=engagement_summary,
        )

        result = self.brain.ask_json(prompt, timeout=180)

        if result:
            self.evaluation = result
            self.state = "evaluated"
        else:
            self.evaluation = {"error": "Evaluation failed", "raw_transcript": transcript_text}

        return self.evaluation

    def get_session_data(self) -> dict:
        """Get full session data for storage/export."""
        elapsed = 0
        if self.started_at:
            end = self.ended_at or time.time()
            elapsed = (end - self.started_at) / 60

        return {
            "session_id": self.session_id,
            "job_id": getattr(self, "job_id", ""),
            "job_title": self.job_title,
            "company": self.company,
            "interview_type": self.interview_type,
            "difficulty": self.difficulty,
            "duration_minutes": round(elapsed, 1),
            "target_duration": self.duration_minutes,
            "questions_asked": self.questions_asked,
            "state": self.state,
            "started_at": datetime.fromtimestamp(self.started_at, tz=timezone.utc).isoformat() if self.started_at else None,
            "ended_at": datetime.fromtimestamp(self.ended_at, tz=timezone.utc).isoformat() if self.ended_at else None,
            "transcript": self.transcript,
            "engagement_scores": self.engagement_scores,
            "video_enabled": self.video_enabled,
            "evaluation": self.evaluation,
            "recording_path": getattr(self, "recording_path", ""),
            "provider": getattr(self, "provider", ""),
            "mode": getattr(self, "mode", "text"),
        }

    def save(self, output_path: str = None) -> str:
        """Save session data to a JSON file. Returns the file path."""
        if not output_path:
            output_dir = Path(".cache/interviews")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"{self.session_id}.json")

        data = self.get_session_data()
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info(f"Interview session saved to {output_path}")
        return output_path
