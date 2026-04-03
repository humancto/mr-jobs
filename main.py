#!/usr/bin/env python3
"""
MR.Jobs — AI-Powered Job Intelligence
======================================

Uses Claude Code CLI as the AI brain + Playwright for browser automation.

Usage:
    # Discover & review matches (no applications sent)
    python main.py discover

    # Dry run — fill forms but don't submit
    python main.py apply --dry-run

    # Actually apply (use with caution!)
    python main.py apply

    # Apply to a single URL
    python main.py single https://boards.greenhouse.io/company/jobs/12345

    # View stats
    python main.py stats
"""

import asyncio
import argparse
import random
import sys
import yaml
from pathlib import Path

from playwright.async_api import async_playwright

from utils.brain import ClaudeBrain
from utils.discovery import discover_all_jobs
from utils.tracker import (
    is_already_seen, log_discovered, log_matched,
    log_applied, log_skipped, get_today_count, print_stats,
    reset_unscored, delete_all, get_unscored_jobs
)
from adapters.stagehand_adapter import apply_smart


def load_profile(path: str = "profile.yaml") -> dict:
    """Load and validate profile config."""
    p = Path(path)
    if not p.exists():
        print(f"❌ Profile not found: {path}")
        print(f"   Copy profile.yaml.example to profile.yaml and fill it out.")
        sys.exit(1)

    with open(p) as f:
        profile = yaml.safe_load(f)

    # Validate required fields
    personal = profile.get("personal", {})
    required = ["first_name", "last_name", "email"]
    missing = [f for f in required if not personal.get(f)]
    if missing:
        print(f"❌ Missing required fields in profile.yaml: {', '.join(missing)}")
        sys.exit(1)

    # Validate resume exists
    resume = profile.get("resume_path", "")
    if resume and not Path(resume).exists():
        print(f"⚠ Resume not found at: {resume}")
        print(f"  Applications requiring resume upload will fail.")

    return profile


async def cmd_discover(profile: dict):
    """Discover jobs and score them — no applications sent."""
    brain = ClaudeBrain(verbose=True, profile=profile)
    from utils.resume_parser import extract_resume_text
    resume_text = extract_resume_text(profile.get("resume_path", ""))

    print("\n🔍 Discovering jobs from configured boards...\n")
    jobs = await discover_all_jobs(profile)

    if not jobs:
        print("\n😕 No matching jobs found. Try:")
        print("   - Adding more companies to target_boards in profile.yaml")
        print("   - Broadening role keywords in preferences.roles")
        return

    min_score = profile["preferences"].get("min_match_score", 65)
    matches = []

    print(f"\n🧠 Scoring {len(jobs)} jobs with Claude (min score: {min_score})...\n")

    for i, job in enumerate(jobs):
        # Skip already-seen jobs
        if is_already_seen(job.id):
            print(f"  [{i+1}/{len(jobs)}] ⏭ Already seen: {job.title} @ {job.company}")
            continue

        log_discovered(job)

        print(f"  [{i+1}/{len(jobs)}] 🔍 {job.title} @ {job.company} ({job.location})")

        try:
            result = brain.match_job(job.description, profile, resume_text=resume_text)
            score = result.get("score", 0)
            should_apply = result.get("apply", False)
            reasoning = result.get("reasoning", "")
            cover_letter = result.get("cover_letter", "")

            log_matched(job.id, score, reasoning, cover_letter)

            emoji = "✅" if should_apply else "❌"
            print(f"           {emoji} Score: {score} — {reasoning}")

            if should_apply and score >= min_score:
                matches.append((job, result))
            else:
                log_skipped(job.id, f"Score {score} < {min_score}: {reasoning}")

        except Exception as e:
            print(f"           ⚠ Scoring failed: {e}")

    print(f"\n{'='*60}")
    print(f"📊 Results: {len(matches)} jobs above threshold out of {len(jobs)} scanned")
    print(f"{'='*60}")
    for job, result in matches:
        print(f"\n  🎯 {job.title} @ {job.company}")
        print(f"     Location: {job.location}")
        print(f"     Score: {result['score']}")
        print(f"     URL: {job.apply_url}")
        if result.get("skill_overlap"):
            print(f"     Matching: {', '.join(result['skill_overlap'][:5])}")
        if result.get("red_flags"):
            print(f"     Flags: {', '.join(result['red_flags'])}")

    print_stats()


