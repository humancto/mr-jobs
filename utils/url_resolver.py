"""
URL Resolver — Follow aggregator redirects to find real ATS application forms.

Problem: JobSpy, RemoteOK, Adzuna, and HN return URLs to listing pages
(indeed.com/job/123, remoteok.com/remote-jobs/123), NOT to the actual ATS
application forms (greenhouse.io, lever.co, ashbyhq.com, workday.com).

Solution: Follow redirect chains, extract "Apply" links, and resolve to
the real ATS form URL so apply_smart() can fill the form.
"""

import re
import logging
import asyncio
from urllib.parse import urlparse, urljoin
from typing import Optional

logger = logging.getLogger("url_resolver")

# ─── Known ATS domains ─────────────────────────────────────────────────────
# If a URL already points to one of these, it's a real apply form — skip resolution.
ATS_DOMAINS = {
    "greenhouse.io",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "myworkdayjobs.com",
    "myworkday.com",
    "icims.com",
    "smartrecruiters.com",
    "jobvite.com",
    "recruitee.com",
    "breezy.hr",
    "bamboohr.com",
    "jazz.co",
    "applytojob.com",
    "ultipro.com",
    "paylocity.com",
    "taleo.net",
    "successfactors.com",
    "avature.net",
    "phenom.com",
    "eightfold.ai",
}

# ─── Aggregator domains that NEED resolution ────────────────────────────────
AGGREGATOR_DOMAINS = {
    "indeed.com",
    "linkedin.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "remoteok.com",
    "adzuna.com",
    "adzuna.co.uk",
    "news.ycombinator.com",
    "monster.com",
    "dice.com",
    "simplyhired.com",
    "careerbuilder.com",
    "google.com",  # Google Jobs links
}

# ─── Company → ATS lookup table ─────────────────────────────────────────────
# Maps company name (lowercase) to their known ATS career page.
# This is the fastest resolution — no HTTP needed.
COMPANY_ATS_MAP = {
    "anthropic": "https://job-boards.greenhouse.io/anthropic",
    "stripe": "https://stripe.com/jobs",
    "figma": "https://boards.greenhouse.io/figma",
    "notion": "https://boards.greenhouse.io/notion",
    "vercel": "https://vercel.com/careers",
    "linear": "https://jobs.ashbyhq.com/linear",
    "ramp": "https://jobs.ashbyhq.com/ramp",
    "openai": "https://boards.greenhouse.io/openai",
    "datadog": "https://careers.datadoghq.com",
    "cloudflare": "https://boards.greenhouse.io/cloudflare",
    "discord": "https://discord.com/jobs",
    "palantir": "https://jobs.lever.co/palantir",
    "scale ai": "https://boards.greenhouse.io/scaleai",
    "databricks": "https://boards.greenhouse.io/databricks",
    "plaid": "https://plaid.com/careers",
    "square": "https://careers.squareup.com",
    "block": "https://careers.squareup.com",
    "airbnb": "https://careers.airbnb.com",
    "doordash": "https://boards.greenhouse.io/doordash",
    "instacart": "https://boards.greenhouse.io/instacart",
    "coinbase": "https://www.coinbase.com/careers",
    "robinhood": "https://boards.greenhouse.io/robinhood",
    "meta": "https://www.metacareers.com",
    "google": "https://careers.google.com",
    "amazon": "https://www.amazon.jobs",
    "apple": "https://jobs.apple.com",
    "microsoft": "https://careers.microsoft.com",
    "netflix": "https://jobs.netflix.com",
    "spotify": "https://www.lifeatspotify.com",
    "uber": "https://www.uber.com/careers",
    "lyft": "https://www.lyft.com/careers",
    "snap": "https://careers.snap.com",
    "pinterest": "https://www.pinterestcareers.com",
    "dropbox": "https://jobs.dropbox.com",
    "twilio": "https://boards.greenhouse.io/twilio",
    "elastic": "https://jobs.elastic.co",
    "hashicorp": "https://www.hashicorp.com/careers",
    "confluent": "https://careers.confluent.io",
    "snowflake": "https://careers.snowflake.com",
    "mongodb": "https://www.mongodb.com/careers",
    "supabase": "https://boards.greenhouse.io/supabase",
    "retool": "https://boards.greenhouse.io/retool",
    "loom": "https://boards.greenhouse.io/loom",
    "anduril": "https://jobs.lever.co/anduril",
    "airtable": "https://boards.greenhouse.io/airtable",
    "brex": "https://www.brex.com/careers",
    "gusto": "https://boards.greenhouse.io/gusto",
    "rippling": "https://www.rippling.com/careers",
    "deel": "https://jobs.ashbyhq.com/Deel",
}


def is_ats_url(url: str) -> bool:
    """Check if URL already points to a known ATS application form."""
    try:
        host = urlparse(url).hostname or ""
        # Check exact domain and parent domain
        parts = host.split(".")
        for i in range(len(parts)):
            domain = ".".join(parts[i:])
            if domain in ATS_DOMAINS:
                return True
        return False
    except Exception:
        return False


