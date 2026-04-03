"""
Interview rubrics — scoring dimensions and question banks.
"""

# Scoring dimensions used across all interview types
SCORING_DIMENSIONS = {
    "communication": {
        "name": "Communication",
        "description": "Clarity, structure, conciseness, active listening",
        "weight": 0.25,
    },
    "technical_depth": {
        "name": "Technical Depth",
        "description": "Accuracy, complexity of solutions, domain knowledge",
        "weight": 0.30,
    },
    "problem_solving": {
        "name": "Problem Solving",
        "description": "Approach, trade-off analysis, creative solutions",
        "weight": 0.25,
    },
    "leadership": {
        "name": "Leadership & Impact",
        "description": "Ownership, collaboration, growth mindset, impact",
        "weight": 0.20,
    },
}

INTERVIEW_TYPES = {
    "behavioral": {
        "name": "Behavioral",
        "description": "STAR method questions about past experiences",
        "duration_minutes": 30,
        "question_count": 5,
    },
    "technical": {
        "name": "Technical",
        "description": "System design, coding concepts, architecture",
        "duration_minutes": 45,
        "question_count": 4,
    },
    "system_design": {
        "name": "System Design",
        "description": "Design scalable systems and discuss trade-offs",
        "duration_minutes": 45,
        "question_count": 2,
    },
    "mixed": {
        "name": "Mixed",
        "description": "Combination of behavioral and technical questions",
        "duration_minutes": 30,
        "question_count": 4,
    },
}

# Question starters per type — Claude generates specifics based on job description
QUESTION_SEEDS = {
    "behavioral": [
        "Tell me about a time you had to deal with a significant technical challenge.",
        "Describe a situation where you had to influence a team without authority.",
        "Walk me through a project that didn't go as planned. What happened?",
        "Tell me about a time you had to learn something new quickly.",
        "Describe your most impactful technical contribution.",
        "Tell me about a disagreement with a colleague and how you resolved it.",
        "Walk me through how you prioritize when everything is urgent.",
        "Describe a time you went above and beyond.",
    ],
    "technical": [
        "How would you design a rate limiter?",
        "Explain the trade-offs between SQL and NoSQL databases.",
        "Walk me through how you'd debug a production performance issue.",
        "How does garbage collection work in your preferred language?",
        "Explain the CAP theorem and its practical implications.",
        "How would you design an API that handles millions of requests?",
        "What's the difference between processes and threads?",
        "How would you implement a distributed cache?",
    ],
    "system_design": [
        "Design a URL shortener like bit.ly.",
        "Design a real-time chat system like Slack.",
        "Design a news feed system like Twitter's timeline.",
        "Design a distributed file storage system.",
        "Design a video streaming platform.",
        "Design a ride-sharing service like Uber.",
    ],
}


def get_interview_config(interview_type: str) -> dict:
    """Get interview configuration for a given type."""
    config = INTERVIEW_TYPES.get(interview_type, INTERVIEW_TYPES["mixed"])
    return {
        **config,
        "scoring_dimensions": SCORING_DIMENSIONS,
        "question_seeds": QUESTION_SEEDS.get(interview_type, QUESTION_SEEDS.get("behavioral", [])),
    }
