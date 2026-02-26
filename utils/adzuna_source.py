"""
Adzuna API job source — Free job search API covering US, UK, and 10+ countries.
No API key required for basic usage. Optional app_id/app_key for higher rate limits.
Docs: https://developer.adzuna.com/
"""

import hashlib
import httpx


def discover_adzuna_jobs(profile: dict) -> list:
    """Search Adzuna for matching jobs."""
    from utils.discovery import Job

    adzuna_config = profile.get("adzuna", {})
    app_id = adzuna_config.get("app_id", "")
    app_key = adzuna_config.get("app_key", "")

    search_config = profile.get("search", {})
    queries = search_config.get("queries", profile["preferences"].get("roles", []))
    locations = search_config.get("locations", ["Remote"])

    all_jobs = []
    country = "us"  # Default to US

    if not app_id or not app_key:
        print("  ⚠ Adzuna skipped (no API key). Get free key at developer.adzuna.com")
        print("    Add to profile.yaml: adzuna: { app_id: '...', app_key: '...' }")
        return []

    for query in queries[:3]:
        try:
            # Adzuna API v1
            params = {
                "what": query,
                "results_per_page": 20,
                "content-type": "application/json",
            }
            if app_id and app_key:
                params["app_id"] = app_id
                params["app_key"] = app_key

            url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"

            resp = httpx.get(url, params=params, timeout=30,
                             headers={"User-Agent": "AutoApply/1.0"})

            if resp.status_code == 403 or resp.status_code == 401:
                # API key required — try without auth on different endpoint
                print(f"  \u26a0 Adzuna requires API key. Get free key at developer.adzuna.com")
                return all_jobs

            if resp.status_code != 200:
                print(f"  \u26a0 Adzuna returned {resp.status_code} for '{query}'")
                continue

            data = resp.json()
            results = data.get("results", [])

            for item in results:
                title = item.get("title", "")
                company = item.get("company", {}).get("display_name", "Unknown")
                location = item.get("location", {}).get("display_name", "")
                job_url = item.get("redirect_url", "")
                description = item.get("description", "")
                salary_min = item.get("salary_min")
                salary_max = item.get("salary_max")
                created = item.get("created", "")

                if not title or not job_url:
                    continue

                job_id = hashlib.md5(job_url.encode()).hexdigest()[:16]

                job = Job(
                    id=f"adzuna_{job_id}",
                    title=title,
                    company=company,
                    location=location,
                    url=job_url,
                    apply_url=job_url,
                    platform="adzuna",
                    description=description[:5000],
                    department="",
                    metadata={
                        "source": "adzuna",
                        "salary_min": int(salary_min) if salary_min else None,
                        "salary_max": int(salary_max) if salary_max else None,
                        "date_posted": created,
                        "category": item.get("category", {}).get("label", ""),
                    }
                )
                all_jobs.append(job)

            print(f"  \U0001f4ca Adzuna: {len(results)} results for '{query}'")

        except Exception as e:
            print(f"  \u26a0 Adzuna failed for '{query}': {e}")
            continue

    print(f"  \U0001f4ca Adzuna total: {len(all_jobs)} jobs found")
    return all_jobs
