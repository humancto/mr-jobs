"""
AI Resume Tailoring — Generates tailored resume content per job posting.

For high-match jobs, produces optimized summaries, bullet points, and keyword
emphasis that align the candidate's actual experience with specific job requirements.
"""

import json


def tailor_resume(job_description: str, base_resume_text: str, profile: dict, brain=None) -> dict:
    """
    Generate tailored resume content for a specific job posting.

    Args:
        job_description: The full job posting text
        base_resume_text: The candidate's base resume text
        profile: The user's profile.yaml data
        brain: Optional ClaudeBrain instance (creates one if not provided)

    Returns:
        dict with keys: tailored_summary, tailored_bullets, emphasis_areas,
                       keywords_to_include, deemphasize, tailored_cover_letter
    """
    if not base_resume_text:
        return {
            "tailored_summary": "",
            "tailored_bullets": [],
            "emphasis_areas": [],
            "keywords_to_include": [],
            "deemphasize": [],
            "tailored_cover_letter": "",
            "error": "No resume text available",
        }

    if brain is None:
        from utils.brain import ClaudeBrain
        brain = ClaudeBrain(verbose=False)

    skills = profile.get("skills", {})
    roles = profile.get("preferences", {}).get("roles", [])

    prompt = f"""You are an expert resume consultant. Analyze this candidate's resume against a specific job posting and produce tailored content.

CANDIDATE'S BASE RESUME:
{base_resume_text[:5000]}

CANDIDATE'S TARGET ROLES: {', '.join(roles)}
CANDIDATE'S KEY SKILLS: {', '.join(skills.get('primary', []))}

JOB POSTING:
{job_description[:6000]}

Produce a JSON object with these fields:
{{
  "tailored_summary": "<A 2-3 sentence professional summary optimized for THIS specific job, highlighting the most relevant experience and skills from the resume>",
  "tailored_bullets": [
    "<Achievement bullet rewritten to emphasize relevance to this job>",
    "<Another tailored bullet point>",
    "<Up to 6 total>"
  ],
  "emphasis_areas": ["<Skills/experience from resume to emphasize for this role>"],
  "keywords_to_include": ["<Important keywords from the job posting that match the candidate's experience>"],
  "deemphasize": ["<Areas of the resume less relevant to this specific role>"],
  "tailored_cover_letter": "<3 paragraph cover letter specifically for this job, referencing both the job requirements and the candidate's matching experience>"
}}

Rules:
- Only reference experience that ACTUALLY EXISTS in the resume
- Quantify achievements where the resume provides numbers
- Mirror the job posting's language and terminology
- Be specific, not generic — every bullet should connect resume experience to job requirements
- The cover letter should feel personal and specific, not templated
"""

    try:
        result = brain.ask_json(prompt, timeout=180)
        # Ensure all expected keys exist
        defaults = {
            "tailored_summary": "",
            "tailored_bullets": [],
            "emphasis_areas": [],
            "keywords_to_include": [],
            "deemphasize": [],
            "tailored_cover_letter": "",
        }
        for key, default in defaults.items():
            if key not in result:
                result[key] = default
        return result
    except Exception as e:
        return {
            "tailored_summary": "",
            "tailored_bullets": [],
            "emphasis_areas": [],
            "keywords_to_include": [],
            "deemphasize": [],
            "tailored_cover_letter": "",
            "error": str(e),
        }
