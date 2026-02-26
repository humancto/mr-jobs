"""
Background scheduler — Runs discovery and scoring on configurable intervals.
Integrates with FastAPI server lifecycle via setup_scheduler() -> deferred start.
"""

import asyncio
import yaml
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler()
_last_results = {"discover": None, "score": None}
_configured = False


def get_profile():
    """Load profile.yaml."""
    path = Path(__file__).parent / "profile.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


async def scheduled_discover():
    """Discover new jobs from all sources."""
    try:
        from utils.discovery import discover_all_jobs
        from utils.tracker import is_already_seen, log_discovered

        profile = get_profile()
        jobs = await discover_all_jobs(profile)
        new_count = 0
        for job in jobs:
            if not is_already_seen(job.id):
                log_discovered(job)
                new_count += 1

        _last_results["discover"] = {
            "total": len(jobs),
            "new": new_count,
            "timestamp": __import__("datetime").datetime.now().isoformat()
        }
        print(f"[Scheduler] Discovery complete: {new_count} new jobs from {len(jobs)} total")
    except Exception as e:
        print(f"[Scheduler] Discovery failed: {e}")
        _last_results["discover"] = {"error": str(e)}


async def scheduled_score():
    """Score all unscored jobs."""
    try:
        from utils.tracker import get_unscored_jobs, log_matched, log_skipped
        from utils.brain import ClaudeBrain

        profile = get_profile()
        unscored = get_unscored_jobs()
        if not unscored:
            return

        brain = ClaudeBrain(verbose=False)
        min_score = profile["preferences"].get("min_match_score", 65)
        scored = 0

        for job_row in unscored:
            try:
                desc = job_row.get("description", "") or f"Job: {job_row['title']} at {job_row['company']}"
                result = brain.match_job(desc, profile)
                score = result.get("score", 0)
                log_matched(job_row["id"], score, result.get("reasoning", ""), result.get("cover_letter", ""))
                if score < min_score:
                    log_skipped(job_row["id"], f"Score {score} < {min_score}")
                scored += 1
            except Exception:
                pass

        _last_results["score"] = {
            "scored": scored,
            "timestamp": __import__("datetime").datetime.now().isoformat()
        }
        print(f"[Scheduler] Scored {scored} jobs")
    except Exception as e:
        print(f"[Scheduler] Scoring failed: {e}")
        _last_results["score"] = {"error": str(e)}


async def scheduled_email_check():
    """Check email for application status updates."""
    try:
        from utils.email_checker import check_emails
        profile = get_profile()
        results = check_emails(profile)
        _last_results["email"] = {
            "checked": len(results),
            "timestamp": __import__("datetime").datetime.now().isoformat()
        }
    except Exception as e:
        print(f"[Scheduler] Email check failed: {e}")


def setup_scheduler():
    """
    Configure scheduler jobs (but don't start yet).
    Call start_scheduler() after the event loop is running (e.g., in FastAPI lifespan).
    """
    global _configured
    profile = get_profile()
    schedule_config = profile.get("schedule", {})

    if not schedule_config.get("enabled", True):
        print("[Scheduler] Disabled in profile.yaml")
        return

    discover_hours = schedule_config.get("discover_interval_hours", 6)
    score_minutes = schedule_config.get("score_interval_minutes", 30)

    scheduler.add_job(
        scheduled_discover,
        trigger=IntervalTrigger(hours=discover_hours),
        id="discover",
        name="Job Discovery",
        replace_existing=True
    )

    scheduler.add_job(
        scheduled_score,
        trigger=IntervalTrigger(minutes=score_minutes),
        id="score",
        name="Job Scoring",
        replace_existing=True
    )

    # Also check email if configured
    email_config = profile.get("email", {})
    if email_config.get("enabled", False):
        email_hours = email_config.get("check_interval_hours", 12)
        scheduler.add_job(
            scheduled_email_check,
            trigger=IntervalTrigger(hours=email_hours),
            id="email",
            name="Email Check",
            replace_existing=True
        )

    _configured = True
    print(f"[Scheduler] Configured — Discovery every {discover_hours}h, Scoring every {score_minutes}m")


def start_scheduler():
    """Start the scheduler. Must be called from within a running event loop."""
    if _configured and not scheduler.running:
        scheduler.start()
        print("[Scheduler] Started")


def stop_scheduler():
    """Stop the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("[Scheduler] Stopped")


def get_scheduler_status() -> dict:
    """Get current scheduler status for the dashboard."""
    jobs_info = []
    try:
        for job in scheduler.get_jobs():
            jobs_info.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            })
    except Exception:
        pass
    return {
        "running": scheduler.running if hasattr(scheduler, 'running') else False,
        "jobs": jobs_info,
        "last_results": _last_results
    }
