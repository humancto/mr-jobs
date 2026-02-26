"""
Custom career page scraper — Uses Playwright + Claude to extract jobs from any career page URL.
This lets users add ANY company's careers page, not limited to Greenhouse/Lever.
"""

import hashlib
import re


async def discover_career_page_jobs(profile: dict) -> list:
    """
    Scrape job listings from custom career page URLs specified in profile.yaml.
    Uses Playwright to load pages and Claude to extract structured job data.
    """
    from utils.discovery import Job
    from utils.brain import ClaudeBrain
    from playwright.async_api import async_playwright

    career_pages = profile.get("custom_career_pages", [])
    if not career_pages:
        return []

    all_jobs = []
    brain = ClaudeBrain(verbose=False)
    role_keywords = profile["preferences"]["roles"]

    print(f"  🌐 Scanning {len(career_pages)} custom career pages...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        for page_url in career_pages:
            try:
                print(f"    Loading: {page_url}")
                await page.goto(page_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)

                # Get page content
                page_text = await page.evaluate("""() => {
                    // Get all links and their text, focusing on job-like elements
                    const links = Array.from(document.querySelectorAll('a'));
                    const jobLinks = links.filter(a => {
                        const text = a.textContent.toLowerCase();
                        const href = (a.href || '').toLowerCase();
                        return (href.includes('job') || href.includes('career') ||
                                href.includes('position') || href.includes('opening') ||
                                text.length > 10);
                    });
                    return jobLinks.map(a => ({
                        text: a.textContent.trim().substring(0, 200),
                        href: a.href
                    })).filter(j => j.text.length > 5).slice(0, 100);
                }""")

                if not page_text:
                    print(f"      No job links found on {page_url}")
                    continue

                # Use Claude to extract jobs from the link data
                import json
                result = brain.ask_json(f"""Extract job postings from this career page data.
Filter for roles matching these keywords: {', '.join(role_keywords)}

Page URL: {page_url}
Links found on page:
{json.dumps(page_text[:50], indent=2)}

Return a JSON array of jobs:
[
  {{"title": "Job Title", "url": "full URL", "location": "location if visible", "company": "company name"}}
]

Only include actual job postings, not nav links or blog posts. If no matching jobs, return [].
""")

                if isinstance(result, list):
                    for item in result:
                        title = item.get("title", "")
                        url = item.get("url", "")
                        if not title or not url:
                            continue

                        job_id = hashlib.md5(url.encode()).hexdigest()[:16]
                        company = item.get("company", _extract_domain(page_url))

                        job = Job(
                            id=f"career_{job_id}",
                            title=title,
                            company=company,
                            location=item.get("location", "Unknown"),
                            url=url,
                            apply_url=url,
                            platform="career_page",
                            description="",
                            department="",
                            metadata={
                                "source": "career_page",
                                "source_url": page_url,
                            }
                        )
                        all_jobs.append(job)

                    print(f"      Found {len(result)} matching jobs")

            except Exception as e:
                print(f"      ⚠ Failed to scrape {page_url}: {e}")
                continue

        await browser.close()

    print(f"  📊 Career pages total: {len(all_jobs)} jobs found")
    return all_jobs


def _extract_domain(url: str) -> str:
    """Extract company name from URL domain."""
    match = re.search(r'://(?:www\.)?([^/]+)', url)
    if match:
        domain = match.group(1)
        # Remove common suffixes
        for suffix in ['.com', '.io', '.co', '.org', '.net']:
            domain = domain.replace(suffix, '')
        return domain
    return "Unknown"
