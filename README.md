# Pollen — Job Crawler & Search Engine

A production-ready job crawling system that automatically discovers Product & Design roles across job boards, ATS platforms, and company career pages, then serves results via a REST API for your dashboard.

## Architecture

```
┌──────────────────────────────────────────────────┐
│                  Scheduler (APScheduler)          │
│         Runs crawl jobs every 6 hours             │
└────────┬──────────┬──────────┬───────────────────┘
         │          │          │
    ┌────▼───┐ ┌────▼───┐ ┌───▼─────────────────┐
    │ Board  │ │  ATS   │ │  Company Website     │
    │Scrapers│ │Scrapers│ │  Scrapers (LLM)      │
    │        │ │        │ │                       │
    │LinkedIn│ │Greenhouse│ │ Playwright + Claude  │
    │Indeed  │ │Lever    │ │ for arbitrary pages  │
    │Glassdoor│ │Ashby   │ │                      │
    └────┬───┘ └────┬───┘ └───┬─────────────────┘
         │          │          │
    ┌────▼──────────▼──────────▼───┐
    │      Deduplication Engine     │
    │   (fuzzy match + hashing)     │
    └────────────┬─────────────────┘
                 │
    ┌────────────▼─────────────────┐
    │      PostgreSQL / SQLite      │
    │   Full-text search + FTS5     │
    └────────────┬─────────────────┘
                 │
    ┌────────────▼─────────────────┐
    │      FastAPI REST Server      │
    │   /jobs, /search, /stats      │
    └────────────┬─────────────────┘
                 │
    ┌────────────▼─────────────────┐
    │     React Dashboard (Pollen)  │
    └──────────────────────────────┘
```

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure
```bash
cp config/settings.example.yaml config/settings.yaml
# Edit settings.yaml with your API keys and company list
```

### 3. Initialize database
```bash
python -m db.init_db
```

### 4. Run a test crawl
```bash
python run_crawl.py --source greenhouse --dry-run
python run_crawl.py --source company-websites --limit 5
```

### 5. Start the API server
```bash
uvicorn api.server:app --reload --port 8000
```

### 6. Start the scheduler (production)
```bash
python run_scheduler.py
```

## Scrapers

| Source | Method | Auth Required | Notes |
|--------|--------|---------------|-------|
| Greenhouse | JSON API | No | Most reliable, structured data |
| Lever | JSON API | No | Good structured data |
| Ashby | JSON API | No | Growing ATS platform |
| Workday | HTML scraping | No | Complex, JS-rendered |
| LinkedIn | JobSpy library | No | Rate-limited |
| Indeed | JobSpy library | No | Rate-limited |
| Company Websites | Playwright + LLM | API key for LLM | Handles arbitrary HTML |

## API Endpoints

```
GET  /api/jobs              — list jobs with filters
GET  /api/jobs/{id}         — single job detail
GET  /api/jobs/search       — full-text search
GET  /api/stats             — dashboard analytics
GET  /api/sources           — crawl source status
POST /api/jobs/{id}/status  — update application status
POST /api/crawl/trigger     — manually trigger a crawl
```

## Environment Variables

```
DATABASE_URL=sqlite:///jobs.db          # or postgresql://...
ANTHROPIC_API_KEY=sk-ant-...            # for LLM-powered scraping
SLACK_WEBHOOK_URL=https://hooks...      # optional alerts
SMTP_HOST=smtp.gmail.com                # optional email alerts
```
