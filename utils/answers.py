"""
Cached answer matcher — avoids calling Claude CLI for common application questions.
Pattern-matches question text against known Q&A pairs.
"""

import re
from typing import Optional

# Map of keyword patterns → profile.yaml common_answers keys
QUESTION_PATTERNS = {
    # Work authorization
    r"(authorized|legally|eligible).*(work|employ)": "authorized_to_work",
    r"(require|need|sponsor).*visa": "require_sponsorship",
    r"(sponsorship|visa\s*sponsor)": "require_sponsorship",
    r"work\s*(authorization|permit|eligibility)": "authorized_to_work",

    # Experience
    r"(years?|yrs?).*(experience|professional)": "years_experience",
    r"(how long|how many).*(work|experience|industry)": "years_experience",

    # Relocation
    r"(reloc|move|willing to relocate)": "willing_to_relocate",

    # Salary
    r"(salary|compensation|pay|wage).*expect": "salary_expectation",
    r"(desired|expected).*(salary|compensation|pay)": "salary_expectation",

    # Start date
    r"(start|begin|available).*(date|when|earliest)": "earliest_start_date",
    r"(when|how soon).*(start|begin|available|join)": "earliest_start_date",

    # How did you hear
    r"(how did you|where did you|how.*(hear|find|learn))": "how_did_you_hear",
    r"(source|referral|hear about)": "how_did_you_hear",

    # EEO / Demographics (usually optional — answer with "prefer not to say")
    r"(gender|sex)\b": "gender",
    r"(race|ethnic|ethnicity)": "race_ethnicity",
    r"(veteran|military|armed forces)": "veteran_status",
    r"(disabilit|handicap)": "disability_status",
}


def find_cached_answer(question: str, common_answers: dict) -> Optional[str]:
    """
    Try to match a question against known patterns.
    Returns the answer string if found, None if Claude should handle it.
    """
    question_lower = question.lower().strip()

    for pattern, answer_key in QUESTION_PATTERNS.items():
        if re.search(pattern, question_lower):
            answer = common_answers.get(answer_key)
            if answer:
                return answer

    return None


def get_personal_field(field_name: str, personal: dict) -> Optional[str]:
    """
    Try to match a form field label to a personal info field.
    Returns the value if matched, None otherwise.
    """
    field_lower = field_name.lower().strip()

    mappings = {
        r"(first\s*name|given\s*name|fname)": "first_name",
        r"(last\s*name|surname|family\s*name|lname)": "last_name",
        r"(full\s*name|your\s*name|name)": lambda p: f"{p['first_name']} {p['last_name']}",
        r"(email|e-mail)": "email",
        r"(phone|mobile|cell|telephone)": "phone",
        r"(city|location|address)": "location",
        r"(linkedin|linked\s*in)": "linkedin",
        r"(github|git\s*hub)": "github",
        r"(portfolio|website|personal\s*site|url)": "portfolio",
    }

    for pattern, key_or_fn in mappings.items():
        if re.search(pattern, field_lower):
            if callable(key_or_fn):
                return key_or_fn(personal)
            return personal.get(key_or_fn)

    return None
