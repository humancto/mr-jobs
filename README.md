# MR.Jobs — AI-Powered Job Intelligence

Discovers jobs from 7+ sources, scores them with AI, auto-fills applications via browser automation, and tracks your entire pipeline from discovery to offer.

## Quick Start

```bash
# Option 1: Docker (recommended)
docker compose up

# Option 2: Local
bash setup.sh
nano profile.yaml
python3 main.py server --port 8080
```

Open `http://localhost:8080` — complete the setup wizard on first run.

## How It Works

```
DISCOVER  →  7+ sources (Greenhouse, Lever, Indeed, LinkedIn, RemoteOK, HN, custom)
SCORE     →  AI scores each job 0-100 against your profile + resume
TAILOR    →  AI generates tailored resume content for top matches
APPLY     →  Playwright fills forms, AI handles custom questions
TRACK     →  Follow-up reminders, ghost detection, email monitoring
```

## Commands

| Command                           | Description                  |
| --------------------------------- | ---------------------------- |
| `python3 main.py server`          | Launch dashboard + scheduler |
| `python3 main.py discover`        | Find & score jobs            |
| `python3 main.py apply --dry-run` | Fill forms, don't submit     |
| `python3 main.py apply --live`    | Submit real applications     |
| `python3 main.py single <url>`    | Test a single job URL        |
| `python3 main.py rescore`         | Re-score all unscored jobs   |
| `python3 main.py stats`           | View pipeline statistics     |

## Architecture

```
main.py                    CLI entry + orchestrator
profile.yaml               Your config (roles, skills, companies)
applications.db            SQLite tracker (WAL mode)
scheduler.py               Background jobs (APScheduler)

dashboard/
  server.py                FastAPI + REST API + WebSocket
  templates/index.html     Dashboard (Alpine.js + Tailwind + Chart.js)
  static/app.js            Dashboard logic
  static/style.css         Theme

utils/
  brain.py                 AI scoring engine (Claude CLI)
  llm.py                   Pluggable LLM backends
  tracker.py               SQLite CRUD + events
  discovery.py             Job source registry
  resume_tailor.py         AI resume tailoring
  resume_parser.py         PDF text extraction

adapters/
  greenhouse.py            Greenhouse ATS automation
  generic.py               AI-driven generic form filler
```

## Safety

- Dry run by default (`--live` required to submit)
- Daily rate limits (configurable)
- Duplicate detection across runs
- Browser visible in headed mode
- Confirmation required for real applications
