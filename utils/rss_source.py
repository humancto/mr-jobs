"""
RSS feed job sources — RemoteOK, HN Who's Hiring.
"""

import re
import hashlib
from typing import List


def discover_rss_jobs(profile: dict) -> list:
    """Discover jobs from RSS feeds."""
    from utils.discovery import Job

    all_jobs = []
    role_keywords = [kw.lower() for kw in profile["preferences"]["roles"]]
    skill_keywords = [kw.lower() for kw in profile["preferences"].get("keywords", [])]
    all_keywords = role_keywords + skill_keywords

    # RemoteOK
    try:
        remoteok_jobs = _fetch_remoteok(all_keywords)
        all_jobs.extend(remoteok_jobs)
        print(f"  📡 RemoteOK: {len(remoteok_jobs)} matching jobs")
    except Exception as e:
        print(f"  ⚠ RemoteOK failed: {e}")

    return all_jobs


def _fetch_remoteok(keywords: list) -> list:
    """Fetch and filter jobs from RemoteOK RSS feed."""
    from utils.discovery import Job

    try:
        import feedparser
    except ImportError:
        print("  ⚠ feedparser not installed. Run: pip install feedparser")
        return []

    import httpx

    jobs = []
    # RemoteOK has a JSON API that's more reliable than RSS
    try:
        resp = httpx.get(
            "https://remoteok.com/api",
            headers={"User-Agent": "AutoApply/1.0"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        # Fallback to RSS
        feed = feedparser.parse("https://remoteok.com/remote-jobs.rss")
        data = []
        for entry in feed.entries:
            data.append({
                "position": entry.get("title", ""),
                "company": entry.get("author", "Unknown"),
                "description": entry.get("summary", ""),
                "url": entry.get("link", ""),
                "location": "Remote",
                "tags": [],
            })

    for item in data:
        if isinstance(item, dict) and item.get("position"):
            title = item.get("position", "")
            description = item.get("description", "")
            combined = f"{title} {description}".lower()

            # Filter by keywords
            if not any(kw in combined for kw in keywords):
                continue

            url = item.get("url", "")
            if not url.startswith("http"):
                url = f"https://remoteok.com{url}" if url else ""

            job_id = hashlib.md5(
                (url or f"{title}_{item.get('company', '')}").encode()
            ).hexdigest()[:16]

            tags = item.get("tags", [])
            if isinstance(tags, list):
                tags = ", ".join(tags)

            job = Job(
                id=f"remoteok_{job_id}",
                title=title,
                company=item.get("company", "Unknown"),
                location=item.get("location", "Remote"),
                url=url,
                apply_url=url,
                platform="remoteok",
                description=(description or "")[:5000],
                department="",
                metadata={
                    "source": "remoteok",
                    "tags": tags,
                    "date_posted": item.get("date", ""),
                    "salary_min": None,
                    "salary_max": None,
                }
            )
            jobs.append(job)

    return jobs
