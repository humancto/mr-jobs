# MR.Jobs

**Your AI job hunting command center.**

Discovers. Scores. Tailors. Applies. Tracks. -- All running locally on your machine.

---

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green.svg)](https://www.python.org/downloads/)
[![Docker Ready](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen.svg)](https://github.com/humancto/mr-jobs/pulls)

---

MR.Jobs is a self-hosted, AI-powered job hunting automation system. It continuously discovers jobs from 7+ sources, scores every listing against your actual resume, generates tailored application materials, auto-fills forms via browser automation, and tracks your entire pipeline with follow-up reminders and ghost detection -- all from a real-time local dashboard.

```
  DISCOVER          SCORE            TAILOR           APPLY            TRACK
  ──────────     ──────────      ──────────      ──────────      ──────────
  7+ sources     AI scores       Tailored        Playwright      Follow-ups
  scanning       0-100 vs        resume per      fills forms     & ghost
  continuously   your resume     top match       automatically   detection
```

---

## Dashboard

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="MR.Jobs Dashboard" width="800" />
</p>
<p align="center"><em>The MR.Jobs dashboard -- real-time job intelligence at a glance</em></p>

|                                                      |                                                        |
| ---------------------------------------------------- | ------------------------------------------------------ |
| ![Setup Wizard](docs/screenshots/wizard.png)         | ![Job Scoring](docs/screenshots/scoring.png)           |
| _First-run setup wizard -- no YAML editing required_ | _AI-powered job scoring with detailed reasoning_       |
| ![Resume Tailoring](docs/screenshots/tailoring.png)  | ![Follow-up Tracking](docs/screenshots/follow-ups.png) |
| _Per-job resume tailoring and cover letters_         | _Follow-up reminders and ghost detection_              |

---

## Features

### Discovery Engine -- 7+ Sources

MR.Jobs casts the widest possible net, pulling listings from direct ATS APIs, aggregated job boards, community threads, and any custom career page you point it at.

| Source              | Module                        | Coverage                                                           | API Key Required |
| ------------------- | ----------------------------- | ------------------------------------------------------------------ | ---------------- |
| Greenhouse API      | `utils/discovery.py`          | Direct company job boards (Anthropic, Stripe, Figma, Vercel, etc.) | No               |
| Lever API           | `utils/discovery.py`          | Direct company job boards                                          | No               |
| python-jobspy       | `utils/jobspy_source.py`      | Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs             | No               |
| RemoteOK            | `utils/rss_source.py`         | Remote-first tech jobs                                             | No               |
| HN Who is Hiring    | `utils/hn_source.py`          | Monthly Y Combinator hiring threads via Algolia API                | No               |
| Adzuna              | `utils/adzuna_source.py`      | Adzuna job aggregator (200k+ listings)                             | Yes (free)       |
| Custom Career Pages | `utils/career_page_source.py` | Any URL -- Playwright scrapes and AI extracts listings             | No               |

Every source is independently optional. If one source fails or is unconfigured, all others continue running normally.

### AI Scoring -- Resume-Aware Matching

Every discovered job is scored 0-100 by an AI that reads your full resume text, not just keyword matching. The scoring engine produces:

- **Match score** (0-100) based on role fit, skills overlap, location, and career trajectory
- **Apply recommendation** (yes/no) with detailed reasoning
- **Skill overlap analysis** -- which of your skills match the job requirements
- **Red flags** -- mismatches in seniority, tech stack, or requirements
- **Cover letter** -- a tailored draft generated alongside the score
- **Favorite company bonus** -- +10 points for companies you flag as top targets

The minimum score threshold is configurable (default: 65). Jobs below the threshold are automatically skipped.

### AI Resume Tailoring

For high-match jobs (score 80+), MR.Jobs generates tailored application materials specific to each posting:

- **Tailored professional summary** -- 2-3 sentences optimized for the specific role
- **Achievement bullets** -- your real accomplishments rewritten to emphasize relevance
- **Keyword alignment** -- important terms from the job posting matched to your experience
- **Emphasis guidance** -- which parts of your background to highlight or de-emphasize
- **Custom cover letter** -- a specific, non-templated letter referencing both the job requirements and your matching experience

All tailored content is based exclusively on experience that actually exists in your resume. Nothing is fabricated.

### Browser Automation

Playwright drives a visible Chromium browser to fill application forms:

- **Greenhouse adapter** -- purpose-built for Greenhouse ATS forms
- **Lever adapter** -- handles Lever-specific application flows
- **Generic AI form filler** -- uses AI to analyze and fill any ATS or custom application form
- **Custom question handling** -- AI interprets and answers freeform questions using your profile data
- **Dry-run mode by default** -- forms are filled but never submitted unless you explicitly pass `--live`
- **Headed browser** -- you can see every action as it happens and intervene at any time

### Pipeline Tracking

A SQLite database (WAL mode for concurrent access) tracks every job from discovery through final outcome:

- **Status pipeline**: discovered - matched - applied - interviewing - offer / rejected / withdrawn / archived
- **Follow-up reminders**: automatically set 7 days after application, configurable per job
- **Ghost detection**: flags applications with no response after 14 days
- **ACTION REQUIRED card**: the dashboard highlights overdue follow-ups and ghost alerts front and center
- **Email monitoring**: optional IMAP integration scans your inbox for responses from known ATS domains (Greenhouse, Lever, Workday, iCIMS, Ashby, and more)
- **Real-time updates**: WebSocket connection pushes every change to the dashboard instantly -- no polling

### Pluggable AI Backends

Claude CLI (included with a Claude Pro/Max subscription) is the default backend. You can swap to any of the following, and each AI component can be configured independently:

| Backend           | Configuration                | Notes                                               |
| ----------------- | ---------------------------- | --------------------------------------------------- |
| Claude CLI        | Default -- no setup required | Uses `claude -p` subprocess                         |
| OpenAI            | API key + model name         | GPT-4o, GPT-4-turbo, or any OpenAI model            |
| Ollama            | Local URL + model name       | Llama 3, Mistral, or any Ollama-supported model     |
| OpenAI-compatible | Custom base URL              | Any API matching the OpenAI chat completions format |

Components that can be independently routed: scoring, cover letter generation, resume tailoring, form analysis, email classification, and profile analysis.

### Setup Wizard

On first launch, MR.Jobs presents a guided setup wizard in the browser. No manual YAML editing is required to get started:

1. Enter your personal information
2. Upload your resume (PDF)
3. Select target roles and skills
4. Add target companies
5. Configure preferences and thresholds

The wizard writes `profile.yaml` for you. Advanced users can still edit the file directly.

---

## Quick Start

### Option 1: Docker (Recommended)

```bash
git clone https://github.com/humancto/mr-jobs.git
cd mrjobs
docker compose up
```

Open [http://localhost:8080](http://localhost:8080) and complete the setup wizard.

To pass a Claude auth token for the default AI backend:

```bash
CLAUDE_AUTH_TOKEN=your_token docker compose up
```

### Option 2: Local Install

```bash
git clone https://github.com/humancto/mr-jobs.git
cd mrjobs
bash setup.sh
python3 main.py server --port 8080
```

Open [http://localhost:8080](http://localhost:8080) and complete the setup wizard.

The setup script installs Python dependencies, the Claude Code CLI, and Playwright's Chromium browser.

**Prerequisites:**

- Python 3.11+
- Node.js 18+ (for Claude Code CLI)

### Option 3: CLI Only

```bash
# Complete the setup wizard first, or copy and edit profile.yaml manually
cp profile.yaml.example profile.yaml

# Discover and score jobs
python3 main.py discover

# Fill application forms without submitting
python3 main.py apply --dry-run

# View pipeline statistics
python3 main.py stats
```

---

## Configuration

MR.Jobs is configured through `profile.yaml`. The setup wizard generates this file, or you can create it manually from `profile.yaml.example`.

```yaml
personal:
  first_name: Jane
  last_name: Doe
  email: jane@example.com
  phone: "+1-555-000-0000"
  location: San Francisco, CA
  linkedin: https://linkedin.com/in/janedoe
  github: https://github.com/janedoe

resume_path: ./resume.pdf

preferences:
  roles:
    - Software Engineer
    - Backend Engineer
    - Platform Engineer
  keywords:
    - Python
    - distributed systems
    - AI
  min_match_score: 65
  remote_only: false
  locations:
    - San Francisco
    - Remote
  exclude_companies:
    - SomeCompany

skills:
  primary: [Python, Go, Rust, distributed systems]
  secondary: [Kubernetes, Docker, AWS, PostgreSQL]

ideal_job_description: >
  A senior backend or platform engineering role at a technology
  company building distributed systems or AI infrastructure.

favorite_companies: [anthropic, stripe, figma, vercel]

target_boards:
  greenhouse: [anthropic, stripe, figma, vercel, datadog]
  lever: [anyscale]

search:
  enabled: true
  queries: [Software Engineer, Backend Engineer]
  locations: [San Francisco, CA, Remote]
  distance_miles: 50
  results_per_query: 25

custom_career_pages: []

rate_limits:
  max_applications_per_day: 25
  min_delay_seconds: 60
  max_delay_seconds: 180

schedule:
  discover_interval_hours: 6
  score_interval_minutes: 30
  enabled: true

email:
  enabled: false
  imap_server: imap.gmail.com
  email: ""
  app_password: ""
  check_interval_hours: 12

ai:
  default_backend: claude_cli
  backends:
    claude_cli:
      timeout: 120
    # openai:
    #   api_key: ${OPENAI_API_KEY}
    #   model: gpt-4o
    # ollama:
    #   base_url: http://localhost:11434
    #   model: llama3
  components:
    scoring: claude_cli
    cover_letter: claude_cli
    resume_tailoring: claude_cli
    form_analysis: claude_cli
    email_classification: claude_cli
    profile_analysis: claude_cli
```

---

## CLI Reference

| Command                               | Description                                                    |
| ------------------------------------- | -------------------------------------------------------------- |
| `python3 main.py server`              | Launch the web dashboard and background scheduler on port 8080 |
| `python3 main.py server --port 3000`  | Launch on a custom port                                        |
| `python3 main.py discover`            | Run all discovery sources and score found jobs                 |
| `python3 main.py apply --dry-run`     | Discover, score, and fill forms without submitting             |
| `python3 main.py apply --live`        | Discover, score, and submit real applications                  |
| `python3 main.py single <url>`        | Fill a single job application form (dry run)                   |
| `python3 main.py single <url> --live` | Submit a single real application                               |
| `python3 main.py rescore`             | Re-score all unscored jobs in the database                     |
| `python3 main.py stats`               | Print pipeline statistics to the terminal                      |
| `python3 main.py reset`               | Delete all tracked jobs and start fresh                        |

---

## API Reference

The dashboard server exposes a REST API at `http://localhost:8080`. All endpoints return JSON.

### Jobs

| Method   | Endpoint           | Description                                                                 |
| -------- | ------------------ | --------------------------------------------------------------------------- |
| `GET`    | `/api/jobs`        | List jobs with optional filters: `status`, `company`, `min_score`, `search` |
| `GET`    | `/api/jobs/{id}`   | Get full details for a single job                                           |
| `PATCH`  | `/api/jobs/{id}`   | Update job status or notes                                                  |
| `DELETE` | `/api/jobs/{id}`   | Remove a job from tracking                                                  |
| `POST`   | `/api/jobs/ignore` | Add jobs to the ignore list                                                 |

### Stats

| Method | Endpoint              | Description                              |
| ------ | --------------------- | ---------------------------------------- |
| `GET`  | `/api/stats`          | Aggregate counts by status               |
| `GET`  | `/api/stats/timeline` | Daily discovery and application activity |
| `GET`  | `/api/stats/scores`   | Score distribution across all jobs       |
| `GET`  | `/api/companies`      | List of all companies with job counts    |
| `GET`  | `/api/statuses`       | Valid status values for filtering        |

### Actions

| Method | Endpoint            | Description                                           |
| ------ | ------------------- | ----------------------------------------------------- |
| `POST` | `/api/discover`     | Trigger a full discovery run across all sources       |
| `POST` | `/api/score-all`    | Score all unscored jobs                               |
| `POST` | `/api/rescore/{id}` | Re-score a specific job                               |
| `POST` | `/api/ingest`       | Ingest externally discovered jobs (`{"jobs": [...]}`) |
| `POST` | `/api/apply/{id}`   | Apply to a specific job via browser automation        |
| `POST` | `/api/apply-batch`  | Apply to multiple jobs in sequence                    |
| `POST` | `/api/check-email`  | Check email for application status updates            |
| `POST` | `/api/purge`        | Purge jobs by criteria                                |

### Follow-ups and Tailoring

| Method | Endpoint                           | Description                                    |
| ------ | ---------------------------------- | ---------------------------------------------- |
| `GET`  | `/api/follow-ups`                  | List overdue follow-ups and ghost alerts       |
| `POST` | `/api/jobs/{id}/follow-up`         | Set or update a follow-up reminder             |
| `POST` | `/api/jobs/{id}/dismiss-follow-up` | Dismiss a follow-up reminder                   |
| `POST` | `/api/jobs/{id}/tailor`            | Generate tailored resume content for a job     |
| `GET`  | `/api/jobs/{id}/tailor`            | Retrieve previously generated tailored content |

### Profile and Setup

| Method  | Endpoint             | Description                                         |
| ------- | -------------------- | --------------------------------------------------- |
| `GET`   | `/api/profile`       | Read the current profile configuration              |
| `PATCH` | `/api/profile`       | Update profile fields                               |
| `POST`  | `/api/setup`         | Complete the first-run setup wizard                 |
| `POST`  | `/api/profile/score` | AI analysis of profile strength and market position |
| `GET`   | `/api/resumes`       | List uploaded resumes                               |
| `POST`  | `/api/resumes`       | Upload a new resume (PDF)                           |

### Scheduler

| Method | Endpoint                       | Description                                                          |
| ------ | ------------------------------ | -------------------------------------------------------------------- |
| `GET`  | `/api/scheduler/status`        | Current scheduler state and next run times                           |
| `POST` | `/api/scheduler/trigger/{job}` | Manually trigger a scheduled job (discover, score, email, follow_up) |

---

## Architecture

```
                    ┌──────────────────────┐
                    │   MR.Jobs Dashboard   │
                    │   localhost:8080      │
                    └─────────┬────────────┘
                              │ REST + WebSocket
                    ┌─────────┴────────────┐
                    │   FastAPI Server      │
                    │   dashboard/server.py │
                    └─────────┬────────────┘
                              │
           ┌──────────┬───────┴───────┬───────────┐
           │          │               │            │
     ┌─────┴───┐ ┌───┴────┐  ┌──────┴─────┐ ┌───┴──────┐
     │ Discover │ │ Score  │  │   Track    │ │ Schedule │
     │ Engine   │ │ Engine │  │   Engine   │ │  Engine  │
     └─────┬───┘ └───┬────┘  └──────┬─────┘ └───┬──────┘
           │          │              │            │
           │     Pluggable LLM      │       APScheduler
           │     Backends           │       - Discovery: 6h
           │     (brain.py + llm.py)│       - Scoring: 30m
           │          │              │       - Email: 12h
           │          │              │       - Follow-ups: 6h
           │          │              │
    ┌──────┴──────────┴──────────────┴──────┐
    │              SQLite (WAL)              │
    │           applications.db             │
    └───────────────────────────────────────┘
```

**Discovery Engine** pulls from all configured sources in parallel, deduplicates by normalized (title, company), and stores raw listings. **Score Engine** reads each job description and the candidate's resume, then calls the configured AI backend to produce scores, reasoning, and cover letters. **Track Engine** manages the SQLite database with WAL mode for safe concurrent access from CLI and dashboard. **Schedule Engine** uses APScheduler to run discovery, scoring, email checks, and follow-up detection on configurable intervals.

---

## File Structure

```
mrjobs/
  main.py                          CLI entry point and command orchestrator
  profile.yaml                     User configuration (created by setup wizard)
  profile.yaml.example             Example configuration for reference
  applications.db                  SQLite database (auto-created, WAL mode)
  scheduler.py                     APScheduler background job definitions
  requirements.txt                 Python dependencies
  setup.sh                         Local installation script
  Dockerfile                       Container image definition
  docker-compose.yml               One-command Docker deployment

  dashboard/
    server.py                      FastAPI server, REST API, WebSocket hub
    templates/
      index.html                   Dashboard UI (Alpine.js + Tailwind CSS + Chart.js)
    static/
      app.js                       Dashboard application logic
      style.css                    Dark mission-control theme

  utils/
    brain.py                       ClaudeBrain -- AI scoring and reasoning interface
    llm.py                         Pluggable LLM backend system (Claude, OpenAI, Ollama)
    tracker.py                     SQLite CRUD, schema migration, event broadcasting
    discovery.py                   Source registry, deduplication, parallel discovery
    resume_tailor.py               AI resume tailoring per job posting
    resume_parser.py               PDF text extraction with caching
    jobspy_source.py               python-jobspy (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google)
    rss_source.py                  RemoteOK JSON API
    hn_source.py                   HN "Who is Hiring" via Algolia API
    adzuna_source.py               Adzuna API integration
    career_page_source.py          Playwright + AI career page scraping
    mcp_source.py                  MCP tool helpers and search query generation
    email_checker.py               IMAP email monitoring and classification
    events.py                      EventBus singleton (sync emit to async broadcast)
    answers.py                     Form answer pattern matching

  adapters/
    greenhouse.py                  Greenhouse ATS form automation
    generic.py                     AI-driven generic form filler (any ATS)

  service/
    install.sh                     macOS LaunchAgent installer (run as background service)
    uninstall.sh                   LaunchAgent uninstaller
```

---

## Safety Features

MR.Jobs is designed to keep you in control at every step:

- **Dry run by default** -- the `apply` command fills forms but never submits unless you explicitly pass `--live`
- **Daily application limits** -- configurable cap (default: 25 per day) prevents runaway submissions
- **Rate limiting** -- randomized delays between applications (60-180s default) to avoid detection
- **Deduplication** -- jobs are deduplicated by normalized (title, company) across all sources and runs
- **Headed browser** -- Playwright runs in visible mode so you can watch every action and intervene
- **Confirmation required** -- the dashboard requires explicit confirmation before submitting real applications
- **Source isolation** -- each discovery source runs in its own try/except block; one failure never breaks the others
- **No data leaves your machine** -- everything runs locally; the only external calls are to job board APIs and your configured AI backend

---

## Background Scheduling

When running in server mode, MR.Jobs automatically schedules background jobs:

| Job             | Default Interval | Description                                        |
| --------------- | ---------------- | -------------------------------------------------- |
| Discovery       | Every 6 hours    | Scans all configured sources for new listings      |
| Scoring         | Every 30 minutes | Scores any unscored jobs in the database           |
| Email Check     | Every 12 hours   | Scans inbox for application responses (if enabled) |
| Follow-up Check | Every 6 hours    | Flags overdue follow-ups and ghost applications    |

All intervals are configurable in `profile.yaml` under the `schedule` section. The scheduler can be disabled entirely by setting `schedule.enabled: false`.

You can manually trigger any scheduled job from the dashboard or via the API:

```bash
curl -X POST http://localhost:8080/api/scheduler/trigger/discover
curl -X POST http://localhost:8080/api/scheduler/trigger/score
```

---

## Contributing

Contributions are welcome. Here is how to get started:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes, following the existing code style
4. Test your changes locally (`python3 main.py discover` for discovery changes, launch the server for API/dashboard changes)
5. Commit with a clear message describing what and why
6. Open a pull request against `main`

**Guidelines:**

- Each discovery source must be independently optional -- wrap source calls in try/except
- New API endpoints should emit events via `EventBus` for real-time dashboard updates
- SQLite access must use WAL mode (`PRAGMA journal_mode=WAL`)
- AI subprocess calls must strip the `CLAUDECODE` environment variable to prevent nested session errors
- Keep the dashboard responsive -- long operations should run as background tasks via `asyncio.create_task`

Issues are welcome for bug reports, feature requests, and questions.

---

## License

MIT

---

## Acknowledgments

- Built with [Claude](https://claude.ai) by Anthropic
- [python-jobspy](https://github.com/Bunsly/JobSpy) for aggregated job search across Indeed, LinkedIn, Glassdoor, ZipRecruiter, and Google
- [Playwright](https://playwright.dev/) for reliable browser automation
- [FastAPI](https://fastapi.tiangolo.com/) for the REST API and WebSocket server
- [Alpine.js](https://alpinejs.dev/) and [Tailwind CSS](https://tailwindcss.com/) for the dashboard UI
- [APScheduler](https://apscheduler.readthedocs.io/) for background job scheduling
- The open source community
