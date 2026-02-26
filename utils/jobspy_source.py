"""
JobSpy integration — Broad keyword-based job search across multiple platforms.
Uses python-jobspy to search Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google.
"""

import traceback
from typing import Optional


def discover_jobspy_jobs(profile: dict) -> list:
    """
    Search for jobs using python-jobspy across multiple job boards.
    Each source is independently optional — if one fails, others continue.
    """
    from utils.discovery import Job

    search_config = profile.get("search", {})
    queries = search_config.get("queries", profile["preferences"].get("roles", []))
    locations = search_config.get("locations", profile["preferences"].get("locations", ["Remote"]))
    distance = search_config.get("distance_miles", 50)
    results_wanted = search_config.get("results_per_query", 25)

    all_jobs = []

    try:
        from jobspy import scrape_jobs
    except ImportError:
        print("  ⚠ python-jobspy not installed. Run: pip install python-jobspy")
        return []

    # Sites to search
    sites = ["indeed", "linkedin", "glassdoor", "zip_recruiter", "google"]

    for query in queries:
        for location in locations:
            print(f"  🔍 Searching: '{query}' in '{location}'...")
            try:
                results = scrape_jobs(
                    site_name=sites,
                    search_term=query,
                    location=location,
                    distance=distance,
                    results_wanted=results_wanted,
                    country_indeed="USA",
                    is_remote=profile["preferences"].get("remote_only", False),
                )

                if results is None or len(results) == 0:
                    continue

                for _, row in results.iterrows():
                    try:
                        title = str(row.get("title", ""))
                        company = str(row.get("company_name", "Unknown"))
                        job_location = str(row.get("location", location))
                        job_url = str(row.get("job_url", ""))
                        description = str(row.get("description", ""))
                        site = str(row.get("site", "jobspy"))
                        date_posted = str(row.get("date_posted", ""))

                        # Generate a stable ID from URL or title+company
                        import hashlib
                        job_id = hashlib.md5(
                            (job_url or f"{title}_{company}").encode()
                        ).hexdigest()[:16]

                        # Extract salary info if available
                        salary_min = None
                        salary_max = None
                        try:
                            if row.get("min_amount") is not None:
                                salary_min = int(row["min_amount"])
                            if row.get("max_amount") is not None:
                                salary_max = int(row["max_amount"])
                        except (ValueError, TypeError):
                            pass

                        job = Job(
                            id=f"jobspy_{job_id}",
                            title=title,
                            company=company,
                            location=job_location,
                            url=job_url,
                            apply_url=job_url,
                            platform=f"jobspy_{site}",
                            description=description[:5000],
                            department="",
                            metadata={
                                "source": site,
                                "date_posted": date_posted,
                                "salary_min": salary_min,
                                "salary_max": salary_max,
                            }
                        )
                        all_jobs.append(job)
                    except Exception as e:
                        continue

                print(f"    Found {len(results)} jobs for '{query}' in '{location}'")

            except Exception as e:
                print(f"    ⚠ Search failed for '{query}' in '{location}': {e}")
                continue

    print(f"  📊 JobSpy total: {len(all_jobs)} jobs found")
    return all_jobs
