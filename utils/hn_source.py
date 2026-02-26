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
    # Discovery uses ONLY role titles + generic stems — cast widest net
    # Skills/keywords are used by AI scoring only, NOT for discovery filtering
    all_keywords = list(set(
        role_keywords +
        ["engineer", "developer", "architect", "sre", "devops", "sde", "staff", "lead",
         "software", "backend", "frontend", "fullstack", "platform", "infrastructure",
         "data", "ml", "ai", "cloud", "security", "systems"]
    ))

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

            # Clean HTML and decode entities
            import html as html_mod
            clean_text = re.sub(r'<[^>]+>', ' ', text)
            clean_text = html_mod.unescape(clean_text)  # &#x2F; -> /, &amp; -> &, etc.
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()

            text_lower = clean_text.lower()

            # Filter by keywords
            if not any(kw in text_lower for kw in all_keywords):
                continue

            # Extract company, title, location from pipe-separated format
            # Common HN format: "Company Name | Role | Location | Remote | ..."
            parts = [p.strip() for p in clean_text.split('|')]
            company = parts[0][:80] if parts else "Unknown"
            title = "See posting"
            location = "See posting"

            if len(parts) >= 2:
                # Find the role-like part
                role_keywords_match = ["engineer", "developer", "designer",
                                       "manager", "lead", "senior", "junior",
                                       "architect", "devops", "sre", "data",
                                       "ml", "ai", "frontend", "backend",
                                       "fullstack", "full-stack", "full stack",
                                       "platform", "infrastructure", "security"]
                location_keywords = ["remote", "onsite", "hybrid", "on-site",
                                     "sf", "nyc", "la", "seattle", "austin",
                                     "denver", "chicago", "london", "berlin",
                                     "toronto", "new york", "san francisco",
                                     "los angeles", "boston", "usa", "eu"]

                for part in parts[1:]:
                    part_lower = part.lower()
                    # Skip parts that are URLs or very long
                    if part.startswith("http") or len(part) > 120:
                        continue
                    if any(kw in part_lower for kw in role_keywords_match):
                        title = part[:120]
                    elif any(kw in part_lower for kw in location_keywords):
                        location = part[:100]
                    elif title == "See posting" and len(part) < 80:
                        # If we haven't found a title yet, use the second part
                        title = part[:120]

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