def is_aggregator_url(url: str) -> bool:
    """Check if URL is from an aggregator that needs resolution."""
    try:
        host = urlparse(url).hostname or ""
        parts = host.split(".")
        for i in range(len(parts)):
            domain = ".".join(parts[i:])
            if domain in AGGREGATOR_DOMAINS:
                return True
        return False
    except Exception:
        return False


def _search_company_ats(company: str, title: str = "") -> Optional[str]:
    """Look up company in the ATS map and try to construct a search URL."""
    if not company:
        return None

    company_lower = company.lower().strip()

    # Direct match
    if company_lower in COMPANY_ATS_MAP:
        base = COMPANY_ATS_MAP[company_lower]
        # For Greenhouse/Lever/Ashby, we can link to the board (user searches manually)
        # Better than an aggregator URL
        return base

    # Partial match (e.g., "Anthropic Inc" → "anthropic")
    for key, base_url in COMPANY_ATS_MAP.items():
        if key in company_lower or company_lower in key:
            return base_url

    return None


async def _follow_redirects(page, url: str, timeout: int = 15000) -> Optional[str]:
    """
    Follow HTTP redirects by navigating to the URL and checking where we end up.
    Returns the final URL after all redirects, or None on failure.
    """
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if response:
            final_url = page.url
            if final_url != url and is_ats_url(final_url):
                logger.info(f"Redirect resolved: {url} → {final_url}")
                return final_url
            return final_url
        return None
    except Exception as e:
        logger.warning(f"Redirect follow failed for {url}: {e}")
        return None


