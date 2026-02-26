"""
Claude Code CLI Brain — Uses `claude` CLI as the reasoning engine.

Instead of paying for API calls, this shells out to the Claude Code CLI
which is included in your Pro/Max subscription.

Supports pluggable LLM backends via profile.yaml `ai` section.
When a profile with `ai` config is provided, requests are routed through
the configured backend per component. Without a profile, falls back to
direct Claude CLI calls (original behavior).
"""

import os
import subprocess
import json
import re
import hashlib
from pathlib import Path
from typing import Optional

CACHE_DIR = Path(__file__).parent.parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


class ClaudeBrain:
    """Interface to AI reasoning — routes through pluggable LLM backends."""

    def __init__(self, verbose: bool = True, profile: dict = None):
        self.verbose = verbose
        self.profile = profile
        self._verify_cli()

    def _verify_cli(self):
        """Check that claude CLI is installed and accessible."""
        try:
            result = subprocess.run(
                ["claude", "--version"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "Claude CLI not responding. Install with: "
                    "npm install -g @anthropic-ai/claude-code"
                )
            if self.verbose:
                version = result.stdout.strip()
                print(f"  🧠 Claude CLI ready: {version}")
        except FileNotFoundError:
            raise RuntimeError(
                "Claude CLI not found. Install with:\n"
                "  npm install -g @anthropic-ai/claude-code\n"
                "Then run: claude auth"
            )

    def ask(self, prompt: str, timeout: int = 120, component: str = "general") -> str:
        """
        Send a prompt to the configured LLM backend.

        When a profile with `ai` config is available, routes through the
        pluggable backend for the given component. Otherwise falls back to
        direct Claude CLI calls (original behavior).
        """
        if self.verbose:
            preview = prompt[:80].replace('\n', ' ')
            print(f"  AI: {preview}...")

        # Route through pluggable backend if profile is available
        if self.profile:
            from utils.llm import get_backend
            backend = get_backend(component, self.profile)
            return backend.ask(prompt, timeout=timeout)

        # Fallback: direct Claude CLI (original behavior)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            ["claude", "-p", "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or "Unknown error"
            raise RuntimeError(f"Claude CLI error: {error_msg}")

        # Parse the JSON output
        try:
            data = json.loads(result.stdout)
            # Claude Code JSON output has a "result" field
            return data.get("result", result.stdout)
        except json.JSONDecodeError:
            # Fallback: return raw stdout
            return result.stdout.strip()

    def ask_json(self, prompt: str, timeout: int = 120, component: str = "general") -> dict:
        """Ask the LLM and parse JSON from the response."""
        # Route through pluggable backend if profile is available
        if self.profile:
            from utils.llm import get_backend
            backend = get_backend(component, self.profile)
            return backend.ask_json(prompt, timeout=timeout)

        # Fallback: original behavior
        full_prompt = prompt + (
            "\n\nIMPORTANT: Respond ONLY with valid JSON. "
            "No markdown fencing, no explanation, no preamble. Just the JSON object."
        )
        raw = self.ask(full_prompt, timeout=timeout)

        # Strip markdown code fences if present
        cleaned = raw.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            if self.verbose:
                print(f"  Warning: JSON parse failed, raw response:\n{raw[:500]}")
            raise ValueError(f"LLM didn't return valid JSON: {e}")

    def ask_cached(self, prompt: str, cache_key: Optional[str] = None) -> str:
        """Ask with disk caching — useful for repeated identical questions."""
        if cache_key is None:
            cache_key = hashlib.md5(prompt.encode()).hexdigest()

        cache_file = CACHE_DIR / f"{cache_key}.txt"
        if cache_file.exists():
            if self.verbose:
                print(f"  💾 Cache hit: {cache_key}")
            return cache_file.read_text()

        result = self.ask(prompt)
        cache_file.write_text(result)
        return result

    def match_job(self, job_description: str, profile: dict, resume_text: str = "") -> dict:
        """Score a job posting against the user's profile with enhanced context."""
        # Build enhanced profile context
        skills = profile.get("skills", {})
        primary_skills = ", ".join(skills.get("primary", profile["preferences"].get("keywords", [])))
        secondary_skills = ", ".join(skills.get("secondary", []))
        ideal_job = profile.get("ideal_job_description", "")
        favorites = profile.get("favorite_companies", [])

        prompt = f"""You are a job matching engine. Be critical and honest.

APPLICANT PROFILE:
- Roles sought: {', '.join(profile['preferences']['roles'])}
- Primary skills: {primary_skills}
- Secondary skills: {secondary_skills}
- Location preference: {', '.join(profile['preferences']['locations'])}
- Remote only: {profile['preferences']['remote_only']}
- Ideal job: {ideal_job[:500]}
- Favorite companies (bonus +10 if match): {', '.join(favorites)}

APPLICANT RESUME:
{resume_text[:4000] if resume_text else '(No resume provided)'}

JOB POSTING:
{job_description[:8000]}

Return this exact JSON structure:
{{
  "score": <integer 0-100>,
  "apply": <true or false>,
  "reasoning": "<one sentence explaining the score>",
  "title_match": "<how well the title matches>",
  "skill_overlap": ["<matching skills>"],
  "missing_skills": ["<skills they want that applicant lacks>"],
  "red_flags": ["<any concerns>"],
  "cover_letter": "<2-3 paragraph tailored cover letter>",
  "improve_match": "<one suggestion to improve match for this job>"
}}

Scoring guidelines:
- 90-100: Perfect match (role, skills, location all align)
- 70-89: Strong match (most criteria met)
- 50-69: Partial match (some gaps but worth considering)
- Below 50: Poor match
- If the company is in the favorites list, add 10 bonus points (max 100)
- Set "apply" to true only if score >= {profile['preferences'].get('min_match_score', 65)}
"""
        return self.ask_json(prompt, component="scoring")

    def score_profile(self, profile: dict, resume_text: str = "") -> dict:
        """Analyze the user's profile and resume for job market readiness."""
        skills = profile.get("skills", {})
        primary = ", ".join(skills.get("primary", []))
        secondary = ", ".join(skills.get("secondary", []))
        roles = ", ".join(profile["preferences"]["roles"])
        ideal = profile.get("ideal_job_description", "")

        prompt = f"""Analyze this job seeker's profile and resume for job market readiness.

PROFILE:
- Target roles: {roles}
- Primary skills: {primary}
- Secondary skills: {secondary}
- Ideal job: {ideal[:500]}
- Location: {profile['personal'].get('location', 'Not specified')}

RESUME TEXT:
{resume_text[:6000] if resume_text else '(No resume provided)'}

Return a JSON assessment:
{{
  "profile_score": <0-100>,
  "strengths": ["<top 3 strengths>"],
  "gaps": ["<skills or experience gaps>"],
  "resume_suggestions": ["<specific resume improvements>"],
  "keyword_recommendations": ["<keywords to add to profile>"],
  "role_fit": {{
    "<role name>": <0-100 fit score>
  }},
  "summary": "<2-3 sentence overall assessment>"
}}
"""
        return self.ask_json(prompt, timeout=180, component="profile_analysis")

    def answer_question(self, question: str, profile: dict, context: str = "") -> str:
        """Answer a custom application question using AI."""
        return self.ask(f"""You are filling out a job application for someone.
Answer this question concisely and professionally (1-3 sentences max).

Applicant info:
- Name: {profile['personal']['first_name']} {profile['personal']['last_name']}
- Location: {profile['personal']['location']}
- Looking for: {', '.join(profile['preferences']['roles'])}

Additional context: {context}

Question: {question}

Answer (be concise, direct, professional):""", component="form_analysis")

    def analyze_form(self, form_html: str, profile: dict) -> list:
        """Analyze a form's HTML and return fill instructions."""
        return self.ask_json(f"""You are a form-filling automation assistant.

APPLICANT PROFILE:
{json.dumps(profile['personal'], indent=2)}

COMMON ANSWERS:
{json.dumps(profile.get('common_answers', {}), indent=2)}

FORM HTML (may be truncated):
{form_html[:12000]}

Analyze every visible input field. Return a JSON array of actions:
[
  {{"action": "fill", "selector": "<CSS selector>", "value": "<text to type>", "field_name": "<what field this is>"}},
  {{"action": "select", "selector": "<CSS selector>", "value": "<option value>", "field_name": "<what field>"}},
  {{"action": "check", "selector": "<CSS selector>", "field_name": "<what checkbox>"}},
  {{"action": "upload", "selector": "<CSS selector>", "file_key": "resume", "field_name": "resume upload"}},
  {{"action": "click", "selector": "<CSS selector>", "field_name": "submit button"}}
]

Rules:
- Use the most specific CSS selector possible (prefer #id, then [name=...], then [aria-label=...])
- For file uploads, always set file_key to "resume"
- Put the submit/next button click LAST
- Skip hidden fields and CSRF tokens
- For dropdowns, use the actual option value attribute
""", component="form_analysis")
