# 🌸 Pollen — Scaling Guide

This guide covers the three scaling areas: **scheduling & alerts**, **smarter search**, and **auto-apply**.

---

## Quick Start (New Pipeline)

The main entry point is now `run_pipeline.py`:

```bash
# One-time: migrate existing database to new schema
python -m db.migrate

# Crawl + score only (safest — no status changes)
python run_pipeline.py --mode crawl

# Crawl + score + auto-mark high-scoring jobs as saved (recommended)
python run_pipeline.py --mode rank --min-score 30

# Crawl + score + mark saved + dry-run auto-apply (fills forms, no submit)
python run_pipeline.py --mode dry-apply --profile profile.yaml

# Full pipeline — crawl + score + mark saved + submit applications
python run_pipeline.py --mode apply --profile profile.yaml --limit 20
```

---

## 1. Scheduling & Alerts

### How it works

The scheduler runs the pipeline on a configurable interval. After each crawl:
1. New jobs are scored (relevance + visa detection)
2. High-scoring **Greenhouse** jobs are auto-marked as `saved` (auto-apply currently supports Greenhouse only)
3. Alerts are sent for new jobs matching your saved searches

### Running the scheduler

```bash
# Default: rank mode every 6 hours
python run_scheduler.py

# Custom interval and mode
python run_scheduler.py --mode rank --interval 12 --min-score 40

# Full auto-apply on a schedule (use with caution)
python run_scheduler.py --mode apply --limit 10 --interval 24
```

### Configuring alerts

Alerts use the existing `utils/alerts.py` system. Set up in `.env`:

```bash
# Slack alerts (easiest)
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...

# Email alerts
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your.email@gmail.com
SMTP_PASSWORD=your-app-password
ALERT_EMAIL=your.email@gmail.com
```

Then create saved searches via the API:

```bash
# Start the API server
uvicorn api.server:app --port 8000

# Create a saved search with alerts
curl -X POST http://localhost:8000/api/saved-searches \
  -H "Content-Type: application/json" \
  -d '{
    "name": "AI PM roles",
    "query": "product manager",
    "filters": {"remote": true},
    "alerts_enabled": true,
    "alert_channels": ["email", "slack"]
  }'
```

---

## 2. Smarter Search

### Relevance scoring

Every job gets a `job_score` (0-100) based on:

| Factor | Points | Example |
|--------|--------|---------|
| Title keyword match | +25 to +35 | "Senior Product Manager" = +30 |
| Tag match (AI/ML, GenAI, Platform) | +10 to +15 each (cap 30) | AI/ML tag = +15 |
| Seniority alignment | +15 | IC5 (Senior) = +15 |
| Remote | +5 | |
| Salary >= $150k | +5 | |
| Visa sponsorship detected | +10 | |
| Defense/clearance keywords | -40 to -50 | "defense", "Lockheed" |
| Junior/entry-level | -10 to -20 | |

### Scoring jobs

```bash
# Score all unscored jobs
curl -X POST http://localhost:8000/api/resume/match

# Or via Python directly
python -c "
import asyncio
from utils.resume_matcher import run_batch_matching
asyncio.run(run_batch_matching(limit=500, force=False))
"
```

### Visa sponsorship detection

```bash
# Scan all job descriptions for visa info
curl -X POST http://localhost:8000/api/resume/visa-scan
```

This sets `visa_sponsorship` to `yes`, `no`, or `unknown` on each job by scanning the description text.

### Ranked search endpoint

```bash
# Get top jobs by score
curl http://localhost:8000/api/jobs/ranked?min_score=50&limit=20

# Filter by source and status
curl http://localhost:8000/api/jobs/ranked?source=greenhouse&status=new&min_score=30
```

---

## 3. Auto-Apply

### Direct DB mode (no API server needed)

The auto-apply now supports querying jobs directly from the database:

```bash
# Apply using direct DB access (recommended — no API server needed)
python auto_apply.py --profile profile.yaml --use-db --auto-only --limit 20

# Only apply to jobs scoring above 50
python auto_apply.py --profile profile.yaml --use-db --auto-only --min-score 50 --limit 20

# Dry run (fill forms, take screenshots, don't submit)
python auto_apply.py --profile profile.yaml --use-db --dry-run --limit 5

# Run headless (for production/scheduled runs)
python auto_apply.py --profile profile.yaml --use-db --headless --auto-only --limit 50
```

### Via the pipeline

```bash
# Dry-run apply (fills forms, takes screenshots, no submit)
python run_pipeline.py --mode dry-apply --profile profile.yaml

# Full auto-apply (submits applications)
python run_pipeline.py --mode apply --profile profile.yaml --limit 20
```

### Form scanning (find gaps in your profile)

Before bulk-applying, scan forms to find what custom questions you're missing:

```bash
python scan_forms.py --profile profile.yaml --status saved --limit 20
```

This generates a report showing:
- Which required fields your profile can answer
- Which required fields are missing (with suggestions)
- A ready-to-paste `custom_answers` block for your `profile.yaml`

---

## Pipeline Modes Reference

| Mode | Crawl | Score | Auto-save | Auto-apply | Safe? |
|------|-------|-------|-----------|------------|-------|
| `crawl` | ✓ | ✓ | — | — | ✓ Always |
| `rank` | ✓ | ✓ | ✓ | — | ✓ Recommended |
| `dry-apply` | ✓ | ✓ | ✓ | Fill only | ✓ Safe |
| `apply` | ✓ | ✓ | ✓ | Submit | ⚠ Irreversible |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPELINE_MODE` | `rank` | Pipeline mode (crawl, rank, dry-apply, apply) |
| `PIPELINE_MIN_SCORE` | `30` | Minimum score to auto-mark as saved |
| `PIPELINE_APPLY_LIMIT` | `50` | Max applications per run |
| `PIPELINE_SOURCES` | (all) | Comma-separated sources to crawl |
| `CRAWL_INTERVAL_HOURS` | `6` | Hours between scheduled runs |
| `ANTHROPIC_API_KEY` | — | For LLM-powered website scraping |
| `SLACK_WEBHOOK_URL` | — | For Slack alerts |
| `SMTP_HOST` | — | For email alerts |

---

## Privacy Note

Your `profile.yaml` contains personal information (name, email, phone, resume path).
It is now in `.gitignore` and removed from git tracking.

**Important:** If you previously committed `profile.yaml` to a public GitHub repo,
your personal data is still in the git history. To scrub it:

```bash
# Option 1: Use git filter-repo (recommended)
pip install git-filter-repo
git filter-repo --invert-paths --path profile.yaml

git push origin --force --all

# Option 2: Use BFG Repo-Cleaner
bfg --delete-files profile.yaml
git reflog expire --expire=now --all
git gc --prune=now --aggressive

git push origin --force --all
```

After scrubbing, anyone who already cloned the repo still has your data locally.
Consider rotating any exposed credentials.