async def _extract_apply_link(page, timeout: int = 5000) -> Optional[str]:
    """
    Look for "Apply" or "Apply Now" links/buttons on the current page.
    Returns the href of the apply link if found.
    """
    try:
        # Common apply button/link selectors — ordered by specificity
        apply_selectors = [
            # Direct apply links with ATS hrefs
            'a[href*="greenhouse.io"]',
            'a[href*="lever.co"]',
            'a[href*="ashbyhq.com"]',
            'a[href*="myworkdayjobs.com"]',
            'a[href*="icims.com"]',
            'a[href*="smartrecruiters.com"]',
            'a[href*="jobvite.com"]',
            # Generic apply buttons/links
            'a[href*="apply"]',
            'a[href*="application"]',
        ]

        for selector in apply_selectors:
            try:
                link = await page.wait_for_selector(selector, timeout=2000)
                if link:
                    href = await link.get_attribute("href")
                    if href and href.startswith("http"):
                        if is_ats_url(href):
                            logger.info(f"Found ATS apply link: {href}")
                            return href
                        # Non-ATS apply link — still useful
                        return href
                    elif href and href.startswith("/"):
                        base = f"{urlparse(page.url).scheme}://{urlparse(page.url).hostname}"
                        full = urljoin(base, href)
                        return full
            except Exception:
                continue

        # Fallback: JS extraction of all links containing "apply"
        apply_links = await asyncio.wait_for(
            page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href]'));
                return links
                    .filter(a => {
                        const text = (a.textContent || '').toLowerCase();
                        const href = (a.href || '').toLowerCase();
                        return (text.includes('apply') || href.includes('apply'))
                            && !text.includes('sign in') && !text.includes('log in');
                    })
                    .map(a => ({href: a.href, text: a.textContent.trim().slice(0, 50)}))
                    .slice(0, 5);
            }"""),
            timeout=5.0,
        )

        if apply_links:
            # Prefer ATS links
            for link in apply_links:
                if is_ats_url(link["href"]):
                    logger.info(f"Found ATS apply link via JS: {link['href']}")
                    return link["href"]
            # Return first apply link
            return apply_links[0]["href"]

    except Exception as e:
        logger.debug(f"Apply link extraction failed: {e}")

    return None


def _extract_urls_from_text(text: str) -> list[str]:
    """Extract URLs from text (used for HN comments)."""
    url_pattern = re.compile(
        r'https?://[^\s<>"\')\],;]+',
        re.IGNORECASE,
    )
    urls = url_pattern.findall(text)
    # Clean trailing punctuation
    cleaned = []
    for url in urls:
        url = url.rstrip(".,;:!?)")
        if len(url) > 10:
            cleaned.append(url)
    return cleaned


def _extract_email_from_text(text: str) -> Optional[str]:
    """Extract email addresses from text (used for HN comments)."""
    email_pattern = re.compile(
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
    )
    match = email_pattern.search(text)
    return match.group(0) if match else None


async def resolve_apply_url(
    page,
    job_url: str,
    company: str = "",
    title: str = "",
    description: str = "",
    platform: str = "",
) -> dict:
    """
    Resolve an aggregator URL to a real ATS application form URL.

    Strategy chain:
    1. Already an ATS URL → return as-is
    2. Company → ATS lookup (instant, no HTTP)
    3. Follow HTTP redirects (many aggregators redirect to the real job)
    4. Extract "Apply" link from the landing page
    5. Check redirected page for "Apply" link
    6. For HN: parse description text for URLs/emails
    7. Fallback: return original URL

    Returns:
        {
            "resolved_url": str,      # Best URL we found
            "resolution": str,        # How we resolved it: "ats_direct", "company_lookup",
                                      #   "redirect", "apply_link", "hn_extract", "unresolved"
            "original_url": str,      # Original input URL
            "apply_email": str|None,  # Email to apply to (HN posts)
            "company_careers": str|None,  # Company careers page URL
        }
    """
    result = {
        "resolved_url": job_url,
        "resolution": "unresolved",
        "original_url": job_url,
        "apply_email": None,
        "company_careers": None,
    }

    # ─── Strategy 1: Already an ATS URL ─────────────────────────────────────
    if is_ats_url(job_url):
        result["resolution"] = "ats_direct"
        logger.debug(f"Already ATS URL: {job_url}")
        return result

    # ─── Strategy 2: Company → ATS lookup ────────────────────────────────────
    careers_url = _search_company_ats(company, title)
    if careers_url:
        result["company_careers"] = careers_url
        # If it's an ATS URL, use it directly
        if is_ats_url(careers_url):
            result["resolved_url"] = careers_url
            result["resolution"] = "company_lookup"
            logger.info(f"Company lookup: {company} → {careers_url}")
            return result

    # ─── Strategy 3: HN comment URL/email extraction ────────────────────────
    if platform == "hackernews" or "news.ycombinator.com" in job_url:
        # HN posts have the apply info IN the description text
        if description:
            urls = _extract_urls_from_text(description)
            email = _extract_email_from_text(description)

            if email:
                result["apply_email"] = email

            # Look for ATS URLs in the description
            for url in urls:
                if is_ats_url(url):
                    result["resolved_url"] = url
                    result["resolution"] = "hn_extract"
                    logger.info(f"HN extract (ATS): {url}")
                    return result

            # Look for any careers/jobs URL
            for url in urls:
                lower = url.lower()
                if any(kw in lower for kw in ["careers", "jobs", "apply", "hiring"]):
                    result["resolved_url"] = url
                    result["resolution"] = "hn_extract"
                    logger.info(f"HN extract (careers): {url}")
                    return result

            # Any URL from the posting is better than the HN comment link
            if urls:
                result["resolved_url"] = urls[0]
                result["resolution"] = "hn_extract"
                logger.info(f"HN extract (first URL): {urls[0]}")
                return result

            # If we found an email but no URL, the company careers page is the fallback
            if email and careers_url:
                result["resolved_url"] = careers_url
                result["resolution"] = "hn_extract"
                return result

        # HN with no extractable info — company lookup is the best we can do
        if careers_url:
            result["resolved_url"] = careers_url
            result["resolution"] = "company_lookup"
            return result

        return result

    # ─── Strategy 4: Follow redirects ────────────────────────────────────────
    if is_aggregator_url(job_url):
        final_url = await _follow_redirects(page, job_url)
        if final_url and final_url != job_url:
            if is_ats_url(final_url):
                result["resolved_url"] = final_url
                result["resolution"] = "redirect"
                logger.info(f"Redirect to ATS: {job_url} → {final_url}")
                return result

            # ─── Strategy 5: Extract "Apply" link from redirected page ───────
            apply_link = await _extract_apply_link(page)
            if apply_link:
                if is_ats_url(apply_link):
                    result["resolved_url"] = apply_link
                    result["resolution"] = "apply_link"
                    logger.info(f"Apply link on redirected page: {apply_link}")
                    return result
                # Non-ATS apply link is still better than aggregator
                result["resolved_url"] = apply_link
                result["resolution"] = "apply_link"
                return result

            # Redirected to a non-ATS page — still better than aggregator
            result["resolved_url"] = final_url
            result["resolution"] = "redirect"
            return result

    # ─── Strategy 6: Navigate and extract apply link from original URL ───────
    # For non-aggregator URLs we haven't handled yet (RemoteOK listing pages, etc.)
    if not is_ats_url(job_url):
        try:
            await page.goto(job_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)  # Let SPA render

            apply_link = await _extract_apply_link(page)
            if apply_link and apply_link != job_url:
                result["resolved_url"] = apply_link
                result["resolution"] = "apply_link"
                logger.info(f"Apply link on page: {apply_link}")
                return result
        except Exception as e:
            logger.warning(f"Page navigation failed for {job_url}: {e}")

    # ─── Fallback: company careers page or original URL ──────────────────────
    if careers_url:
        result["resolved_url"] = careers_url
        result["resolution"] = "company_lookup"
        return result

    return result


async def resolve_and_update_url(
    page,
    job: dict,
) -> dict:
    """
    Convenience wrapper that takes a job dict (from tracker DB) and resolves
    its apply_url. Returns the resolution result.
    """
    return await resolve_apply_url(
        page,
        job_url=job.get("apply_url") or job.get("url", ""),
        company=job.get("company", ""),
        title=job.get("title", ""),
        description=job.get("description", ""),
        platform=job.get("platform", ""),
    )
