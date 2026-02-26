"""
Job Discovery — Scrape job listings from ATS platforms and job boards.
Supports: Greenhouse, Lever, JobSpy (Indeed/LinkedIn/Glassdoor/ZipRecruiter/Google),
RSS feeds (RemoteOK), and custom career page scraping.
"""

import asyncio
import json
import re
from dataclasses import dataclass, asdict, field
from typing import Optional
from playwright.async_api import async_playwright, Page


@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    url: str
    apply_url: str
    platform: str  # "greenhouse" | "lever" | "linkedin" | "jobspy_*" | "remoteok" | "career_page"
    description: str = ""
    department: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def deduplicate_jobs(jobs: list) -> list:
    """Deduplicate jobs by (title_lower, company_lower) to avoid cross-source duplicates."""
    seen = set()
    unique = []
    for job in jobs:
        key = (job.title.lower().strip(), job.company.lower().strip())
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


async def discover_greenhouse_jobs(company_slug: str, role_keywords: list[str]) -> list[Job]:
    """
    Scrape jobs from a Greenhouse board.
    URL pattern: https://boards.greenhouse.io/{company_slug}
    API pattern: https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs

    NOTE: We do LOOSE filtering here — check title AND description against
    role keywords AND skill keywords. The AI scoring engine makes the real
    relevance decision later. Better to surface too many jobs than miss good ones.
    """
    import httpx

    jobs = []
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            data = resp.json()

        for job_data in data.get("jobs", []):
            title = job_data.get("title", "")

            # Loose filter: check title AND description for any role or skill keyword
            # This catches "SDE", "Software Developer", "Platform Eng" etc.
            title_lower = title.lower()
            raw_desc = job_data.get("content", "")
            desc_lower = re.sub(r'<[^>]+>', ' ', raw_desc).lower()
            combined = f"{title_lower} {desc_lower}"

            # Build broad keyword list: roles + any extra keywords from profile
            broad_keywords = [kw.lower() for kw in role_keywords]
            # Also match on common tech role stems
            broad_keywords.extend([
                "engineer", "developer", "architect", "sre", "devops",
                "sde", "sse", "staff", "principal", "lead",
            ])
            # Deduplicate
            broad_keywords = list(set(broad_keywords))

            if not any(kw in combined for kw in broad_keywords):
                continue

            location = job_data.get("location", {}).get("name", "Unknown")

            # Strip HTML from description
            raw_desc = job_data.get("content", "")
            description = re.sub(r'<[^>]+>', ' ', raw_desc)
            description = re.sub(r'\s+', ' ', description).strip()

            job = Job(
                id=str(job_data["id"]),
                title=title,
                company=company_slug,
                location=location,
                url=f"https://boards.greenhouse.io/{company_slug}/jobs/{job_data['id']}",
                apply_url=f"https://boards.greenhouse.io/{company_slug}/jobs/{job_data['id']}#app",
                platform="greenhouse",
                description=description[:5000],
                department=", ".join(
                    d.get("name", "") for d in job_data.get("departments", [])
                ),
                metadata={
                    "updated_at": job_data.get("updated_at", ""),
                    "requisition_id": job_data.get("requisition_id", ""),
                }
            )
            jobs.append(job)

    except Exception as e:
        print(f"  ⚠ Greenhouse [{company_slug}]: {e}")

    return jobs


async def discover_lever_jobs(company_slug: str, role_keywords: list[str]) -> list[Job]:
    """
    Scrape jobs from a Lever board.
    API pattern: https://api.lever.co/v0/postings/{company_slug}

    NOTE: Loose filtering — let the AI scoring decide relevance.
    """
    import httpx

    jobs = []
    api_url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(api_url)
            resp.raise_for_status()
            data = resp.json()

        for posting in data:
            title = posting.get("text", "")

            # Loose filter: check title AND description
            title_lower = title.lower()
            desc_lower = posting.get("descriptionPlain", "").lower()
            combined = f"{title_lower} {desc_lower}"

            broad_keywords = [kw.lower() for kw in role_keywords]
            broad_keywords.extend([
                "engineer", "developer", "architect", "sre", "devops",
                "sde", "sse", "staff", "principal", "lead",
            ])
            broad_keywords = list(set(broad_keywords))

            if not any(kw in combined for kw in broad_keywords):
                continue

            categories = posting.get("categories", {})
            location = categories.get("location", "Unknown")
            description = posting.get("descriptionPlain", "")

            job = Job(
                id=posting["id"],
                title=title,
                company=company_slug,
                location=location,
                url=posting.get("hostedUrl", ""),
                apply_url=posting.get("applyUrl", posting.get("hostedUrl", "")),
                platform="lever",
                description=description[:5000],
                department=categories.get("team", ""),
                metadata={
                    "commitment": categories.get("commitment", ""),
                    "created_at": posting.get("createdAt", ""),
                }
            )
            jobs.append(job)

    except Exception as e:
        print(f"  ⚠ Lever [{company_slug}]: {e}")

    return jobs


