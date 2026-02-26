# MR.Jobs Agent Instructions

This file tells Claude Code (or any AI agent) how the MR.Jobs system works so each new session can immediately understand and operate the system.

## What This Is

A **production-grade local job hunting system** that:

1. **Discovers** jobs from Greenhouse, Lever, Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google, RemoteOK, and custom career pages
2. **Scores** each job against the user's profile using Claude CLI (`claude -p`)
3. **Tracks** everything in a local SQLite database (`applications.db`)
4. **Displays** a web dashboard at `http://localhost:8080`
5. **Applies** to matching jobs via Playwright browser automation
6. **Monitors** email for application status updates (rejections, interviews, offers)
7. **Runs continuously** via background scheduler + macOS LaunchAgent

## Architecture

```
main.py                    # CLI entry point (discover, apply, server, reset, rescore, stats)
profile.yaml               # User config: personal info, skills, search queries, preferences
applications.db            # SQLite database (WAL mode for concurrent access)

utils/
  brain.py                 # Claude CLI wrapper (scoring, profile analysis, form analysis)
  tracker.py               # SQLite CRUD + event broadcasting
  discovery.py             # Pluggable job source registry + deduplication
  jobspy_source.py         # python-jobspy integration (Indeed/LinkedIn/Glassdoor/etc.)
  rss_source.py            # RSS feeds (RemoteOK)
  career_page_source.py    # Custom career page scraping via Playwright + Claude
  resume_parser.py         # PDF resume text extraction (pdfplumber)
  email_checker.py         # IMAP email status monitoring
  answers.py               # Cached answer pattern matcher for common form questions
  events.py                # EventBus singleton for WebSocket broadcasting

dashboard/
  server.py                # FastAPI + WebSocket + REST API
  templates/index.html     # Single-page dashboard (Tailwind + Alpine.js + Chart.js)
  static/app.js            # Dashboard JavaScript
  static/style.css         # Minimal custom styles

adapters/
  greenhouse.py            # Greenhouse ATS form automation
  generic.py               # AI-driven generic form filler (any job site)

scheduler.py               # APScheduler background jobs (discover, score, email check)
service/install.sh         # macOS LaunchAgent installer
service/uninstall.sh       # macOS LaunchAgent uninstaller
```

## Key Commands

```bash
# Start the dashboard + scheduler (main way to run)
python3.11 main.py server --port 8080

# CLI-only operations
python3.11 main.py discover    # Find and score jobs
python3.11 main.py apply       # Discover + score + apply (dry-run by default)
python3.11 main.py apply --live # Actually submit applications
python3.11 main.py rescore     # Re-score all unscored jobs
python3.11 main.py reset       # Clear database
python3.11 main.py stats       # View stats

# Install as background service
bash service/install.sh        # Runs on login, restarts on crash
bash service/uninstall.sh      # Stop and remove
```

## Scoring System

- Score 0-100 per job, set in `brain.py:match_job()`
- Uses Claude CLI (`claude -p --output-format json`) with the CLAUDECODE env var stripped to avoid nested session errors
- Profile matching considers: roles, primary/secondary skills, location, remote preference, ideal job description, favorite companies (+10 bonus)
- Minimum score threshold in `profile.yaml` -> `preferences.min_match_score` (default 65)
- Scoring data is stored in `applications.db`: `match_score`, `reasoning`, `cover_letter` columns
- The `score_profile()` method analyzes the user's resume + profile for job market readiness

## Database Schema

Table: `applications`

- `id` (TEXT PRIMARY KEY) - Job ID from source platform
- `title`, `company`, `platform`, `url`, `apply_url`, `location`, `description`
- `match_score` (INTEGER 0-100), `reasoning`, `cover_letter`
- `status`: discovered | matched | applied | skipped | failed | interviewing | offer | rejected | withdrawn | archived
- `salary_min`, `salary_max`, `date_posted`, `source`, `notes`, `tags`
- `applied_at`, `discovered_at`, `metadata` (JSON)

## Email Integration

Three ways to check email for application updates:

1. **IMAP (built-in)**: Configure in `profile.yaml` under `email:` section with IMAP server + app password
2. **LocalMind MCP**: The system has `mcp__localmind__localmind_email` tool available to read Gmail directly. Use this to check for application status emails from ATS domains (greenhouse.io, lever.co, workday.com, etc.)
3. **Dashboard button**: Click "Check Email" on the dashboard to trigger a check

When checking emails, look for:

