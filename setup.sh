#!/usr/bin/env bash
# ──────────────────────────────────────────────────
# 🌸 Pollen Job Crawler — One-Click Setup
# ──────────────────────────────────────────────────
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
# ──────────────────────────────────────────────────

set -e

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m' # No Color

echo ""
echo -e "${BOLD}🌸 Pollen Job Crawler — Setup${NC}"
echo "──────────────────────────────────────"
echo ""

# ── Step 1: Check Python ──
echo -e "${CYAN}▸ Checking Python...${NC}"
if command -v python3 &> /dev/null; then
    PY=$(python3 --version)
    echo -e "  ${GREEN}✓${NC} $PY"
else
    echo -e "  ${RED}✗ Python 3 not found.${NC}"
    echo "  Install it from https://python.org or via your package manager."
    exit 1
fi

# ── Step 2: Create virtual environment ──
echo ""
echo -e "${CYAN}▸ Creating virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "  ${GREEN}✓${NC} Created venv/"
else
    echo -e "  ${DIM}Already exists, skipping${NC}"
fi

# Activate it
source venv/bin/activate
echo -e "  ${GREEN}✓${NC} Activated virtual environment"

# ── Step 3: Install Python dependencies ──
echo ""
echo -e "${CYAN}▸ Installing Python dependencies...${NC}"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "  ${GREEN}✓${NC} All packages installed"

# ── Step 4: Install Playwright browser ──
echo ""
echo -e "${CYAN}▸ Installing Playwright Chromium browser...${NC}"
playwright install chromium 2>/dev/null || {
    echo -e "  ${YELLOW}⚠ Playwright browser install failed.${NC}"
    echo "  Company website scraping won't work, but ATS scrapers will."
    echo "  To fix: pip install playwright && playwright install chromium"
}
echo -e "  ${GREEN}✓${NC} Playwright ready"

# ── Step 5: Set up environment variables ──
echo ""
echo -e "${CYAN}▸ Setting up environment...${NC}"
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "  ${GREEN}✓${NC} Created .env from template"
    echo ""
    echo -e "  ${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "  ${YELLOW}IMPORTANT: Edit .env to add your API keys:${NC}"
    echo ""
    echo -e "  ${BOLD}nano .env${NC}  ${DIM}(or use your preferred editor)${NC}"
    echo ""
    echo -e "  Required for company website scraping:"
    echo -e "    ${BOLD}ANTHROPIC_API_KEY=sk-ant-...${NC}"
    echo ""
    echo -e "  Optional (for alerts):"
    echo -e "    SLACK_WEBHOOK_URL=https://hooks.slack.com/..."
    echo -e "  ${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
else
    echo -e "  ${DIM}.env already exists, skipping${NC}"
fi

# ── Step 6: Set up config ──
if [ ! -f "config/settings.yaml" ]; then
    cp config/settings.example.yaml config/settings.yaml
    echo -e "  ${GREEN}✓${NC} Created config/settings.yaml"
    echo -e "  ${DIM}  Edit this to add/remove target companies${NC}"
fi

# ── Step 7: Initialize database ──
echo ""
echo -e "${CYAN}▸ Initializing database...${NC}"
python3 -c "
import asyncio
from db.session import init_db
asyncio.run(init_db())
"
echo -e "  ${GREEN}✓${NC} SQLite database created (jobs.db)"

# ── Done! ──
echo ""
echo "──────────────────────────────────────"
echo -e "${GREEN}${BOLD}✓ Setup complete!${NC}"
echo "──────────────────────────────────────"
echo ""
echo -e "${BOLD}What to do next:${NC}"
echo ""
echo -e "  ${BOLD}1. Edit your API keys:${NC}"
echo "     nano .env"
echo ""
echo -e "  ${BOLD}2. Test with a dry run (no database writes):${NC}"
echo "     source venv/bin/activate"
echo "     python run_crawl.py --source greenhouse --dry-run"
echo ""
echo -e "  ${BOLD}3. Run a real crawl:${NC}"
echo "     python run_crawl.py --source greenhouse"
echo ""
echo -e "  ${BOLD}4. Start the API server (for the dashboard):${NC}"
echo "     uvicorn api.server:app --reload --port 8000"
echo "     # Then open http://localhost:8000/api/docs"
echo ""
echo -e "  ${BOLD}5. Run the scheduler (auto-crawl every 6 hours):${NC}"
echo "     python run_scheduler.py"
echo ""
echo -e "${DIM}Tip: Start with Greenhouse — it's the most reliable.${NC}"
echo -e "${DIM}     Add your ANTHROPIC_API_KEY to also scrape company websites.${NC}"
echo ""
