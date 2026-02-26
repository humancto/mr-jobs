# 🚀 Auto-Apply — AI-Powered Job Application Bot

Uses **Claude Code CLI** as the AI brain + **Playwright** for browser automation to discover, match, and auto-fill job applications.

## How It Works

```
1. DISCOVER  →  Scrapes Greenhouse/Lever job boards for matching roles
2. MATCH     →  Claude CLI scores each job against your profile (0-100)
3. APPLY     →  Playwright fills forms, Claude handles custom questions
```

## Quick Start

```bash
# 1. Setup
bash setup.sh

# 2. Edit your profile
nano profile.yaml       # Add your info, target companies, preferences

# 3. Place your resume
cp ~/path/to/resume.pdf ./resume.pdf

# 4. Authenticate Claude CLI (one time)
claude auth

# 5. Discover matching jobs
python3 main.py discover

# 6. Dry run — fills forms but doesn't submit
python3 main.py apply --dry-run

# 7. Go live (when you're confident)
python3 main.py apply --live
```

## Commands

| Command | What it does |
|---------|-------------|
| `python3 main.py discover` | Find & score jobs, no applications |
| `python3 main.py apply --dry-run` | Fill forms visually, don't submit |
| `python3 main.py apply --live` | Actually submit applications |
| `python3 main.py single <url>` | Dry-run a single job URL |
| `python3 main.py single <url> --live` | Submit to a single job URL |
| `python3 main.py stats` | View application statistics |

## Architecture

```
auto-apply/
├── main.py              # CLI entry point & orchestrator
├── profile.yaml         # Your info, preferences, target companies
├── resume.pdf           # Your resume (place here)
├── requirements.txt
├── setup.sh
├── applications.db      # SQLite tracker (auto-created)
├── adapters/
│   ├── greenhouse.py    # Greenhouse ATS form filler
│   └── generic.py       # AI-driven generic form filler
└── utils/
    ├── brain.py         # Claude Code CLI wrapper
    ├── discovery.py     # Job scraping (Greenhouse/Lever APIs)
    ├── tracker.py       # SQLite application log
    └── answers.py       # Cached answers for common questions
```

## Why Claude Code CLI Instead of API?

- **Free** with Pro/Max subscription (no per-token costs)
- **No API key management** — just `claude auth`
- Perfect for personal tools with moderate throughput
- Trade-off: ~2-3s overhead per call vs API's ~1s

## Adding More Job Boards

Edit `target_boards` in `profile.yaml`:

```yaml
target_boards:
  greenhouse:
    - "stripe"
    - "figma"
    - "notion"
  lever:
    - "openai"
    - "databricks"
```

Find company slugs by visiting:
- `https://boards.greenhouse.io/{company}` 
- `https://jobs.lever.co/{company}`

## Safety Features

- **Dry run by default** — `--live` flag required to submit
- **Daily rate limits** — configurable in profile.yaml
- **Random delays** — 60-180s between applications
- **Duplicate detection** — SQLite tracks all seen jobs
- **Browser visible** — runs in headed mode so you can watch

## Extending

### Add LinkedIn Easy Apply

You'd need to:
1. Save LinkedIn login session: `context.storage_state(path="linkedin_auth.json")`
2. Create `adapters/linkedin.py` similar to the greenhouse adapter
3. Add LinkedIn-specific selectors for the Easy Apply modal wizard

### Add Workday

Workday sites are the hardest — every company customizes heavily.
The `generic.py` adapter handles these via AI form analysis.

## Tips

- Start with `discover` to see what matches before applying
- Use `single <url>` to test on individual postings
- Keep the browser visible (headed mode) to catch issues
- Check `applications.db` with any SQLite viewer for full history