- Rejection keywords: "unfortunately", "not moving forward", "other candidates"
- Interview keywords: "schedule an interview", "next step", "phone screen"
- Offer keywords: "offer letter", "pleased to offer"
- Match detected emails to tracked jobs by company name and update status accordingly

## MCP Tools Available

### Email & Calendar

- `mcp__localmind__localmind_email` - Read unread Gmail messages (needs valid OAuth — re-auth LocalMind if `invalid_grant`)
- `mcp__localmind__localmind_email_send` - Send email (always confirm with user first)
- `mcp__localmind__localmind_email_reply` - Reply to email thread
- `mcp__localmind__localmind_calendar` - Check calendar for interview scheduling
- `mcp__localmind__localmind_calendar_create` - Create calendar events for interviews

### Job Discovery via MCP (Claude Code agent can use these directly)

- `WebSearch` - Search the web for job listings. Use `utils/mcp_source.py:get_all_search_queries(profile)` to generate optimized queries
- `mcp__playwright__browser_navigate` + `mcp__playwright__browser_snapshot` - Navigate to career pages and capture job listings
- `mcp__playwright__browser_click` - Interact with career pages (pagination, filters)
- `mcp__github__search_repositories` - Find companies with open-source presence (often hiring)

### MCP Job Discovery Workflow (for Claude Code agents)

When the user asks to find jobs, or as a supplement to automated discovery:

1. **Load profile**: Read `profile.yaml` to get roles, skills, locations, favorites
2. **Generate search queries**:
   ```python
   from utils.mcp_source import get_all_search_queries, parse_web_search_results, ingest_jobs
   queries = get_all_search_queries(profile)
   ```
3. **Run WebSearch for each query**: Call `WebSearch` MCP tool with each query string
4. **Parse results**: Call `parse_web_search_results(results, source)` to normalize
5. **Ingest into tracker**: Call `ingest_jobs(job_dicts)` or POST `/api/ingest` with `{"jobs": [...]}`
6. **Optionally browse career pages**: Use Playwright MCP to navigate to specific career pages, take snapshots, extract jobs

This gives the system a **Claude-native fallback** that works even when python-jobspy is rate-limited or blocked. The MCP search results go through the same scoring pipeline as all other sources.

### MCP Ingestion API

POST `/api/ingest` accepts `{"jobs": [{"id": "...", "title": "...", "company": "...", "url": "...", ...}]}`
and saves them to the tracker. Use this from Claude Code after WebSearch or Playwright discovery.

## REST API Endpoints

| Method | Path                           | Description                                              |
| ------ | ------------------------------ | -------------------------------------------------------- |
| GET    | `/api/jobs`                    | List jobs (filter by status, company, min_score, search) |
| GET    | `/api/jobs/{id}`               | Single job detail                                        |
| PATCH  | `/api/jobs/{id}`               | Update status or notes                                   |
| DELETE | `/api/jobs/{id}`               | Remove job                                               |
| GET    | `/api/stats`                   | Dashboard statistics                                     |
| GET    | `/api/stats/timeline`          | Applications over time                                   |
| GET    | `/api/stats/scores`            | Score distribution                                       |
| POST   | `/api/discover`                | Trigger discovery run                                    |
| POST   | `/api/rescore/{id}`            | Re-score a job                                           |
| POST   | `/api/score-all`               | Score all unscored jobs                                  |
| POST   | `/api/ingest`                  | Ingest MCP-discovered jobs into tracker                  |
| POST   | `/api/check-email`             | Check email for status updates                           |
| GET    | `/api/scheduler/status`        | Scheduler state                                          |
| POST   | `/api/scheduler/trigger/{job}` | Manually trigger scheduled job                           |
| WS     | `/ws`                          | WebSocket for real-time updates                          |

## Common Tasks for an Agent

1. **User wants to find jobs**: Run `python3.11 main.py discover` or hit POST `/api/discover`
2. **User asks about scoring**: Read from DB or explain the scoring criteria in brain.py
3. **User wants to check application status**: Use `mcp__localmind__localmind_email` to read Gmail, then match to tracked jobs
4. **User wants to modify search**: Edit `profile.yaml` search/skills sections
5. **User reports a bug**: Check logs at `logs/server.log`, check DB state, check profile.yaml config
6. **User wants to add a company**: Add to `target_boards` (Greenhouse/Lever) or `custom_career_pages` in profile.yaml

## Dependencies

```
playwright, pyyaml, httpx, python-jobspy, feedparser, pdfplumber,
fastapi, uvicorn, jinja2, apscheduler
```

Install: `pip install -r requirements.txt && playwright install chromium`