async def cmd_apply(profile: dict, dry_run: bool = True):
    """Discover, score, and apply to matching jobs."""
    brain = ClaudeBrain(verbose=True, profile=profile)
    from utils.resume_parser import extract_resume_text
    resume_text = extract_resume_text(profile.get("resume_path", ""))
    rate_limits = profile.get("rate_limits", {})
    max_per_day = rate_limits.get("max_applications_per_day", 25)
    min_delay = rate_limits.get("min_delay_seconds", 60)
    max_delay = rate_limits.get("max_delay_seconds", 180)

    today_count = get_today_count()
    if today_count >= max_per_day:
        print(f"🛑 Daily limit reached ({today_count}/{max_per_day}). Try again tomorrow.")
        return

    # Discover
    print("\n🔍 Discovering jobs...\n")
    jobs = await discover_all_jobs(profile)
    if not jobs:
        print("No matching jobs found.")
        return

    # Score
    min_score = profile["preferences"].get("min_match_score", 65)
    matches = []

    print(f"\n🧠 Scoring {len(jobs)} jobs...\n")
    for job in jobs:
        if is_already_seen(job.id):
            continue
        log_discovered(job)
        try:
            result = brain.match_job(job.description, profile, resume_text=resume_text)
            score = result.get("score", 0)
            log_matched(job.id, score, result.get("reasoning", ""), result.get("cover_letter", ""))
            if result.get("apply") and score >= min_score:
                matches.append((job, result))
                print(f"  ✅ {score}: {job.title} @ {job.company}")
            else:
                log_skipped(job.id, result.get("reasoning", "Low score"))
                print(f"  ❌ {score}: {job.title} @ {job.company}")
        except Exception as e:
            print(f"  ⚠ {job.title} @ {job.company}: {e}")

    if not matches:
        print("\nNo jobs above the match threshold.")
        print_stats()
        return

    # Apply
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"\n{'='*60}")
    print(f"🚀 Applying to {len(matches)} jobs [{mode}]")
    print(f"{'='*60}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # Show browser so you can watch/intervene
            slow_mo=100
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        for i, (job, result) in enumerate(matches):
            if get_today_count() >= max_per_day:
                print(f"\n🛑 Daily limit reached ({max_per_day}). Stopping.")
                break

            print(f"\n{'─'*50}")
            print(f"[{i+1}/{len(matches)}] {job.title} @ {job.company}")
            print(f"  URL: {job.apply_url}")
            print(f"  Score: {result['score']} — {result.get('reasoning', '')}")

            try:
                cover_letter = result.get("cover_letter", "")

                success = await apply_smart(
                    page, job.apply_url, profile, brain,
                    cover_letter=cover_letter, dry_run=dry_run,
                    platform=job.platform,
                    company=job.company, title=job.title,
                    description=getattr(job, 'description', ''),
                )

                if not dry_run:
                    log_applied(job.id, success)

            except Exception as e:
                print(f"  ❌ Application failed: {e}")
                if not dry_run:
                    log_applied(job.id, False)

            # Rate limiting
            if i < len(matches) - 1:
                delay = random.randint(min_delay, max_delay)
                print(f"  ⏳ Waiting {delay}s before next application...")
                await asyncio.sleep(delay)

        await browser.close()

    print_stats()


async def cmd_single(profile: dict, url: str, dry_run: bool = True):
    """Apply to a single job URL."""
    brain = ClaudeBrain(verbose=True, profile=profile)

    print(f"\n🎯 Single application: {url}")
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"   Mode: {mode}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080}
        )
        page = await context.new_page()

        await apply_smart(page, url, profile, brain, dry_run=dry_run)

        if dry_run:
            print("\n💡 Browser staying open for review. Press Ctrl+C to exit.")
            try:
                await asyncio.sleep(300)  # Keep browser open 5 min for review
            except KeyboardInterrupt:
                pass

        await browser.close()


def cmd_reset():
    """Delete all tracked jobs for a fresh start."""
    count = delete_all()
    print(f"Deleted {count} jobs. Database is clean.")


