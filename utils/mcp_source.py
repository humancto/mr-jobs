"""
MCP-powered job discovery — Uses Claude Code's MCP tools as additional search channels.

This module is designed to be called by Claude Code sessions (not standalone Python).
It produces structured job data that the system can ingest into the tracker.

When running inside Claude Code, the agent can call these functions and then
use WebSearch, Playwright browser, or GitHub MCP tools to discover jobs.

For standalone Python (e.g., scheduler), these are no-ops — the other sources
(JobSpy, RSS, Greenhouse/Lever APIs) handle discovery instead.

Usage from Claude Code session:
    1. Agent calls WebSearch for job listings
    2. Agent parses results into Job objects via parse_web_search_results()
    3. Agent calls ingest_jobs() to save them to the tracker
"""

import hashlib
import json
from pathlib import Path
from typing import Optional


def parse_web_search_results(search_results: list[dict], platform_hint: str = "web") -> list:
    """
    Parse WebSearch MCP results into Job-compatible dicts.

    Args:
        search_results: List of {"title": "...", "url": "..."} from WebSearch
        platform_hint: Source label (e.g., "web_greenhouse", "web_lever", "web_indeed")

    Returns:
        List of job dicts ready for ingest_jobs()
    """
    jobs = []
    for result in search_results:
        title = result.get("title", "")
        url = result.get("url", "")
        if not title or not url:
            continue

        # Try to extract company from URL or title
        company = _extract_company_from_url(url)
        if not company:
            company = _extract_company_from_title(title)

        # Clean up title (remove "Job Application for ... at Company" patterns)
        clean_title = title
        if " at " in title:
            parts = title.split(" at ")
            clean_title = parts[0].replace("Job Application for ", "").strip()
            if not company:
                company = parts[-1].strip()

        if " - " in clean_title and not company:
            parts = clean_title.split(" - ")
            clean_title = parts[0].strip()

        job_id = hashlib.md5(url.encode()).hexdigest()[:16]

        jobs.append({
            "id": f"mcp_{job_id}",
            "title": clean_title,
            "company": company or "Unknown",
            "location": "See posting",
            "url": url,
            "apply_url": url,
            "platform": f"mcp_{platform_hint}",
            "description": "",
            "source": f"mcp_{platform_hint}",
        })

    return jobs


def parse_playwright_job_listings(snapshot_text: str, page_url: str) -> list:
    """
    Parse a Playwright browser snapshot into job listings.

    This is meant to be called by Claude Code after taking a browser_snapshot
    of a careers page. The agent should extract structured job data from the
    accessibility tree text.

    Args:
        snapshot_text: Text from mcp__playwright__browser_snapshot
        page_url: The URL that was scraped

    Returns:
        List of job dicts ready for ingest_jobs()
    """
    # This function provides the structure — the actual parsing is done by
    # the Claude agent interpreting the snapshot. The agent should call this
    # with pre-parsed data.
    return []


def ingest_jobs(job_dicts: list) -> dict:
    """
    Ingest parsed job dicts into the tracker database.

    Args:
        job_dicts: List of dicts with keys: id, title, company, location, url,
                   apply_url, platform, description, source

    Returns:
        {"ingested": N, "skipped": N, "total": N}
    """
    from utils.tracker import is_already_seen, get_db
    import json as _json

    ingested = 0
    skipped = 0

    conn = get_db()
    for job in job_dicts:
        job_id = job.get("id", "")
        if not job_id:
            continue

        if is_already_seen(job_id):
            skipped += 1
            continue

        try:
            conn.execute("""
                INSERT OR IGNORE INTO applications
                (id, title, company, platform, url, apply_url, location, description, source, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id,
                job.get("title", ""),
                job.get("company", ""),
                job.get("platform", "mcp"),
                job.get("url", ""),
                job.get("apply_url", ""),
                job.get("location", ""),
                job.get("description", ""),
                job.get("source", "mcp"),
                _json.dumps({"source": "mcp_discovery"}),
            ))
            ingested += 1
        except Exception:
            pass

    conn.commit()
    conn.close()

    return {"ingested": ingested, "skipped": skipped, "total": len(job_dicts)}


def search_greenhouse_web(query: str = "Software Engineer", max_results: int = 20) -> str:
    """
    Returns a WebSearch query string optimized for Greenhouse job boards.
    The Claude agent should pass this to WebSearch MCP tool.
    """
    return f'{query} site:greenhouse.io OR site:job-boards.greenhouse.io'


def search_lever_web(query: str = "Software Engineer", max_results: int = 20) -> str:
    """
    Returns a WebSearch query string optimized for Lever job boards.
    """
    return f'{query} site:jobs.lever.co remote 2026'


def search_indeed_web(query: str = "Software Engineer", location: str = "Remote") -> str:
    """WebSearch query for Indeed."""
    return f'{query} {location} site:indeed.com'


def search_linkedin_web(query: str = "Software Engineer", location: str = "Remote") -> str:
    """WebSearch query for LinkedIn jobs."""
    return f'{query} {location} site:linkedin.com/jobs'


def get_all_search_queries(profile: dict) -> list[dict]:
    """
    Generate all MCP WebSearch queries based on the user's profile.

    Returns list of {"query": "...", "source": "..."} dicts.
    The Claude agent should iterate these, call WebSearch for each,
    then parse and ingest results.
    """
    roles = profile.get("preferences", {}).get("roles", ["Software Engineer"])
    locations = profile.get("preferences", {}).get("locations", ["Remote"])
    favorites = profile.get("favorite_companies", [])

    queries = []

    # Greenhouse/Lever targeted searches
    for role in roles[:3]:  # Top 3 roles
        queries.append({
            "query": search_greenhouse_web(role),
            "source": "web_greenhouse"
        })
        queries.append({
            "query": search_lever_web(role),
            "source": "web_lever"
        })

    # General job board searches
    for role in roles[:2]:
        for location in locations[:2]:
            queries.append({
                "query": f'{role} {location} hiring 2026 remote',
                "source": "web_general"
            })

    # Favorite company searches
    for company in favorites[:5]:
        queries.append({
            "query": f'{company} {roles[0]} jobs careers',
            "source": f"web_{company}"
        })

    return queries


def _extract_company_from_url(url: str) -> Optional[str]:
    """Extract company slug from Greenhouse/Lever URLs."""
    import re

    # greenhouse.io/company/jobs/...
    m = re.search(r'greenhouse\.io/([^/]+)', url)
    if m:
        return m.group(1).replace('-', ' ').title()

    # jobs.lever.co/company/...
    m = re.search(r'lever\.co/([^/]+)', url)
    if m:
        return m.group(1).replace('-', ' ').title()

    return None


def _extract_company_from_title(title: str) -> Optional[str]:
    """Try to extract company name from job title string."""
    # "... at Company" or "... - Company"
    for sep in [" at ", " - ", " | "]:
        if sep in title:
            parts = title.split(sep)
            candidate = parts[-1].strip()
            if len(candidate) > 2 and len(candidate) < 50:
                return candidate
    return None
