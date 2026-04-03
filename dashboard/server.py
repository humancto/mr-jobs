"""
MR.Jobs Dashboard — FastAPI server with REST API and WebSocket support.
Serves a local web dashboard at http://localhost:8080.

Architecture notes:
- REST endpoints handle all CRUD operations against the SQLite tracker.
- A single /ws WebSocket endpoint fans out EventBus events to every browser tab
  that is currently open, enabling live updates without polling.
- Background tasks (discover, rescore, score-all) are run via asyncio.create_task
  so the HTTP response returns immediately while work continues.
"""

import asyncio
import base64
import copy
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dashboard.server")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from utils.tracker import (
    get_all_jobs,
    get_job_by_id,
    update_job_status,
    update_job_notes,
    delete_job,
    get_stats,
    get_timeline_stats,
    get_score_distribution,
    get_companies,
    log_matched,
    log_skipped,
    get_unscored_jobs,
    VALID_STATUSES,
    ignore_jobs,
    purge_all,
    purge_everything,
    get_ignored_count,
)
from utils.events import EventBus

BASE_DIR = Path(__file__).parent
RESUMES_DIR = BASE_DIR.parent / "resumes"
RESUMES_META = RESUMES_DIR / "meta.json"


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    """Start scheduler when server starts, stop when it shuts down."""
    try:
        from scheduler import setup_scheduler, start_scheduler
        setup_scheduler()
        start_scheduler()
    except Exception as e:
        print(f"  Scheduler start skipped: {e}")
    yield
    try:
        from scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass

app = FastAPI(title="MR.Jobs", version="1.0.0", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict:
    """Health check for Docker and monitoring."""
    return {"status": "ok", "profile": Path("profile.yaml").exists()}


# ---------------------------------------------------------------------------
# Static files and Jinja2 templates
# ---------------------------------------------------------------------------
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ---------------------------------------------------------------------------
# WebSocket connection registry
# ---------------------------------------------------------------------------
ws_clients: list[WebSocket] = []


def _json_safe(obj):
    """Make an object JSON-serializable (datetimes, sets, etc.)."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if hasattr(obj, "__dict__"):
        return str(obj)
    return str(obj)


async def broadcast_event(event: dict) -> None:
    """
    Send a JSON event to every connected WebSocket client.

    Dead connections are collected and removed after each broadcast
    to avoid growing the registry with stale entries.
    """
    # Pre-serialize to catch any JSON issues before sending
    try:
        payload = json.dumps(event, default=_json_safe)
    except (TypeError, ValueError) as exc:
        logger.error("broadcast_event: JSON serialization failed: %s", exc)
        # Send a simplified error event instead
        payload = json.dumps({
            "type": event.get("type", "unknown"),
            "data": {"error": f"Serialization error: {exc}"},
        })

    dead: list[WebSocket] = []
    for ws in ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in ws_clients:
            ws_clients.remove(ws)


# ---------------------------------------------------------------------------
# Bridge EventBus (sync) -> WebSocket broadcast (async)
# ---------------------------------------------------------------------------
def _on_event(event: dict) -> None:
    """
    Callback registered with EventBus.

    EventBus.emit() is called from synchronous tracker functions, so we must
    schedule the coroutine on whatever event loop is currently running.
    asyncio.ensure_future is safe to call from sync code that runs within an
    async context (i.e. while uvicorn's loop is active).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(broadcast_event(event))
    except RuntimeError:
        pass  # No event loop — running outside server context, skip


EventBus.subscribe(_on_event)


# ===========================================================================
# HTML Route
# ===========================================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Serve the single-page dashboard."""
    return templates.TemplateResponse("index.html", {"request": request})


# ===========================================================================
# REST API — Jobs
# ===========================================================================

@app.get("/api/jobs")
async def list_jobs(
    status: Optional[str] = None,
    company: Optional[str] = None,
    min_score: Optional[int] = None,
    search: Optional[str] = None,
    sort_by: str = "discovered_at",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Return a paginated, filtered list of jobs.

    Query parameters:
        status      — filter by exact status string
        company     — partial match against company name
        min_score   — only jobs with match_score >= this value
        search      — full-text search across title, company, location
        sort_by     — column name (discovered_at, match_score, company, title,
                      status, applied_at)
        sort_order  — asc | desc
        limit       — page size (default 50)
        offset      — pagination offset (default 0)
    """
    jobs, total = get_all_jobs(
        status=status,
        company=company,
        min_score=min_score,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    return {"jobs": jobs, "total": total, "limit": limit, "offset": offset}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    """Return a single job record by ID."""
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.patch("/api/jobs/{job_id}")
async def update_job(job_id: str, body: dict) -> dict:
    """
    Partially update a job.

    Accepted body keys:
        status  — must be a member of VALID_STATUSES
        notes   — free-text string
    """
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if "status" in body:
        if not update_job_status(job_id, body["status"]):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Valid values: {VALID_STATUSES}",
            )

    if "notes" in body:
        update_job_notes(job_id, body["notes"])

    return get_job_by_id(job_id)


@app.delete("/api/jobs/{job_id}")
async def remove_job(job_id: str) -> dict:
    """Permanently delete a job record."""
    if not delete_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"ok": True}


# ===========================================================================
# REST API — Ignore & Purge
# ===========================================================================

@app.post("/api/jobs/ignore")
async def ignore_selected(body: dict) -> dict:
    """Mark selected jobs as ignored. They won't reappear on future discovery runs.
    Body: {"job_ids": ["id1", "id2", ...]}
    """
    job_ids = body.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="No job_ids provided")
    count = ignore_jobs(job_ids)
    return {"ok": True, "ignored": count}


@app.post("/api/purge")
async def purge(body: dict = {}) -> dict:
    """Nuke discovery data.
    Body: {"keep_ignore_list": true} — preserves ignore list (default).
          {"keep_ignore_list": false} — full factory reset.
    """
    keep = body.get("keep_ignore_list", True)
    if keep:
        result = purge_all()
    else:
        result = purge_everything()
    return {"ok": True, **result}


@app.get("/api/ignored/count")
async def ignored_count() -> dict:
    """Return the number of ignored job hashes."""
    return {"count": get_ignored_count()}


# ===========================================================================
# REST API — Stats and metadata
# ===========================================================================

@app.get("/api/stats")
async def stats() -> dict:
    """Return aggregate counts and average score."""
    return get_stats()


@app.get("/api/stats/timeline")
async def timeline() -> list:
    """Return per-day discovery and application counts (last 30 days)."""
    return get_timeline_stats()


@app.get("/api/stats/scores")
async def scores() -> list:
    """Return match-score distribution bucketed into score brackets."""
    return get_score_distribution()


@app.get("/api/companies")
async def companies() -> list:
    """Return distinct company names for the filter dropdown."""
    return get_companies()


@app.get("/api/statuses")
async def statuses() -> list:
    """Return all valid status strings."""
    return VALID_STATUSES


# ===========================================================================
# REST API — Follow-ups & Ghost Detection
# ===========================================================================

@app.get("/api/follow-ups")
async def get_follow_ups() -> dict:
    """Get overdue follow-ups and ghost alerts."""
    from utils.tracker import get_overdue_follow_ups, get_ghost_alerts
    return {
        "overdue": get_overdue_follow_ups(),
        "ghosts": get_ghost_alerts(days=14),
    }


@app.post("/api/jobs/{job_id}/follow-up")
async def mark_follow_up(job_id: str, body: dict = {}) -> dict:
    """Mark follow-up done and reschedule."""
    from utils.tracker import increment_follow_up
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    days = body.get("next_days", 7)
    increment_follow_up(job_id, days=days)
    return {"ok": True, "job_id": job_id, "next_days": days}


@app.post("/api/jobs/{job_id}/dismiss-follow-up")
async def dismiss_follow_up_endpoint(job_id: str) -> dict:
    """Clear follow-up reminder for a job."""
    from utils.tracker import dismiss_follow_up
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    dismiss_follow_up(job_id)
    return {"ok": True, "job_id": job_id}


# ===========================================================================
# REST API — Profile
# ===========================================================================

@app.get("/api/profile")
async def get_profile() -> dict:
    """Return the current profile.yaml as JSON, or signal setup needed."""
    import yaml
    profile_path = BASE_DIR.parent / "profile.yaml"
    if not profile_path.exists():
        return {"needs_setup": True}
    with open(profile_path) as f:
        return yaml.safe_load(f)


@app.post("/api/setup")
async def run_setup(body: dict) -> dict:
    """First-run wizard: create profile.yaml from wizard data."""
    import yaml
    profile_path = BASE_DIR.parent / "profile.yaml"
    example_path = BASE_DIR.parent / "profile.yaml.example"

    # Load defaults from example
    defaults = {}
    if example_path.exists():
        with open(example_path) as f:
            defaults = yaml.safe_load(f) or {}

    # Deep merge wizard data over defaults
    def deep_merge(base, updates):
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                deep_merge(base[key], value)
            else:
                base[key] = value

    deep_merge(defaults, body)

    with open(profile_path, "w") as f:
        yaml.dump(defaults, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Try to start scheduler now that profile exists
    try:
        from scheduler import setup_scheduler, start_scheduler
        setup_scheduler()
        start_scheduler()
    except Exception:
        pass

    return defaults


@app.patch("/api/profile")
async def update_profile(body: dict) -> dict:
    """Partially update profile.yaml with recursive deep merge."""
    import yaml
    profile_path = BASE_DIR.parent / "profile.yaml"
    with open(profile_path) as f:
        profile = yaml.safe_load(f)

    def deep_merge(base, updates):
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                deep_merge(base[key], value)
            else:
                base[key] = value

    deep_merge(profile, body)

    with open(profile_path, "w") as f:
        yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Clear LLM backend cache so new ai config takes effect
    try:
        from utils.llm import clear_backend_cache
        clear_backend_cache()
    except ImportError:
        pass

    return profile


@app.post("/api/profile/score")
async def score_profile_endpoint() -> dict:
    """
    AI-powered profile and resume analysis.
    Reads profile.yaml + resume PDF, sends to Claude for scoring.
    Returns strengths, gaps, resume suggestions, role fit rankings.
    """
    import yaml

    profile_path = BASE_DIR.parent / "profile.yaml"
    with open(profile_path) as f:
        profile = yaml.safe_load(f)

    async def _do_score():
        try:
            from utils.brain import ClaudeBrain
            from utils.resume_parser import extract_resume_text

            await broadcast_event({"type": "profile_score_started", "data": {}})

            brain = ClaudeBrain(verbose=False, profile=profile)
            resume_path = profile.get("resume_path", "")
            resume_text = extract_resume_text(resume_path) if resume_path else ""

            result = brain.score_profile(profile, resume_text)

            await broadcast_event({
                "type": "profile_score_complete",
                "data": result,
            })
        except Exception as exc:
            await broadcast_event({
                "type": "profile_score_error",
                "data": {"error": str(exc)},
            })

    asyncio.create_task(_do_score())
    return {"status": "started"}


# ===========================================================================
# REST API — Resume Management
# ===========================================================================

def _load_resume_meta() -> dict:
    RESUMES_DIR.mkdir(exist_ok=True)
    if RESUMES_META.exists():
        return json.loads(RESUMES_META.read_text())
    return {}

def _save_resume_meta(meta: dict):
    RESUMES_DIR.mkdir(exist_ok=True)
    RESUMES_META.write_text(json.dumps(meta, indent=2))

def _update_profile_resume_path(path: str):
    import yaml
    profile_path = BASE_DIR.parent / "profile.yaml"
    with open(profile_path) as f:
        profile = yaml.safe_load(f)
    profile["resume_path"] = path
    with open(profile_path, "w") as f:
        yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


@app.get("/api/resumes")
async def list_resumes() -> list:
    """List all stored resumes with metadata."""
    meta = _load_resume_meta()
    return [
        {"name": name, **info, "exists": (RESUMES_DIR / info["filename"]).exists()}
        for name, info in meta.items()
    ]


@app.post("/api/resumes")
async def upload_resume(file: UploadFile = File(...), name: str = Form(...)) -> dict:
    """Upload a resume PDF with a display name."""
    RESUMES_DIR.mkdir(exist_ok=True)
    meta = _load_resume_meta()

    safe_name = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    if not safe_name:
        raise HTTPException(400, "Invalid resume name")

    filename = f"{safe_name.replace(' ', '_')}_{file.filename}"
    dest = RESUMES_DIR / filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    is_first = len(meta) == 0
    meta[safe_name] = {
        "filename": filename,
        "original_name": file.filename,
        "is_default": is_first,
    }
    _save_resume_meta(meta)

    if is_first:
        _update_profile_resume_path(str(dest))

    return {"name": safe_name, "filename": filename, "is_default": is_first}


@app.delete("/api/resumes/{name}")
async def delete_resume(name: str) -> dict:
    """Remove a resume by display name."""
    meta = _load_resume_meta()
    if name not in meta:
        raise HTTPException(404, "Resume not found")
    filepath = RESUMES_DIR / meta[name]["filename"]
    was_default = meta[name].get("is_default", False)
    if filepath.exists():
        filepath.unlink()
    del meta[name]
    if was_default and meta:
        first_key = next(iter(meta))
        meta[first_key]["is_default"] = True
        _update_profile_resume_path(str(RESUMES_DIR / meta[first_key]["filename"]))
    _save_resume_meta(meta)
    return {"ok": True}


@app.patch("/api/resumes/{name}/default")
async def set_default_resume(name: str) -> dict:
    """Set a resume as the default for applications."""
    meta = _load_resume_meta()
    if name not in meta:
        raise HTTPException(404, "Resume not found")
    for key in meta:
        meta[key]["is_default"] = (key == name)
    _save_resume_meta(meta)
    _update_profile_resume_path(str(RESUMES_DIR / meta[name]["filename"]))
    return {"ok": True, "default": name}


@app.get("/api/resumes/{name}/download")
async def download_resume(name: str):
    """Download a resume file."""
    meta = _load_resume_meta()
    if name not in meta:
        raise HTTPException(404, "Resume not found")
    filepath = RESUMES_DIR / meta[name]["filename"]
    if not filepath.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(filepath, filename=meta[name].get("original_name", meta[name]["filename"]))


# ===========================================================================
# REST API — Background actions
# ===========================================================================

@app.post("/api/discover")
async def trigger_discover() -> dict:
    """
    Launch a full discovery run in the background.

    The endpoint returns immediately with {"status": "started"}.
    Progress events (discovery_started, discovery_complete, discovery_error)
    are broadcast over the WebSocket.
    """
    import yaml

    profile_path = BASE_DIR.parent / "profile.yaml"
    with open(profile_path) as f:
        profile = yaml.safe_load(f)

    async def _run_discovery() -> None:
        try:
            await broadcast_event({"type": "discovery_started", "data": {}})

            from utils.discovery import discover_all_jobs
            from utils.tracker import is_already_seen, log_discovered

            jobs = await discover_all_jobs(profile)
            new_count = 0
            for job in jobs:
                if not is_already_seen(job.id):
                    log_discovered(job)
                    new_count += 1

            await broadcast_event(
                {
                    "type": "discovery_complete",
                    "data": {"total": len(jobs), "new": new_count},
                }
            )
        except Exception as exc:
            await broadcast_event(
                {"type": "discovery_error", "data": {"error": str(exc)}}
            )

    asyncio.create_task(_run_discovery())
    return {"status": "started"}


@app.post("/api/rescore/{job_id}")
async def rescore_job(job_id: str) -> dict:
    """
    Re-score a single job with the Claude brain.

    The endpoint returns immediately. A rescore_complete or rescore_error
    WebSocket event is emitted when finished.
    """
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def _do_rescore() -> None:
        try:
            import yaml
            from utils.brain import ClaudeBrain

            profile_path = BASE_DIR.parent / "profile.yaml"
            with open(profile_path) as f:
                profile = yaml.safe_load(f)

            brain = ClaudeBrain(verbose=False, profile=profile)
            from utils.resume_parser import extract_resume_text
            resume_text = extract_resume_text(profile.get("resume_path", ""))
            desc = (
                job.get("description", "")
                or f"Job: {job['title']} at {job['company']}. Location: {job['location']}"
            )
            result = brain.match_job(desc, profile, resume_text=resume_text)
            score: int = result.get("score", 0)
            reasoning: str = result.get("reasoning", "")
            cover_letter: str = result.get("cover_letter", "")

            log_matched(job_id, score, reasoning, cover_letter)

            min_score: int = profile["preferences"].get("min_match_score", 65)
            if score < min_score:
                log_skipped(job_id, f"Score {score} < {min_score}")

            await broadcast_event(
                {"type": "rescore_complete", "data": {"id": job_id, "score": score}}
            )
        except Exception as exc:
            await broadcast_event(
                {
                    "type": "rescore_error",
                    "data": {"id": job_id, "error": str(exc)},
                }
            )

    asyncio.create_task(_do_rescore())
    return {"status": "started"}


@app.post("/api/jobs/{job_id}/tailor")
async def tailor_job(job_id: str) -> dict:
    """Generate tailored resume content for a specific job."""
    from utils.tracker import get_tailored_resume, update_tailored_resume

    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def _do_tailor():
        try:
            import yaml
            from utils.resume_tailor import tailor_resume
            from utils.resume_parser import extract_resume_text
            from utils.brain import ClaudeBrain

            profile_path = BASE_DIR.parent / "profile.yaml"
            with open(profile_path) as f:
                profile = yaml.safe_load(f)

            resume_text = extract_resume_text(profile.get("resume_path", ""))
            desc = job.get("description", "") or f"Job: {job['title']} at {job['company']}"

            brain = ClaudeBrain(verbose=False, profile=profile)
            result = tailor_resume(desc, resume_text, profile, brain=brain)

            update_tailored_resume(job_id, result)

            await broadcast_event({
                "type": "tailor_complete",
                "data": {"id": job_id, "has_content": bool(result.get("tailored_summary"))}
            })
        except Exception as exc:
            await broadcast_event({
                "type": "tailor_error",
                "data": {"id": job_id, "error": str(exc)}
            })

    asyncio.create_task(_do_tailor())
    return {"status": "started", "job_id": job_id}


@app.get("/api/jobs/{job_id}/tailor")
async def get_tailor(job_id: str) -> dict:
    """Get tailored resume content for a job."""
    from utils.tracker import get_tailored_resume
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return get_tailored_resume(job_id)


@app.post("/api/score-all")
async def score_all_unscored() -> dict:
    """
    Score every job that currently has no match_score.

    Returns {"status": "started", "count": N} immediately.
    A score_all_complete or score_all_error WebSocket event follows.
    """
    unscored = get_unscored_jobs()

    async def _do_score_all() -> None:
        try:
            import yaml
            from utils.brain import ClaudeBrain

            profile_path = BASE_DIR.parent / "profile.yaml"
            with open(profile_path) as f:
                profile = yaml.safe_load(f)

            brain = ClaudeBrain(verbose=False, profile=profile)
            from utils.resume_parser import extract_resume_text
            resume_text = extract_resume_text(profile.get("resume_path", ""))
            min_score: int = profile["preferences"].get("min_match_score", 65)

            for job_row in unscored:
                try:
                    desc = (
                        job_row.get("description", "")
                        or (
                            f"Job: {job_row['title']} at {job_row['company']}. "
                            f"Location: {job_row['location']}"
                        )
                    )
                    result = brain.match_job(desc, profile, resume_text=resume_text)
                    score: int = result.get("score", 0)
                    log_matched(
                        job_row["id"],
                        score,
                        result.get("reasoning", ""),
                        result.get("cover_letter", ""),
                    )
                    if score < min_score:
                        log_skipped(job_row["id"], f"Score {score} < {min_score}")
                except Exception:
                    # Skip individual failures so the batch continues
                    pass

            await broadcast_event(
                {"type": "score_all_complete", "data": {"count": len(unscored)}}
            )
        except Exception as exc:
            await broadcast_event(
                {"type": "score_all_error", "data": {"error": str(exc)}}
            )

    asyncio.create_task(_do_score_all())
    return {"status": "started", "count": len(unscored)}


# ===========================================================================
# REST API — Apply (Form Filling)
# ===========================================================================

# Track active apply sessions so we can report status / cancel
_apply_state = {
    "running": False,
    "job_id": None,
    "progress": [],  # list of {job_id, status, message}
    "cancel_requested": False,
}


@app.post("/api/apply/{job_id}")
async def apply_single_job(job_id: str, body: dict = {}) -> dict:
    """
    Apply to a single job by ID. Launches Playwright, fills the form, submits.

    Body options:
        dry_run  — bool, default True. If True, fills form but doesn't submit.
    """
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.get("apply_url"):
        raise HTTPException(status_code=400, detail="Job has no apply URL")
    if _apply_state["running"]:
        raise HTTPException(status_code=409, detail="An apply session is already running")

    dry_run = body.get("dry_run", True)

    async def _do_apply():
        _apply_state["running"] = True
        _apply_state["job_id"] = job_id
        _apply_state["cancel_requested"] = False

        try:
            import yaml
            from playwright.async_api import async_playwright
            from utils.brain import ClaudeBrain
            from adapters.stagehand_adapter import apply_smart
            from utils.tracker import log_applied

            profile_path = BASE_DIR.parent / "profile.yaml"
            with open(profile_path) as f:
                profile = yaml.safe_load(f)

            brain = ClaudeBrain(verbose=False, profile=profile)
            cover_letter = job.get("cover_letter", "")

            await broadcast_event({
                "type": "apply_started",
                "data": {
                    "job_id": job_id,
                    "title": job["title"],
                    "company": job["company"],
                    "dry_run": dry_run,
                }
            })

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False, slow_mo=100)
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()

                platform = job.get("platform", "")
                apply_url = job["apply_url"]

                success = await apply_smart(
                    page, apply_url, profile, brain,
                    cover_letter=cover_letter, dry_run=dry_run,
                    platform=platform,
                    company=job.get("company", ""),
                    title=job.get("title", ""),
                    description=job.get("description", ""),
                )

                if not dry_run:
                    log_applied(job_id, success)

                await broadcast_event({
                    "type": "apply_complete",
                    "data": {
                        "job_id": job_id,
                        "success": success,
                        "dry_run": dry_run,
                        "title": job["title"],
                        "company": job["company"],
                    }
                })

                # Keep browser open for a bit so user can review
                await asyncio.sleep(5)
                await browser.close()

        except Exception as exc:
            await broadcast_event({
                "type": "apply_error",
                "data": {"job_id": job_id, "error": str(exc)}
            })
        finally:
            _apply_state["running"] = False
            _apply_state["job_id"] = None

    asyncio.create_task(_do_apply())
    return {"status": "started", "job_id": job_id, "dry_run": dry_run}


@app.post("/api/apply-batch")
async def apply_batch(body: dict = {}) -> dict:
    """
    Apply to all matched jobs above the score threshold.

    Body options:
        dry_run   — bool, default True
        max_count — int, max applications this batch (default 10)
        min_score — int, override minimum score (default from profile)
    """
    if _apply_state["running"]:
        raise HTTPException(status_code=409, detail="An apply session is already running")

    dry_run = body.get("dry_run", True)
    max_count = body.get("max_count", 10)
    min_score_override = body.get("min_score")

    # Get matched jobs that haven't been applied to yet
    from utils.tracker import get_all_jobs as get_jobs_filtered, get_today_count
    matched_jobs, _ = get_jobs_filtered(status="matched", sort_by="match_score", sort_order="desc", limit=500)

    import yaml
    profile_path = BASE_DIR.parent / "profile.yaml"
    with open(profile_path) as f:
        profile = yaml.safe_load(f)

    min_score = min_score_override or profile["preferences"].get("min_match_score", 65)
    rate_limits = profile.get("rate_limits", {})
    max_per_day = rate_limits.get("max_applications_per_day", 25)

    # Filter to only jobs with apply URLs and above score threshold
    eligible = [
        j for j in matched_jobs
        if j.get("apply_url")
        and (j.get("match_score") or 0) >= min_score
    ][:max_count]

    if not eligible:
        return {"status": "no_eligible_jobs", "count": 0}

    today_count = get_today_count()
    remaining_today = max(0, max_per_day - today_count)
    eligible = eligible[:remaining_today]

    if not eligible:
        return {"status": "daily_limit_reached", "today": today_count, "max": max_per_day}

    async def _do_batch():
        import random
        _apply_state["running"] = True
        _apply_state["progress"] = []
        _apply_state["cancel_requested"] = False

        try:
            from playwright.async_api import async_playwright
            from utils.brain import ClaudeBrain
            from adapters.stagehand_adapter import apply_smart
            from utils.tracker import log_applied

            brain = ClaudeBrain(verbose=False, profile=profile)
            min_delay = rate_limits.get("min_delay_seconds", 60)
            max_delay = rate_limits.get("max_delay_seconds", 180)

            await broadcast_event({
                "type": "apply_batch_started",
                "data": {"count": len(eligible), "dry_run": dry_run}
            })

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=False, slow_mo=100)
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = await context.new_page()

                applied_count = 0
                for i, job in enumerate(eligible):
                    if _apply_state["cancel_requested"]:
                        await broadcast_event({
                            "type": "apply_batch_cancelled",
                            "data": {"applied": applied_count, "cancelled_at": i}
                        })
                        break

                    job_id = job["id"]
                    _apply_state["job_id"] = job_id

                    await broadcast_event({
                        "type": "apply_progress",
                        "data": {
                            "current": i + 1,
                            "total": len(eligible),
                            "job_id": job_id,
                            "title": job["title"],
                            "company": job["company"],
                            "dry_run": dry_run,
                        }
                    })

                    try:
                        cover_letter = job.get("cover_letter", "")
                        platform = job.get("platform", "")
                        apply_url = job["apply_url"]

                        success = await apply_smart(
                            page, apply_url, profile, brain,
                            cover_letter=cover_letter, dry_run=dry_run,
                            platform=platform,
                            company=job.get("company", ""),
                            title=job.get("title", ""),
                            description=job.get("description", ""),
                        )

                        if not dry_run:
                            log_applied(job_id, success)

                        applied_count += 1
                        _apply_state["progress"].append({
                            "job_id": job_id, "status": "success" if success else "failed",
                            "title": job["title"], "company": job["company"],
                        })

                    except Exception as exc:
                        if not dry_run:
                            log_applied(job_id, False)
                        _apply_state["progress"].append({
                            "job_id": job_id, "status": "error",
                            "error": str(exc), "title": job["title"],
                            "company": job["company"],
                        })

                    # Rate limiting between applications
                    if i < len(eligible) - 1:
                        delay = random.randint(min_delay, max_delay)
                        await broadcast_event({
                            "type": "apply_waiting",
                            "data": {"seconds": delay, "next_index": i + 2}
                        })
                        await asyncio.sleep(delay)

                await browser.close()

            await broadcast_event({
                "type": "apply_batch_complete",
                "data": {
                    "applied": applied_count,
                    "total": len(eligible),
                    "dry_run": dry_run,
                    "results": _apply_state["progress"],
                }
            })

        except Exception as exc:
            await broadcast_event({
                "type": "apply_batch_error",
                "data": {"error": str(exc)}
            })
        finally:
            _apply_state["running"] = False
            _apply_state["job_id"] = None

    asyncio.create_task(_do_batch())
    return {
        "status": "started",
        "count": len(eligible),
        "dry_run": dry_run,
        "daily_remaining": remaining_today,
    }


@app.get("/api/apply/status")
async def apply_status() -> dict:
    """Get the current apply session status."""
    return {
        "running": _apply_state["running"],
        "current_job_id": _apply_state["job_id"],
        "progress": _apply_state["progress"],
    }


@app.post("/api/apply/cancel")
async def cancel_apply() -> dict:
    """Request cancellation of the current batch apply."""
    if not _apply_state["running"]:
        return {"status": "not_running"}
    _apply_state["cancel_requested"] = True
    return {"status": "cancel_requested"}


@app.post("/api/resolve-url/{job_id}")
async def resolve_url(job_id: str) -> dict:
    """
    Resolve a job's aggregator URL to a real ATS application form URL.
    Updates the job's apply_url in the database if resolved.
    """
    from utils.url_resolver import resolve_apply_url, is_ats_url
    from utils.tracker import get_job_by_id, update_apply_url

    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    current_url = job.get("apply_url") or job.get("url", "")
    if is_ats_url(current_url):
        return {"status": "already_ats", "url": current_url}

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            result = await resolve_apply_url(
                page,
                job_url=current_url,
                company=job.get("company", ""),
                title=job.get("title", ""),
                description=job.get("description", ""),
                platform=job.get("platform", ""),
            )

            await browser.close()

        if result["resolved_url"] != current_url:
            update_apply_url(job_id, result["resolved_url"])

        return {
            "status": result["resolution"],
            "original_url": result["original_url"],
            "resolved_url": result["resolved_url"],
            "apply_email": result.get("apply_email"),
            "company_careers": result.get("company_careers"),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ===========================================================================
# REST API — YOLO Mode (Fully Autonomous Pipeline)
# ===========================================================================

_yolo_state = {
    "running": False,
    "phase": None,       # "discover" | "score" | "apply" | "waiting"
    "cancel_requested": False,
    "log": [],           # Full action log
    "cycle": 0,          # Which cycle we're on
    "continuous": False,  # Keep looping?
}


@app.post("/api/yolo")
async def start_yolo(body: dict = {}) -> dict:
    """
    YOLO Mode — Fully autonomous: discover → score → apply → repeat.

    Body options:
        dry_run      — bool, default True. Safety net.
        continuous   — bool, default False. If True, loops forever with interval.
        interval_min — int, minutes between cycles (default 360 = 6 hours).
        max_apply    — int, max applications per cycle (default 10).
        min_score    — int, override minimum score threshold.
    """
    if _yolo_state["running"]:
        raise HTTPException(status_code=409, detail="YOLO mode already running")
    if _apply_state["running"]:
        raise HTTPException(status_code=409, detail="An apply session is already running")

    dry_run = body.get("dry_run", True)
    continuous = body.get("continuous", False)
    interval_min = body.get("interval_min", 360)
    max_apply = body.get("max_apply", 10)
    min_score_override = body.get("min_score")

    async def _yolo_pipeline():
        import random
        import yaml

        _yolo_state["running"] = True
        _yolo_state["cancel_requested"] = False
        _yolo_state["continuous"] = continuous
        _yolo_state["log"] = []
        _yolo_state["cycle"] = 0

        def ylog(msg, level="info"):
            """Append to YOLO action log and broadcast."""
            entry = {
                "time": __import__("datetime").datetime.now().isoformat(),
                "msg": msg,
                "level": level,
                "cycle": _yolo_state["cycle"],
            }
            _yolo_state["log"].append(entry)
            # Keep log bounded
            if len(_yolo_state["log"]) > 500:
                _yolo_state["log"] = _yolo_state["log"][-500:]

        try:
            while True:
                if _yolo_state["cancel_requested"]:
                    ylog("YOLO cancelled by user.", "warn")
                    await broadcast_event({"type": "yolo_cancelled", "data": {}})
                    break

                _yolo_state["cycle"] += 1
                cycle = _yolo_state["cycle"]

                await broadcast_event({
                    "type": "yolo_cycle_start",
                    "data": {"cycle": cycle, "dry_run": dry_run, "continuous": continuous}
                })
                ylog(f"=== CYCLE {cycle} START {'[DRY RUN]' if dry_run else '[LIVE]'} ===")

                # --- PHASE 1: DISCOVER ---
                _yolo_state["phase"] = "discover"
                ylog("Phase 1: Discovering jobs...")
                await broadcast_event({"type": "yolo_phase", "data": {"phase": "discover", "cycle": cycle}})

                profile_path = BASE_DIR.parent / "profile.yaml"
                with open(profile_path) as f:
                    profile = yaml.safe_load(f)

                try:
                    from utils.discovery import discover_all_jobs
                    from utils.tracker import is_already_seen, log_discovered

                    jobs = await discover_all_jobs(profile)
                    new_count = 0
                    for job in jobs:
                        if not is_already_seen(job.id):
                            log_discovered(job)
                            new_count += 1
                    ylog(f"Discovered {new_count} new jobs from {len(jobs)} total.")
                    await broadcast_event({
                        "type": "yolo_discover_done",
                        "data": {"total": len(jobs), "new": new_count, "cycle": cycle}
                    })
                except Exception as e:
                    ylog(f"Discovery error: {e}", "error")
                    await broadcast_event({"type": "yolo_error", "data": {"phase": "discover", "error": str(e)}})

                if _yolo_state["cancel_requested"]:
                    break

                # --- PHASE 2: SCORE ---
                _yolo_state["phase"] = "score"
                ylog("Phase 2: Scoring unscored jobs...")
                await broadcast_event({"type": "yolo_phase", "data": {"phase": "score", "cycle": cycle}})

                try:
                    from utils.brain import ClaudeBrain
                    from utils.resume_parser import extract_resume_text

                    brain = ClaudeBrain(verbose=False, profile=profile)
                    resume_text = extract_resume_text(profile.get("resume_path", ""))
                    unscored = get_unscored_jobs()
                    min_score = min_score_override or profile["preferences"].get("min_match_score", 65)
                    scored_count = 0

                    for job_row in unscored:
                        if _yolo_state["cancel_requested"]:
                            break
                        try:
                            desc = (
                                job_row.get("description", "")
                                or f"Job: {job_row['title']} at {job_row['company']}. Location: {job_row['location']}"
                            )
                            result = brain.match_job(desc, profile, resume_text=resume_text)
                            score = result.get("score", 0)
                            log_matched(job_row["id"], score, result.get("reasoning", ""), result.get("cover_letter", ""))
                            if score < min_score:
                                log_skipped(job_row["id"], f"Score {score} < {min_score}")
                            scored_count += 1
                            ylog(f"  Scored: {job_row['title']} @ {job_row['company']} = {score}")
                        except Exception as e:
                            ylog(f"  Score failed: {job_row['title']}: {e}", "error")

                    ylog(f"Scored {scored_count} jobs.")
                    await broadcast_event({
                        "type": "yolo_score_done",
                        "data": {"scored": scored_count, "cycle": cycle}
                    })
                except Exception as e:
                    ylog(f"Scoring error: {e}", "error")
                    await broadcast_event({"type": "yolo_error", "data": {"phase": "score", "error": str(e)}})

                if _yolo_state["cancel_requested"]:
                    break

                # --- PHASE 3: APPLY ---
                _yolo_state["phase"] = "apply"
                ylog("Phase 3: Applying to matched jobs...")
                await broadcast_event({"type": "yolo_phase", "data": {"phase": "apply", "cycle": cycle}})

                try:
                    from playwright.async_api import async_playwright
                    from adapters.stagehand_adapter import apply_smart
                    from utils.tracker import log_applied, get_today_count

                    rate_limits = profile.get("rate_limits", {})
                    max_per_day = rate_limits.get("max_applications_per_day", 25)
                    min_delay = rate_limits.get("min_delay_seconds", 60)
                    max_delay = rate_limits.get("max_delay_seconds", 180)
                    min_score = min_score_override or profile["preferences"].get("min_match_score", 65)

                    # Get matched jobs not yet applied
                    matched, _ = get_all_jobs(status="matched", sort_by="match_score", sort_order="desc", limit=500)
                    eligible = [
                        j for j in matched
                        if j.get("apply_url") and (j.get("match_score") or 0) >= min_score
                    ][:max_apply]

                    today_count = get_today_count()
                    remaining = max(0, max_per_day - today_count)
                    eligible = eligible[:remaining]

                    if not eligible:
                        ylog(f"No eligible jobs to apply to (today: {today_count}/{max_per_day}).")
                    else:
                        ylog(f"Applying to {len(eligible)} jobs...")
                        _apply_state["running"] = True

                        async with async_playwright() as p:
                            browser = await p.chromium.launch(headless=False, slow_mo=100)
                            context = await browser.new_context(
                                viewport={"width": 1920, "height": 1080},
                                user_agent=(
                                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/120.0.0.0 Safari/537.36"
                                ),
                            )
                            page = await context.new_page()
                            applied_count = 0

                            for i, job in enumerate(eligible):
                                if _yolo_state["cancel_requested"]:
                                    break

                                job_id = job["id"]
                                _apply_state["job_id"] = job_id
                                ylog(f"  [{i+1}/{len(eligible)}] {job['title']} @ {job['company']}")

                                await broadcast_event({
                                    "type": "yolo_applying",
                                    "data": {
                                        "current": i + 1, "total": len(eligible),
                                        "title": job["title"], "company": job["company"],
                                        "dry_run": dry_run,
                                    }
                                })

                                try:
                                    cover_letter = job.get("cover_letter", "")
                                    platform = job.get("platform", "")
                                    apply_url = job["apply_url"]

                                    success = await apply_smart(
                                        page, apply_url, profile, brain,
                                        cover_letter=cover_letter, dry_run=dry_run,
                                        platform=platform,
                                        company=job.get("company", ""),
                                        title=job.get("title", ""),
                                        description=job.get("description", ""),
                                    )

                                    if not dry_run:
                                        log_applied(job_id, success)

                                    status_str = "OK" if success else "FAIL"
                                    ylog(f"    -> {status_str}")
                                    applied_count += 1

                                except Exception as e:
                                    ylog(f"    -> ERROR: {e}", "error")
                                    if not dry_run:
                                        log_applied(job_id, False)

                                # Rate limit
                                if i < len(eligible) - 1:
                                    delay = random.randint(min_delay, max_delay)
                                    ylog(f"    Waiting {delay}s...")
                                    await asyncio.sleep(delay)

                            await browser.close()

                        _apply_state["running"] = False
                        _apply_state["job_id"] = None
                        ylog(f"Applied to {applied_count}/{len(eligible)} jobs.")

                    await broadcast_event({
                        "type": "yolo_apply_done",
                        "data": {"applied": applied_count if eligible else 0, "cycle": cycle, "dry_run": dry_run}
                    })

                except Exception as e:
                    ylog(f"Apply error: {e}", "error")
                    _apply_state["running"] = False
                    _apply_state["job_id"] = None
                    await broadcast_event({"type": "yolo_error", "data": {"phase": "apply", "error": str(e)}})

                # --- CYCLE COMPLETE ---
                ylog(f"=== CYCLE {cycle} COMPLETE ===")
                await broadcast_event({
                    "type": "yolo_cycle_complete",
                    "data": {"cycle": cycle, "log_size": len(_yolo_state["log"])}
                })

                if not continuous:
                    break

                # Wait for next cycle
                _yolo_state["phase"] = "waiting"
                ylog(f"Next cycle in {interval_min} minutes...")
                await broadcast_event({
                    "type": "yolo_waiting",
                    "data": {"minutes": interval_min, "next_cycle": cycle + 1}
                })

                # Sleep in small increments so cancel is responsive
                for _ in range(interval_min * 6):  # check every 10 seconds
                    if _yolo_state["cancel_requested"]:
                        break
                    await asyncio.sleep(10)

        except Exception as e:
            ylog(f"YOLO fatal error: {e}", "error")
            await broadcast_event({"type": "yolo_error", "data": {"phase": "fatal", "error": str(e)}})
        finally:
            _yolo_state["running"] = False
            _yolo_state["phase"] = None
            _apply_state["running"] = False

    asyncio.create_task(_yolo_pipeline())
    return {
        "status": "started",
        "dry_run": dry_run,
        "continuous": continuous,
        "interval_min": interval_min,
        "max_apply": max_apply,
    }


@app.get("/api/yolo/status")
async def yolo_status() -> dict:
    """Get current YOLO mode status and action log."""
    return {
        "running": _yolo_state["running"],
        "phase": _yolo_state["phase"],
        "cycle": _yolo_state["cycle"],
        "continuous": _yolo_state["continuous"],
        "log_count": len(_yolo_state["log"]),
        "recent_log": _yolo_state["log"][-20:],  # Last 20 entries
    }


@app.get("/api/yolo/log")
async def yolo_log(offset: int = 0, limit: int = 100) -> dict:
    """Get full YOLO action log with pagination."""
    log = _yolo_state["log"]
    return {
        "total": len(log),
        "entries": log[offset:offset + limit],
    }


@app.post("/api/yolo/cancel")
async def cancel_yolo() -> dict:
    """Cancel YOLO mode after current action completes."""
    if not _yolo_state["running"]:
        return {"status": "not_running"}
    _yolo_state["cancel_requested"] = True
    _apply_state["cancel_requested"] = True  # Also cancel any active apply
    return {"status": "cancel_requested"}


# ===========================================================================
# REST API — MCP-powered discovery (ingestion endpoint)
# ===========================================================================

@app.post("/api/ingest")
async def ingest_mcp_jobs(body: dict) -> dict:
    """
    Ingest job results discovered via MCP tools (WebSearch, Playwright, etc.).

    Accepts: {"jobs": [{"title": "...", "company": "...", "url": "...", ...}]}
    This is called by Claude Code agents after running MCP-powered searches.
    """
    from utils.mcp_source import ingest_jobs
    job_dicts = body.get("jobs", [])
    if not job_dicts:
        raise HTTPException(status_code=400, detail="No jobs provided")
    result = ingest_jobs(job_dicts)
    await broadcast_event({
        "type": "mcp_ingest_complete",
        "data": result
    })
    return result


# ===========================================================================
# REST API — Scheduler
# ===========================================================================

@app.get("/api/scheduler/status")
async def scheduler_status() -> dict:
    """Return scheduler state: running flag, job next-run times, last results."""
    try:
        from scheduler import get_scheduler_status
        return get_scheduler_status()
    except Exception:
        return {"running": False, "jobs": [], "last_results": {}}


@app.post("/api/scheduler/trigger/{job_name}")
async def trigger_scheduler_job(job_name: str) -> dict:
    """Manually trigger a scheduler job (discover, score, email)."""
    try:
        from scheduler import scheduler as sched
        job = sched.get_job(job_name)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job '{job_name}' not found")
        job.modify(next_run_time=__import__("datetime").datetime.now())
        return {"status": "triggered", "job": job_name}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# REST API — Email checking
# ===========================================================================

@app.post("/api/check-email")
async def check_email_now() -> dict:
    """Trigger an email check for application status updates."""
    import yaml
    profile_path = BASE_DIR.parent / "profile.yaml"
    with open(profile_path) as f:
        profile = yaml.safe_load(f)

    async def _do_check():
        try:
            from utils.email_checker import check_emails
            results = check_emails(profile)
            await broadcast_event({
                "type": "email_check_complete",
                "data": {"results": results, "count": len(results)}
            })
        except Exception as exc:
            await broadcast_event({
                "type": "email_check_error",
                "data": {"error": str(exc)}
            })

    asyncio.create_task(_do_check())
    return {"status": "started"}


# ===========================================================================
# REST API — Providers
# ===========================================================================


@app.get("/api/providers")
async def get_providers() -> dict:
    """Check which LLM providers are available for interview use."""
    from utils.llm import check_provider_availability
    all_providers = check_provider_availability()
    # Only expose interview-capable providers (not CLI-based backends)
    interview_providers = {
        k: v for k, v in all_providers.items()
        if k in ("gemini", "openai")
    }
    return {"providers": interview_providers}


# REST API — Interview Practice
# ===========================================================================

# In-memory interview sessions (keyed by session_id)
_active_interviews: dict = {}
RECORDINGS_DIR = Path(__file__).parent.parent / "recordings"


@app.post("/api/interview/start")
async def start_interview(body: dict) -> dict:
    """
    Start a new text-based interview session.

    Body:
        job_id (optional) — pull context from tracked job
        role — job title
        company — company name
        type — behavioral | technical | system_design | mixed
        difficulty — junior | mid | senior | staff
        duration — target minutes (default 30)
    """
    import yaml, copy
    from utils.brain import ClaudeBrain
    from interviewer.engine import InterviewSession

    # Load profile
    profile = {}
    try:
        with open(BASE_DIR.parent / "profile.yaml") as f:
            profile = yaml.safe_load(f) or {}
    except Exception:
        pass

    # Provider routing — override AI backend for interview component
    provider = body.get("provider", "gemini")
    # Only allow API-based providers for interviews (not CLI)
    if provider in ("claude_cli", "ollama"):
        provider = "gemini"  # Fall back to Gemini
    if provider:
        profile = copy.deepcopy(profile)
        ai = profile.setdefault("ai", {})
        ai.setdefault("backends", {})
        ai.setdefault("components", {})
        ai["components"]["interview"] = provider

    brain = ClaudeBrain(verbose=False, profile=profile)
    session = InterviewSession(brain=brain, profile=profile)

    # Config from request
    job_title = body.get("role", "Software Engineer")
    company = body.get("company", "Tech Company")
    interview_type = body.get("type", "mixed")
    difficulty = body.get("difficulty", "mid")
    duration = body.get("duration", 30)
    job_id = body.get("job_id", "")
    job_description = ""
    resume_text = ""

    # Pull context from tracked job
    if job_id:
        job = get_job_by_id(job_id)
        if job:
            job_title = job.get("title", job_title)
            company = job.get("company", company)
            job_description = job.get("description", "")

    # Load resume
    try:
        from utils.resume_parser import extract_resume_text
        resume_text = extract_resume_text(profile.get("resume_path", ""))
    except Exception:
        pass

    mode = body.get("mode", "text")
    session.configure(
        job_title=job_title,
        company=company,
        interview_type=interview_type,
        difficulty=difficulty,
        duration_minutes=duration,
        job_description=job_description,
        resume_text=resume_text,
        job_id=job_id,
        video_enabled=(mode == "video"),
    )
    session.provider = provider or "gemini"
    session.mode = mode

    # Store session immediately so polling endpoint can find it
    _active_interviews[session.session_id] = session

    # Start in background — use to_thread so blocking brain.ask() doesn't freeze the event loop
    async def _do_start():
        try:
            opening = await asyncio.to_thread(session.start)
            await broadcast_event({
                "type": "interview_started",
                "data": {
                    "session_id": session.session_id,
                    "job_title": job_title,
                    "company": company,
                    "opening": opening,
                    "job_id": job_id,
                    "provider": provider or "gemini",
                    "mode": body.get("mode", "text"),
                },
            })
        except Exception as exc:
            logger.exception("interview start failed")
            await broadcast_event({
                "type": "interview_error",
                "data": {"error": str(exc), "job_id": job_id},
            })

    asyncio.create_task(_do_start())
    return {"status": "starting", "session_id": session.session_id}


@app.post("/api/interview/respond")
async def interview_respond(body: dict) -> dict:
    """
    Send candidate's response and get interviewer reply.

    Body:
        session_id — active session ID
        text — candidate's answer text
    """
    session_id = body.get("session_id", "")
    text = body.get("text", "").strip()

    if not session_id or session_id not in _active_interviews:
        raise HTTPException(status_code=404, detail="Interview session not found")
    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    session = _active_interviews[session_id]

    async def _do_respond():
        try:
            response = await asyncio.to_thread(session.respond, text)
            should_end = session.state != "active"
            await broadcast_event({
                "type": "interview_response",
                "data": {
                    "session_id": session_id,
                    "response": response,
                    "questions_asked": session.questions_asked,
                    "should_end": should_end,
                },
            })
        except Exception as exc:
            logger.exception("interview respond failed")
            await broadcast_event({
                "type": "interview_error",
                "data": {"error": str(exc), "session_id": session_id},
            })

    asyncio.create_task(_do_respond())
    return {"status": "processing"}


@app.post("/api/interview/end")
async def end_interview(body: dict) -> dict:
    """
    End interview and run evaluation.

    Body:
        session_id — active session ID
    """
    session_id = body.get("session_id", "")
    if not session_id or session_id not in _active_interviews:
        raise HTTPException(status_code=404, detail="Interview session not found")

    session = _active_interviews[session_id]

    async def _do_end():
        try:
            closing = await asyncio.to_thread(session.end)
            if closing:
                await broadcast_event({
                    "type": "interview_response",
                    "data": {
                        "session_id": session_id,
                        "response": closing,
                        "should_end": True,
                    },
                })

            # Run evaluation
            await broadcast_event({
                "type": "interview_evaluating",
                "data": {"session_id": session_id},
            })

            evaluation = await asyncio.to_thread(session.evaluate)

            # Save to DB
            from utils.tracker import save_interview_session
            session_data = session.get_session_data()
            save_interview_session(session_data)

            # Format for display
            from interviewer.report import format_evaluation_summary
            summary = format_evaluation_summary(evaluation)

            await broadcast_event({
                "type": "interview_complete",
                "data": {
                    "session_id": session_id,
                    "evaluation": summary,
                },
            })

            # Cleanup
            _active_interviews.pop(session_id, None)

        except Exception as exc:
            logger.exception("interview end/evaluate failed")
            await broadcast_event({
                "type": "interview_error",
                "data": {"error": str(exc), "session_id": session_id},
            })

    asyncio.create_task(_do_end())
    return {"status": "ending"}


@app.get("/api/interview/state/{session_id}")
async def get_interview_live_state(session_id: str) -> dict:
    """
    Polling fallback for interview state when WebSocket is down.
    Returns current messages and session state so the frontend can recover.
    """
    session = _active_interviews.get(session_id)
    if not session:
        # Check if it was completed and saved to DB
        from utils.tracker import get_interview_session
        saved = get_interview_session(session_id)
        if saved:
            return {
                "found": True,
                "state": saved.get("state", "evaluated"),
                "transcript": saved.get("transcript", []),
                "evaluation": saved.get("evaluation"),
            }
        return {"found": False}

    return {
        "found": True,
        "state": session.state,
        "transcript": [
            {"role": e["role"], "text": e["text"]}
            for e in session.transcript
        ],
        "questions_asked": session.questions_asked,
        "evaluation": session.evaluation,
    }


@app.get("/api/interview/sessions")
async def list_interview_sessions(job_id: Optional[str] = None) -> list:
    """List past interview sessions."""
    from utils.tracker import get_interview_sessions
    return get_interview_sessions(job_id=job_id)


@app.get("/api/interview/sessions/{session_id}")
async def get_interview_session_detail(session_id: str) -> dict:
    """Get a single interview session with full transcript and evaluation."""
    from utils.tracker import get_interview_session
    session = get_interview_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# ===========================================================================
# Audio WebSocket — Realtime Voice Interviews
# ===========================================================================

@app.websocket("/ws/interview-audio/{session_id}")
async def interview_audio_ws(websocket: WebSocket, session_id: str) -> None:
    """
    Dedicated audio WebSocket for voice/video interviews.

    Client sends:
      {"type": "audio_chunk", "data": "<base64 PCM16 16kHz>"}
      {"type": "end_of_speech"}
    Server responds:
      {"type": "transcript", "role": "candidate", "text": "..."}
      {"type": "transcript", "role": "interviewer", "text": "..."}
      {"type": "audio_response", "data": "<base64 WAV>", "text": "..."}
      {"type": "status", "status": "listening|thinking|speaking"}
      {"type": "error", "message": "..."}
    """
    await websocket.accept()

    session = _active_interviews.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "No active session"})
        await websocket.close()
        return

    audio_buffer = bytearray()
    whisper_model = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "audio_chunk":
                # Accumulate audio data
                chunk_b64 = msg.get("data", "")
                if chunk_b64:
                    audio_buffer.extend(base64.b64decode(chunk_b64))

            elif msg_type == "end_of_speech":
                if not audio_buffer:
                    continue

                pcm_bytes = bytes(audio_buffer)
                audio_buffer.clear()

                # --- STT: Transcribe candidate speech ---
                await websocket.send_json({"type": "status", "status": "thinking"})

                try:
                    from interviewer.stt import transcribe_audio_bytes, load_model
                    if whisper_model is None:
                        whisper_model = await asyncio.to_thread(load_model, "base")

                    candidate_text = await asyncio.to_thread(
                        transcribe_audio_bytes, pcm_bytes, 16000, whisper_model
                    )
                except Exception as e:
                    logger.error("STT error: %s", e)
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Transcription failed: {e}",
                    })
                    await websocket.send_json({"type": "status", "status": "listening"})
                    continue

                if not candidate_text or not candidate_text.strip():
                    await websocket.send_json({"type": "status", "status": "listening"})
                    continue

                # Send candidate transcript
                await websocket.send_json({
                    "type": "transcript",
                    "role": "candidate",
                    "text": candidate_text,
                })

                # Also broadcast to the main WS feed
                await broadcast_event({
                    "type": "interview_message",
                    "session_id": session_id,
                    "role": "candidate",
                    "text": candidate_text,
                })

                # --- LLM: Generate interviewer response ---
                try:
                    interviewer_text = await asyncio.to_thread(
                        session.respond, candidate_text
                    )
                except Exception as e:
                    logger.error("Interview respond error: %s", e)
                    interviewer_text = "Could you repeat that? I want to make sure I understand."

                # Send interviewer transcript
                await websocket.send_json({
                    "type": "transcript",
                    "role": "interviewer",
                    "text": interviewer_text,
                })

                await broadcast_event({
                    "type": "interview_message",
                    "session_id": session_id,
                    "role": "interviewer",
                    "text": interviewer_text,
                })

                # --- TTS: Convert response to audio ---
                await websocket.send_json({"type": "status", "status": "speaking"})

                try:
                    from interviewer.tts import speak_to_bytes
                    wav_bytes = await asyncio.to_thread(
                        speak_to_bytes, interviewer_text
                    )
                    if wav_bytes:
                        await websocket.send_json({
                            "type": "audio_response",
                            "data": base64.b64encode(wav_bytes).decode(),
                            "text": interviewer_text,
                        })
                except Exception as e:
                    logger.warning("TTS error (non-fatal): %s", e)

                await websocket.send_json({"type": "status", "status": "listening"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("Audio WebSocket error: %s", exc)


# ===========================================================================
# Gemini Live WebSocket — Native Real-Time Audio+Video Interviews
# ===========================================================================

# Throttle state for vision analysis in Gemini Live sessions
_gemini_live_last_analysis: dict[str, float] = {}
_GEMINI_LIVE_VISION_INTERVAL = 15  # seconds between engagement analyses


async def _analyze_frame_vision_live(session, jpeg_bytes: bytes, session_id: str) -> None:
    """
    Analyze a webcam frame for engagement scoring via ClaudeBrain vision.

    Runs as a fire-and-forget background task. Throttled to at most once
    every 15 seconds per session to avoid overloading the vision API.
    """
    import time as _time

    now = _time.time()
    last = _gemini_live_last_analysis.get(session_id, 0)
    if now - last < _GEMINI_LIVE_VISION_INTERVAL:
        return
    _gemini_live_last_analysis[session_id] = now

    try:
        import yaml
        from utils.brain import ClaudeBrain

        profile = {}
        try:
            with open(BASE_DIR.parent / "profile.yaml") as f:
                profile = yaml.safe_load(f) or {}
        except Exception:
            pass

        brain = ClaudeBrain(verbose=False, profile=profile)

        analysis_prompt = (
            "Analyze this webcam frame of a job interview candidate. "
            "Rate their engagement on a scale of 1-5 and provide brief notes. "
            "Consider: eye contact, posture, facial expression, attentiveness, "
            "lighting quality, and professional appearance. "
            'Return ONLY valid JSON: {"score": <1-5>, "notes": "<brief observation>"}'
        )

        result = await asyncio.to_thread(
            brain.ask_vision_json, analysis_prompt, jpeg_bytes,
            "image/jpeg", 20, "interview",
        )

        if result and "score" in result:
            score = min(5, max(1, int(result["score"])))
            notes = result.get("notes", "")
            session.add_engagement_score(score, notes)
            logger.debug("Gemini Live vision analysis: score=%d notes=%s", score, notes)
    except Exception as e:
        logger.warning("Gemini Live frame analysis error: %s", e)


@app.websocket("/ws/interview-live/{session_id}")
async def interview_live_ws(websocket: WebSocket, session_id: str) -> None:
    """
    Gemini Live WebSocket — native real-time bidirectional audio+video.

    Uses the Gemini Live API directly for streaming conversation. No
    separate STT or TTS pipeline is needed; Gemini handles speech-to-speech
    natively with sub-second latency.

    Client sends:
      {"type": "audio_chunk", "data": "<base64 PCM16 16kHz>"}
      {"type": "video_frame", "data": "<base64 JPEG>"}
      {"type": "activity_start"}
      {"type": "activity_end"}
      {"type": "text", "text": "..."}
    Server responds:
      {"type": "transcript", "role": "interviewer", "text": "..."}
      {"type": "audio_response", "data": "<base64 PCM16>"}
      {"type": "turn_complete", "text": "..."}
      {"type": "error", "message": "..."}
    """
    from interviewer.gemini_live import GeminiLiveSession, is_gemini_live_available

    await websocket.accept()

    # Validate Gemini Live availability
    if not is_gemini_live_available():
        await websocket.send_json({
            "type": "error",
            "message": "Gemini Live API not available (missing GEMINI_API_KEY or google-genai SDK)",
        })
        await websocket.close()
        return

    # Retrieve the active interview session
    session = _active_interviews.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "No active session"})
        await websocket.close()
        return

    # Build Gemini Live session with the interview's system prompt
    # Read API key from profile if available
    gemini_api_key = ""
    try:
        import yaml
        with open(BASE_DIR.parent / "profile.yaml") as f:
            _profile = yaml.safe_load(f) or {}
        raw_key = _profile.get("ai", {}).get("backends", {}).get("gemini", {}).get("api_key", "")
        if raw_key and raw_key.startswith("${") and raw_key.endswith("}"):
            import os as _os
            gemini_api_key = _os.environ.get(raw_key[2:-1], "")
        elif raw_key:
            gemini_api_key = raw_key
    except Exception:
        pass
    system_prompt = session._build_system_prompt()
    gemini_session = GeminiLiveSession(system_prompt=system_prompt, api_key=gemini_api_key)

    try:
        await gemini_session.connect()
    except Exception as e:
        logger.error("Gemini Live connect failed: %s", e)
        await websocket.send_json({
            "type": "error",
            "message": f"Gemini Live connection failed: {e}",
        })
        await websocket.close()
        return

    await websocket.send_json({"type": "status", "status": "connected"})

    # ------------------------------------------------------------------
    # Receiver: browser → Gemini
    # ------------------------------------------------------------------
    async def _receiver_task():
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "audio_chunk":
                    chunk_b64 = msg.get("data", "")
                    if chunk_b64:
                        pcm_bytes = base64.b64decode(chunk_b64)
                        await gemini_session.send_audio(pcm_bytes)

                elif msg_type == "video_frame":
                    frame_b64 = msg.get("data", "")
                    if frame_b64:
                        jpeg_bytes = base64.b64decode(frame_b64)
                        await gemini_session.send_video_frame(jpeg_bytes)
                        # Fire-and-forget engagement analysis (throttled internally)
                        asyncio.create_task(
                            _analyze_frame_vision_live(session, jpeg_bytes, session_id)
                        )

                elif msg_type == "text":
                    text = msg.get("text", "").strip()
                    if text:
                        await gemini_session.send_text(text)
                        # Record candidate text in the interview transcript
                        session.transcript.append({
                            "role": "candidate",
                            "text": text,
                            "timestamp": __import__("time").time(),
                        })

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("Gemini Live receiver ended: %s", e)

    # ------------------------------------------------------------------
    # Sender: Gemini → browser
    # ------------------------------------------------------------------
    async def _sender_task():
        try:
            async for event in gemini_session.receive_responses():
                event_type = event.get("type", "")

                if event_type == "text":
                    text = event.get("text", "")
                    # In native audio mode, skip Gemini's thinking text (bold headers)
                    # The actual spoken content is delivered via the audio stream
                    if text and not text.strip().startswith("**"):
                        await websocket.send_json({
                            "type": "transcript",
                            "role": "interviewer",
                            "text": text,
                        })

                elif event_type == "audio":
                    audio_data = event.get("data", b"")
                    await websocket.send_json({
                        "type": "audio_response",
                        "data": base64.b64encode(audio_data).decode(),
                    })

                elif event_type == "turn_complete":
                    full_text = event.get("text", "")
                    if full_text:
                        # Record interviewer turn in the interview transcript
                        session.transcript.append({
                            "role": "interviewer",
                            "text": full_text,
                            "timestamp": __import__("time").time(),
                        })
                        session.questions_asked += 1

                        # Broadcast to the main WebSocket feed
                        await broadcast_event({
                            "type": "interview_message",
                            "session_id": session_id,
                            "role": "interviewer",
                            "text": full_text,
                        })

                    await websocket.send_json({
                        "type": "turn_complete",
                        "text": full_text,
                    })

                elif event_type == "error":
                    await websocket.send_json({
                        "type": "error",
                        "message": event.get("message", "Unknown Gemini error"),
                    })

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("Gemini Live sender ended: %s", e)

    # ------------------------------------------------------------------
    # Run both tasks concurrently; when either exits, cancel the other
    # ------------------------------------------------------------------
    receiver = asyncio.create_task(_receiver_task())
    sender = asyncio.create_task(_sender_task())

    try:
        await asyncio.gather(receiver, sender)
    except Exception as exc:
        logger.warning("Gemini Live WebSocket error: %s", exc)
    finally:
        receiver.cancel()
        sender.cancel()
        await gemini_session.close()
        # Clean up throttle state
        _gemini_live_last_analysis.pop(session_id, None)
        logger.info("Gemini Live session %s closed", session_id)


# ===========================================================================
# OpenAI Realtime WebSocket — Live Audio Phone Call Interviews
# ===========================================================================

@app.websocket("/ws/interview-openai-live/{session_id}")
async def interview_openai_live_ws(websocket: WebSocket, session_id: str) -> None:
    """
    OpenAI Realtime WebSocket — native real-time audio (phone call style).

    Uses OpenAI Realtime API for streaming audio conversation. No separate
    STT/TTS needed; OpenAI handles speech-to-speech natively with server-side
    VAD for automatic turn detection.

    Client sends:
      {"type": "audio_chunk", "data": "<base64 PCM16 24kHz>"}
      {"type": "text", "text": "..."}
    Server responds:
      {"type": "transcript", "role": "interviewer", "text": "..."}
      {"type": "audio_response", "data": "<base64 PCM16>"}
      {"type": "turn_complete", "text": "..."}
      {"type": "speech_started"}
      {"type": "speech_stopped"}
      {"type": "error", "message": "..."}
    """
    from interviewer.openai_realtime import OpenAIRealtimeSession, is_openai_realtime_available

    await websocket.accept()

    # Validate availability
    if not is_openai_realtime_available():
        await websocket.send_json({
            "type": "error",
            "message": "OpenAI Realtime API not available (missing OPENAI_API_KEY or websockets library)",
        })
        await websocket.close()
        return

    # Retrieve the active interview session
    session = _active_interviews.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "No active session"})
        await websocket.close()
        return

    # Build OpenAI Realtime session with the interview's system prompt
    # Read API key from profile if available
    openai_api_key = ""
    try:
        import yaml
        with open(BASE_DIR.parent / "profile.yaml") as f:
            _profile = yaml.safe_load(f) or {}
        raw_key = _profile.get("ai", {}).get("backends", {}).get("openai", {}).get("api_key", "")
        if raw_key and raw_key.startswith("${") and raw_key.endswith("}"):
            import os as _os
            openai_api_key = _os.environ.get(raw_key[2:-1], "")
        elif raw_key:
            openai_api_key = raw_key
    except Exception:
        pass
    system_prompt = session._build_system_prompt()
    openai_session = OpenAIRealtimeSession(system_prompt=system_prompt, api_key=openai_api_key)

    try:
        await openai_session.connect()
    except Exception as e:
        logger.error("OpenAI Realtime connect failed: %s", e)
        await websocket.send_json({
            "type": "error",
            "message": f"OpenAI Realtime connection failed: {e}",
        })
        await websocket.close()
        return

    await websocket.send_json({"type": "status", "status": "connected"})

    # ------------------------------------------------------------------
    # Receiver: browser → OpenAI
    # ------------------------------------------------------------------
    async def _receiver_task():
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "audio_chunk":
                    chunk_b64 = msg.get("data", "")
                    if chunk_b64:
                        pcm_bytes = base64.b64decode(chunk_b64)
                        await openai_session.send_audio(pcm_bytes)

                elif msg_type == "text":
                    text = msg.get("text", "").strip()
                    if text:
                        await openai_session.send_text(text)
                        session.transcript.append({
                            "role": "candidate",
                            "text": text,
                            "timestamp": __import__("time").time(),
                        })

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("OpenAI Realtime receiver ended: %s", e)

    # ------------------------------------------------------------------
    # Sender: OpenAI → browser
    # ------------------------------------------------------------------
    async def _sender_task():
        try:
            async for event in openai_session.receive_responses():
                event_type = event.get("type", "")

                if event_type == "text":
                    text = event.get("text", "")
                    await websocket.send_json({
                        "type": "transcript",
                        "role": "interviewer",
                        "text": text,
                    })

                elif event_type == "audio":
                    audio_data = event.get("data", b"")
                    await websocket.send_json({
                        "type": "audio_response",
                        "data": base64.b64encode(audio_data).decode(),
                    })

                elif event_type == "turn_complete":
                    full_text = event.get("text", "")
                    if full_text:
                        session.transcript.append({
                            "role": "interviewer",
                            "text": full_text,
                            "timestamp": __import__("time").time(),
                        })
                        session.questions_asked += 1

                        await broadcast_event({
                            "type": "interview_message",
                            "session_id": session_id,
                            "role": "interviewer",
                            "text": full_text,
                        })

                    await websocket.send_json({
                        "type": "turn_complete",
                        "text": full_text,
                    })

                elif event_type == "speech_started":
                    await websocket.send_json({"type": "speech_started"})

                elif event_type == "speech_stopped":
                    await websocket.send_json({"type": "speech_stopped"})

                elif event_type == "error":
                    await websocket.send_json({
                        "type": "error",
                        "message": event.get("message", "Unknown OpenAI error"),
                    })

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("OpenAI Realtime sender ended: %s", e)

    # ------------------------------------------------------------------
    # Run both tasks concurrently
    # ------------------------------------------------------------------
    receiver = asyncio.create_task(_receiver_task())
    sender = asyncio.create_task(_sender_task())

    try:
        await asyncio.gather(receiver, sender)
    except Exception as exc:
        logger.warning("OpenAI Realtime WebSocket error: %s", exc)
    finally:
        receiver.cancel()
        sender.cancel()
        await openai_session.close()
        logger.info("OpenAI Realtime session %s closed", session_id)


# ===========================================================================
# Video Analysis + Recording Endpoints
# ===========================================================================

@app.post("/api/interview/analyze-frame")
async def analyze_frame(body: dict) -> dict:
    """
    Analyze a webcam frame for engagement scoring.

    Body: {"session_id": "...", "frame": "<base64 JPEG>"}
    Returns: {"score": 1-5, "notes": "..."}
    """
    session_id = body.get("session_id", "")
    frame_b64 = body.get("frame", "")
    session = _active_interviews.get(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="No active session")
    if not frame_b64:
        raise HTTPException(status_code=400, detail="No frame data")

    # Use Gemini/OpenAI multimodal vision for REAL engagement analysis
    try:
        import yaml
        from utils.brain import ClaudeBrain
        profile = {}
        try:
            with open(BASE_DIR.parent / "profile.yaml") as f:
                profile = yaml.safe_load(f) or {}
        except Exception:
            pass
        brain = ClaudeBrain(verbose=False, profile=profile)

        # Decode the base64 frame to actual image bytes
        image_bytes = base64.b64decode(frame_b64)

        analysis_prompt = (
            "Analyze this webcam frame of a job interview candidate. "
            "Rate their engagement on a scale of 1-5 and provide brief notes. "
            "Consider: eye contact (are they looking at the camera?), posture (upright, slouching?), "
            "facial expression (confident, nervous, enthusiastic?), attentiveness, "
            "lighting quality, and professional appearance. "
            "Return ONLY valid JSON: {\"score\": <1-5>, \"notes\": \"<brief observation>\"}"
        )

        # Send actual image to vision-capable model (Gemini, OpenAI GPT-4o)
        result = await asyncio.to_thread(
            brain.ask_vision_json, analysis_prompt, image_bytes,
            "image/jpeg", 20, "interview"
        )

        if result and "score" in result:
            score = min(5, max(1, int(result["score"])))
            notes = result.get("notes", "")
            session.add_engagement_score(score, notes)
            return {"score": score, "notes": notes}
    except Exception as e:
        logger.warning("Frame analysis error: %s", e)

    # Fallback: neutral score
    session.add_engagement_score(3, "Analysis unavailable")
    return {"score": 3, "notes": "Analysis unavailable"}


@app.post("/api/interview/{session_id}/recording")
async def upload_recording(session_id: str, file: UploadFile = File(...)) -> dict:
    """Upload a recorded interview video (WebM)."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    recording_path = RECORDINGS_DIR / f"{session_id}.webm"

    with open(recording_path, "wb") as f:
        content = await file.read()
        f.write(content)

    logger.info("Recording saved: %s (%.1f MB)", recording_path, len(content) / 1_048_576)
    return {"path": str(recording_path), "size_bytes": len(content)}


@app.get("/api/interview/{session_id}/recording")
async def get_recording(session_id: str):
    """Serve a recorded interview video."""
    recording_path = RECORDINGS_DIR / f"{session_id}.webm"
    if not recording_path.exists():
        raise HTTPException(status_code=404, detail="Recording not found")
    return FileResponse(str(recording_path), media_type="video/webm")


# ===========================================================================
# WebSocket endpoint
# ===========================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Maintain a persistent connection with each browser tab.

    The client may send "ping" to keep the connection alive through
    proxies or firewalls that enforce idle timeouts. All other inbound
    messages are silently ignored — this is a server-push channel.
    """
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
    finally:
        if websocket in ws_clients:
            ws_clients.remove(websocket)


# ===========================================================================
# Entry point
# ===========================================================================

def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the uvicorn server.  Called from main.py or directly."""
    import uvicorn

    print(f"\n  Dashboard running at: http://localhost:{port}")
    print(f"  Press Ctrl+C to stop\n")
    uvicorn.run(app, host=host, port=port, log_level="info")