async def cmd_rescore(profile: dict):
    """Re-score all unscored jobs."""
    import httpx
    import re as _re
    brain = ClaudeBrain(verbose=True, profile=profile)
    from utils.resume_parser import extract_resume_text
    resume_text = extract_resume_text(profile.get("resume_path", ""))
    unscored = get_unscored_jobs()

    if not unscored:
        print("No unscored jobs found.")
        return

    min_score = profile["preferences"].get("min_match_score", 65)
    print(f"\nRe-scoring {len(unscored)} unscored jobs...\n")

    for i, job_row in enumerate(unscored):
        print(f"  [{i+1}/{len(unscored)}] {job_row['title']} @ {job_row['company']}")
        try:
            desc = ""
            if job_row['platform'] == 'greenhouse':
                url = (
                    f"https://boards-api.greenhouse.io/v1/boards/"
                    f"{job_row['company']}/jobs/{job_row['id']}?content=true"
                )
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        raw = data.get("content", "")
                        desc = _re.sub(r'<[^>]+>', ' ', raw)
                        desc = _re.sub(r'\s+', ' ', desc).strip()[:5000]
            elif job_row['platform'] == 'lever':
                url = (
                    f"https://api.lever.co/v0/postings/"
                    f"{job_row['company']}/{job_row['id']}"
                )
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        desc = data.get("descriptionPlain", "")[:5000]

            if not desc:
                desc = (
                    f"Job: {job_row['title']} at {job_row['company']}. "
                    f"Location: {job_row['location']}"
                )

            result = brain.match_job(desc, profile, resume_text=resume_text)
            score = result.get("score", 0)
            reasoning = result.get("reasoning", "")
            cover_letter = result.get("cover_letter", "")

            log_matched(job_row['id'], score, reasoning, cover_letter)

            emoji = "✅" if score >= min_score else "❌"
            print(f"           {emoji} Score: {score} — {reasoning}")

            if score < min_score:
                log_skipped(job_row['id'], f"Score {score} < {min_score}: {reasoning}")

        except Exception as e:
            print(f"           ⚠ Scoring failed: {e}")

    print_stats()


def main():
    parser = argparse.ArgumentParser(
        description="MR.Jobs — AI-Powered Job Intelligence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py discover                          # Find & score jobs
  python main.py apply --dry-run                   # Fill forms, don't submit
  python main.py apply                             # Actually submit applications
  python main.py single https://boards.greenhouse.io/company/jobs/123
  python main.py single https://jobs.lever.co/company/abc --live
  python main.py stats                             # View application stats
        """
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # discover
    subparsers.add_parser("discover", help="Discover and score jobs (no applications)")

    # apply
    apply_parser = subparsers.add_parser("apply", help="Discover, score, and apply")
    apply_parser.add_argument("--dry-run", action="store_true", default=True,
                              help="Fill forms but don't submit (default)")
    apply_parser.add_argument("--live", action="store_true",
                              help="Actually submit applications")

    # single
    single_parser = subparsers.add_parser("single", help="Apply to a single URL")
    single_parser.add_argument("url", help="Job posting URL")
    single_parser.add_argument("--live", action="store_true",
                               help="Actually submit (default: dry run)")

    # stats
    subparsers.add_parser("stats", help="View application stats")

    # reset
    subparsers.add_parser("reset", help="Delete all jobs and start fresh")

    # rescore
    subparsers.add_parser("rescore", help="Re-score all unscored jobs")

    # interview
    interview_parser = subparsers.add_parser("interview", help="Start a mock interview session")
    interview_parser.add_argument("--mode", choices=["text", "voice", "video"], default="text",
                                   help="Interview mode (default: text)")
    interview_parser.add_argument("--role", default=None, help="Job title (e.g. 'Software Engineer')")
    interview_parser.add_argument("--company", default=None, help="Company name")
    interview_parser.add_argument("--type", dest="type", default="mixed",
                                   choices=["behavioral", "technical", "system_design", "mixed"],
                                   help="Interview type (default: mixed)")
    interview_parser.add_argument("--difficulty", default="mid",
                                   choices=["junior", "mid", "senior", "staff"],
                                   help="Difficulty level (default: mid)")
    interview_parser.add_argument("--duration", type=int, default=30,
                                   help="Target duration in minutes (default: 30)")
    interview_parser.add_argument("--job-id", default=None, help="Pull context from tracked job")
    interview_parser.add_argument("--output", default=None, help="Save evaluation to file")

    # server
    server_parser = subparsers.add_parser("server", help="Launch web dashboard")
    server_parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    server_parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")

    args = parser.parse_args()

    if args.command == "stats":
        print_stats()
        return

    if args.command == "reset":
        cmd_reset()
        return

    profile = load_profile()

    if args.command == "discover":
        asyncio.run(cmd_discover(profile))
    elif args.command == "apply":
        dry_run = not args.live
        asyncio.run(cmd_apply(profile, dry_run=dry_run))
    elif args.command == "single":
        dry_run = not args.live
        asyncio.run(cmd_single(profile, args.url, dry_run=dry_run))
    elif args.command == "rescore":
        asyncio.run(cmd_rescore(profile))
    elif args.command == "interview":
        brain = ClaudeBrain(verbose=True, profile=profile)
        from interviewer.cli import cmd_interview
        asyncio.run(cmd_interview(args, profile, brain))
    elif args.command == "server":
        from dashboard.server import run_server
        try:
            from scheduler import setup_scheduler
            setup_scheduler()  # Configures jobs; actual start happens in FastAPI lifespan
        except Exception as e:
            print(f"  Scheduler setup warning: {e}")
        run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
