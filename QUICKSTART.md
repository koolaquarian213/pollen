# 🌸 Pollen — Quickstart Guide

There are two ways to run this: **Local (recommended to start)** or **Docker (for production)**.

---

## Option 1: Local Setup (Easiest to Start)

### Prerequisites
- **Python 3.10+** — [python.org/downloads](https://python.org/downloads)
- **An Anthropic API key** (only needed for company website scraping) — [console.anthropic.com](https://console.anthropic.com)

### Steps

```bash
# 1. Download all the files and navigate to the folder
cd job-crawler

# 2. Run the setup script (does everything automatically)
chmod +x setup.sh
./setup.sh

# 3. Add your API key
nano .env
# → Set ANTHROPIC_API_KEY=sk-ant-your-key-here

# 4. Activate the virtual environment
source venv/bin/activate

# 5. Test with a dry run (scrapes but doesn't save — safe to try)
python run_crawl.py --source greenhouse --dry-run

# 6. Run a real crawl (saves to local SQLite database)
python run_crawl.py --source greenhouse

# 7. Start the API server
uvicorn api.server:app --reload --port 8000

# 8. Open the API docs in your browser
#    → http://localhost:8000/api/docs
```

That's it! The API is now serving your scraped jobs.

### Day-to-day usage

```bash
# Always activate the venv first
cd job-crawler
source venv/bin/activate

# Manual crawl (all sources)
python run_crawl.py

# Crawl only specific sources
python run_crawl.py --source greenhouse
python run_crawl.py --source lever
python run_crawl.py --source job_boards
python run_crawl.py --source company-websites --limit 3

# Auto-crawl every 6 hours + alerts
python run_scheduler.py

# API server (run in a separate terminal)
uvicorn api.server:app --reload --port 8000
```

### Connecting the React Dashboard

The dashboard you received earlier (job-dashboard.jsx) uses mock data.
To connect it to real data, replace the mock data fetch with:

```javascript
// In your React app, replace INITIAL_JOBS with:
const [jobs, setJobs] = useState([]);

useEffect(() => {
  fetch("http://localhost:8000/api/jobs?limit=100")
    .then(r => r.json())
    .then(data => setJobs(data.jobs));
}, []);
```

---

## Option 2: Docker (Production Setup)

### Prerequisites
- **Docker** & **Docker Compose** — [docs.docker.com/get-docker](https://docs.docker.com/get-docker/)

### Steps

```bash
# 1. Navigate to the folder
cd job-crawler

# 2. Create your .env file
cp .env.example .env
nano .env
# → Set ANTHROPIC_API_KEY=sk-ant-your-key-here
# → Optionally set SLACK_WEBHOOK_URL for alerts

# 3. Start everything (PostgreSQL + API + Scheduler)
docker compose up -d

# 4. Check it's running
docker compose ps
docker compose logs -f api        # Watch API logs
docker compose logs -f scheduler  # Watch crawler logs

# 5. Open the API docs
#    → http://localhost:8000/api/docs
```

### Docker gives you 3 services:

| Service     | What it does                          | Port |
|-------------|---------------------------------------|------|
| `db`        | PostgreSQL database                   | 5432 |
| `api`       | FastAPI REST server                   | 8000 |
| `scheduler` | Runs crawls every 6 hours + alerts    | —    |

### Useful Docker commands

```bash
# View logs
docker compose logs -f

# Trigger a manual crawl
curl -X POST http://localhost:8000/api/crawl/trigger \
  -H "Content-Type: application/json" \
  -d '{"sources": ["greenhouse"]}'

# Stop everything
docker compose down

# Stop and delete all data
docker compose down -v

# Rebuild after code changes
docker compose up -d --build
```

---

## What Each Scraper Needs

| Scraper | Needs API Key? | Needs Playwright? | Reliability |
|---------|---------------|-------------------|-------------|
| Greenhouse | No | No | ⭐⭐⭐⭐⭐ Best |
| Lever | No | No | ⭐⭐⭐⭐⭐ |
| Ashby | No | No | ⭐⭐⭐⭐ |
| Job Boards (LinkedIn etc.) | No | No | ⭐⭐⭐ Rate-limited |
| Company Websites | **Yes** (Anthropic) | **Yes** | ⭐⭐⭐⭐ Flexible |

**Recommended order to try:**
1. Start with `--source greenhouse` (free, reliable, 15+ companies pre-configured)
2. Add `--source lever` (also free and reliable)
3. Try `--source job_boards` (broader sweep, may be slow)
4. Add your API key and try `--source company-websites` (most powerful)

---

## Adding New Companies

Edit `config/settings.yaml`:

```yaml
companies:
  # If the company uses Greenhouse:
  - name: Shopify
    careers_url: https://shopify.com/careers
    ats: greenhouse
    ats_board_id: shopify  # ← this is the slug in their Greenhouse URL

  # If the company uses Lever:
  - name: Dropbox
    careers_url: https://dropbox.com/jobs
    ats: lever
    ats_board_id: dropbox

  # If you don't know their ATS (uses Playwright + LLM):
  - name: Tesla
    careers_url: https://www.tesla.com/careers/search/?query=design
```

### How to find a company's ATS board ID:
1. Go to their careers page
2. Click on any job listing
3. Look at the URL:
   - `boards.greenhouse.io/stripe/jobs/...` → ATS: greenhouse, board_id: `stripe`
   - `jobs.lever.co/spotify/...` → ATS: lever, board_id: `spotify`
   - `jobs.ashbyhq.com/ramp/...` → ATS: ashby, board_id: `ramp`
   - If the URL is their own domain → leave `ats` blank (uses website scraper)

---

## API Endpoints Quick Reference

Once the server is running, open **http://localhost:8000/api/docs** for the interactive Swagger UI.

```
GET  /api/jobs?q=designer&remote=true&limit=50  — Search jobs
GET  /api/jobs/42                                 — Single job
POST /api/jobs/42/status     {"status":"applied"} — Update status
GET  /api/stats                                   — Analytics data
POST /api/crawl/trigger      {"sources":["greenhouse"]} — Manual crawl
GET  /api/saved-searches                          — List saved searches
POST /api/saved-searches     {...}                — Create saved search
GET  /api/health                                  — Health check
```

---

## Troubleshooting

**"No module named X"** → Make sure your venv is activated: `source venv/bin/activate`

**Playwright fails** → Run `playwright install chromium` — it needs to download a browser binary (~150MB)

**Greenhouse returns empty** → The board_id might be wrong. Try visiting `https://boards-api.greenhouse.io/v1/boards/BOARD_ID/jobs` in your browser.

**Rate limited on job boards** → This is normal. Increase `request_delay` in `config/settings.yaml` or use fewer queries.

**Company website scraper returns nothing** → Check that your `ANTHROPIC_API_KEY` is set in `.env`. The LLM is needed to parse arbitrary HTML.