async def discover_all_jobs(profile: dict) -> list[Job]:
    """
    Discover jobs from all configured sources in profile.yaml.
    Runs enabled sources: greenhouse, lever, jobspy, rss, career_pages.
    Deduplicates results across sources.
    """
    all_jobs = []
    role_keywords = profile["preferences"]["roles"]
    boards = profile.get("target_boards", {})

    # Greenhouse boards
    gh_companies = boards.get("greenhouse", [])
    if gh_companies:
        print(f"\n🌿 Scanning {len(gh_companies)} Greenhouse boards...")
        tasks = [discover_greenhouse_jobs(slug, role_keywords) for slug in gh_companies]
        results = await asyncio.gather(*tasks)
        for jobs in results:
            all_jobs.extend(jobs)
            if jobs:
                print(f"   ✅ {jobs[0].company}: {len(jobs)} matching jobs")

    # Lever boards
    lever_companies = boards.get("lever", [])
    if lever_companies:
        print(f"\n🔧 Scanning {len(lever_companies)} Lever boards...")
        tasks = [discover_lever_jobs(slug, role_keywords) for slug in lever_companies]
        results = await asyncio.gather(*tasks)
        for jobs in results:
            all_jobs.extend(jobs)
            if jobs:
                print(f"   ✅ {jobs[0].company}: {len(jobs)} matching jobs")

    # JobSpy — keyword search across Indeed, LinkedIn, Glassdoor, etc.
    search_config = profile.get("search", {})
    if search_config.get("enabled", True):
        try:
            from utils.jobspy_source import discover_jobspy_jobs
            print(f"\n🔍 Searching job boards via JobSpy...")
            jobspy_jobs = discover_jobspy_jobs(profile)
            all_jobs.extend(jobspy_jobs)
        except Exception as e:
            print(f"  ⚠ JobSpy search failed: {e}")

    # RSS feeds — RemoteOK, etc.
    try:
        from utils.rss_source import discover_rss_jobs
        print(f"\n📡 Checking RSS feeds...")
        rss_jobs = discover_rss_jobs(profile)
        all_jobs.extend(rss_jobs)
    except Exception as e:
        print(f"  ⚠ RSS feeds failed: {e}")

    # Adzuna API
    try:
        from utils.adzuna_source import discover_adzuna_jobs
        print(f"\n📊 Searching Adzuna...")
        adzuna_jobs = discover_adzuna_jobs(profile)
        all_jobs.extend(adzuna_jobs)
    except Exception as e:
        print(f"  ⚠ Adzuna failed: {e}")

    # HN Who is Hiring
    try:
        from utils.hn_source import discover_hn_jobs
        print(f"\n📰 Checking HN Who is Hiring...")
        hn_jobs = discover_hn_jobs(profile)
        all_jobs.extend(hn_jobs)
    except Exception as e:
        print(f"  ⚠ HN Who is Hiring failed: {e}")

    # Custom career pages
    if profile.get("custom_career_pages"):
        try:
            from utils.career_page_source import discover_career_page_jobs
            print(f"\n🌐 Scraping custom career pages...")
            career_jobs = await discover_career_page_jobs(profile)
            all_jobs.extend(career_jobs)
        except Exception as e:
            print(f"  ⚠ Career page scraping failed: {e}")

    # Deduplicate across sources
    before = len(all_jobs)
    all_jobs = deduplicate_jobs(all_jobs)
    if before != len(all_jobs):
        print(f"\n🔄 Deduplicated: {before} -> {len(all_jobs)} unique jobs")

    print(f"\n📊 Total: {len(all_jobs)} matching jobs found")
    return all_jobs
