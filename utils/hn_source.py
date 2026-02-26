"""
Hacker News 'Who is Hiring' thread scraper.
Parses the monthly hiring threads using the HN Algolia API.
These threads are posted on the 1st of each month by 'whoishiring' user.
"""

import hashlib
import re
import httpx
from datetime import datetime


def discover_hn_jobs(profile: dict) -> list:
    """Scrape the latest HN 'Who is Hiring' thread for matching jobs."""
    from utils.discovery import Job

    role_keywords = [kw.lower() for kw in profile["preferences"]["roles"]]
    skill_keywords = [kw.lower() for kw in profile["preferences"].get("keywords", [])]
    all_keywords = role_keywords + skill_keywords

    all_jobs = []

    try:
        # Find the latest "Who is Hiring" thread via Algolia search
        search_url = "https://hn.algolia.com/api/v1/search"
        params = {
            "query": "Ask HN: Who is hiring",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 1,
        }

        resp = httpx.get(search_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        hits = data.get("hits", [])
        if not hits:
            print("  \u26a0 No 'Who is Hiring' thread found")
            return []

        thread = hits[0]
        thread_id = thread.get("objectID", "")
        thread_title = thread.get("title", "")
        print(f"  \U0001f4f0 Found: {thread_title}")

        # Fetch all comments (top-level only = individual job posts)
        comments_url = f"https://hn.algolia.com/api/v1/items/{thread_id}"
        resp = httpx.get(comments_url, timeout=30)
        resp.raise_for_status()
        thread_data = resp.json()

        children = thread_data.get("children", [])
        print(f"  \U0001f4f0 {len(children)} postings in thread")

        for comment in children:
            text = comment.get("text", "")
            if not text:
                continue

            # Clean HTML
            clean_text = re.sub(r'<[^>]+>', ' ', text)
            clean_text = re.sub(r'&[a-zA-Z]+;', ' ', clean_text)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()

            text_lower = clean_text.lower()

            # Filter by keywords
            if not any(kw in text_lower for kw in all_keywords):
                continue

            # Extract company name (usually the first line / first bold text)
            company = "Unknown"
            # Try to get company from first line (common format: "Company Name | Role | Location | ...")
            first_line = clean_text.split('.')[0].split('|')[0].strip()
            if len(first_line) < 60:
                company = first_line

            # Try to extract title from pipe-separated format
            parts = clean_text.split('|')
            title = "See posting"
            location = "See posting"
            if len(parts) >= 2:
                company = parts[0].strip()[:60]
                # Look for role-like parts
                for part in parts[1:]:
                    part_stripped = part.strip()
                    part_lower = part_stripped.lower()
                    if any(kw in part_lower for kw in ["engineer", "developer", "designer",
                                                        "manager", "lead", "senior", "junior",
                                                        "architect", "devops", "sre", "data",
                                                        "ml", "ai", "frontend", "backend",
                                                        "fullstack", "full-stack", "full stack"]):
                        title = part_stripped[:100]
                    elif any(kw in part_lower for kw in ["remote", "onsite", "hybrid",
                                                          "sf", "nyc", "la", "seattle",
                                                          "austin", "denver", "chicago",
                                                          "london", "berlin", "toronto"]):
                        location = part_stripped[:100]

            comment_id = str(comment.get("id", ""))
            hn_url = f"https://news.ycombinator.com/item?id={comment_id}"

            job_id = hashlib.md5(hn_url.encode()).hexdigest()[:16]

            job = Job(
                id=f"hn_{job_id}",
                title=title,
                company=company,
                location=location,
                url=hn_url,
                apply_url=hn_url,
                platform="hackernews",
                description=clean_text[:5000],
                department="",
                metadata={
                    "source": "hackernews",
                    "thread_title": thread_title,
                    "date_posted": comment.get("created_at", ""),
                }
            )
            all_jobs.append(job)

        print(f"  \U0001f4ca HN Who is Hiring: {len(all_jobs)} matching jobs")

    except Exception as e:
        print(f"  \u26a0 HN Who is Hiring failed: {e}")

    return all_jobs
