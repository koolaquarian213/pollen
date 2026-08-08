"""
Company Website Scraper — Playwright + Claude LLM.

This is the most powerful scraper. It can handle ANY company career page,
including JavaScript-rendered SPAs, by:

1. Using Playwright to render the page in a real browser
2. Extracting the visible text/HTML
3. Sending it to Claude to extract structured job listings

This handles the long tail of companies that don't use a standard ATS,
or whose ATS board IDs we don't know.
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console

from config.settings import CompanySource, settings
from scrapers.base import BaseScraper, JobData

console = Console()

EXTRACTION_PROMPT = """You are a job listing extractor. Given the HTML content of a company's career page, extract all job listings that match Product/Design roles.

Look for roles related to: product design, UX design, UI design, interaction design, visual design, brand design, design systems, UX research, content design, UX writing, design engineering, product management, design leadership.

For each matching job, extract the following fields in JSON format:
- title: exact job title
- url: the application URL or link to the job detail page (if relative, I'll provide the base URL)
- location: city, state/country
- remote: true/false
- department: team or department name
- salary_range: if mentioned (e.g., "$150k - $200k")

Return ONLY a JSON array of objects. If no matching jobs are found, return an empty array [].
Do not include any explanation, markdown, or text outside the JSON array.

Base URL for relative links: {base_url}

Here is the career page content:

{content}"""

DETAIL_EXTRACTION_PROMPT = """Extract job details from this job posting page. Return a JSON object with:
- description: a clean text summary of the role (max 500 words)
- salary_min: minimum salary as integer (e.g., 150000), or null
- salary_max: maximum salary as integer, or null
- level: seniority level (Junior, Mid, Senior, Staff, Lead, Principal, Manager, Director, VP), or null
- employment_type: full-time, part-time, contract, or null
- tags: array of relevant tags from this list: Design Systems, Mobile, B2B, B2C, AI/ML, E-commerce, Growth, Platform, 0→1, Accessibility, Enterprise

Return ONLY the JSON object, no other text.

Page content:
{content}"""


class CompanyWebsiteScraper(BaseScraper):
    """
    Scrapes arbitrary company career pages using Playwright + LLM extraction.
    Falls back to httpx + BeautifulSoup for simpler pages.
    """
    source_name = "company_website"

    def __init__(self):
        super().__init__()
        self._browser = None
        self._playwright = None

    async def _ensure_browser(self):
        """Lazily initialize Playwright browser."""
        if self._browser is None:
            try:
                from playwright.async_api import async_playwright
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            except Exception as e:
                console.print(f"  [yellow]Playwright not available: {e}[/yellow]")
                console.print(f"  [yellow]Falling back to httpx[/yellow]")

    async def scrape(self, company: CompanySource) -> list[JobData]:
        """
        Scrape a company's career page:
        1. Fetch the page (Playwright for JS, httpx for simple HTML)
        2. Extract text content
        3. Send to LLM for structured extraction
        4. Optionally fetch individual job pages for details
        """
        if not settings.llm.api_key:
            console.print(
                "  [red]ANTHROPIC_API_KEY not set — cannot use LLM extraction. "
                "Set it in .env or environment.[/red]"
            )
            return []

        jobs = []

        try:
            # Step 1: Fetch the career page
            content = await self._fetch_page_content(company.careers_url)
            if not content:
                return []

            # Truncate to fit in context window (~100k chars ≈ ~25k tokens)
            content = content[:100000]

            console.print(f"  [dim]Extracted {len(content)} chars from {company.careers_url}[/dim]")

            # Step 2: LLM extraction
            raw_jobs = await self._extract_with_llm(content, company.careers_url)

            if not raw_jobs:
                console.print(f"  [yellow]No matching roles found on {company.name} career page[/yellow]")
                return []

            console.print(f"  [dim]LLM found {len(raw_jobs)} matching roles[/dim]")

            # Step 3: Build JobData objects
            for raw in raw_jobs:
                # Resolve relative URLs — guard against None
                url = raw.get("url") or ""
                if url and not url.startswith("http"):
                    from urllib.parse import urljoin
                    url = urljoin(company.careers_url, url)

                # Parse salary
                salary_min, salary_max = None, None
                salary_str = raw.get("salary_range") or ""
                if salary_str:
                    salary_min, salary_max = self._parse_salary_string(salary_str)

                job = JobData(
                    title=raw.get("title") or "Unknown Role",
                    company_name=company.name,
                    url=url or company.careers_url,
                    source=self.source_name,
                    location=raw.get("location") or "",
                    remote=bool(raw.get("remote", False)),
                    description="",
                    salary_min=salary_min,
                    salary_max=salary_max,
                    department=raw.get("department") or "",
                    company_careers_url=company.careers_url,
                )
                jobs.append(job)

            # Step 4: Optionally fetch detail pages for richer data
            # (Do this for the first N jobs to avoid too many requests)
            detail_limit = min(len(jobs), 10)
            for i in range(detail_limit):
                job = jobs[i]
                if job.url and job.url != company.careers_url:
                    try:
                        details = await self._fetch_job_details(job.url)
                        if details:
                            if details.get("description"):
                                job.description = details["description"]
                            if details.get("salary_min"):
                                job.salary_min = details["salary_min"]
                            if details.get("salary_max"):
                                job.salary_max = details["salary_max"]
                            if details.get("level"):
                                job.level = details["level"]
                            if details.get("tags"):
                                job.tags = details["tags"]
                    except Exception as e:
                        console.print(f"  [dim]Could not fetch details for {job.title}: {e}[/dim]")

            console.print(f"  [green]✓ {company.name}: {len(jobs)} roles via website scraping[/green]")

        except Exception as e:
            console.print(f"  [red]✗ {company.name} website scraper error: {e}[/red]")

        return jobs

    async def _fetch_page_content(self, url: str) -> str:
        """
        Fetch page content. Uses Playwright for JS-rendered pages,
        falls back to httpx + BeautifulSoup.
        """
        # Try Playwright first for full JS rendering
        await self._ensure_browser()

        if self._browser:
            try:
                page = await self._browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                # Wait a bit for any lazy-loaded content
                await page.wait_for_timeout(2000)

                # Scroll down to trigger lazy loading
                await page.evaluate("""
                    async () => {
                        for (let i = 0; i < 5; i++) {
                            window.scrollTo(0, document.body.scrollHeight * (i + 1) / 5);
                            await new Promise(r => setTimeout(r, 500));
                        }
                        window.scrollTo(0, 0);
                    }
                """)

                content = await page.content()
                await page.close()

                # Clean HTML to text
                return self._html_to_text(content)

            except Exception as e:
                console.print(f"  [yellow]Playwright failed for {url}: {e}[/yellow]")

        # Fallback to httpx
        try:
            response = await self.fetch(url)
            return self._html_to_text(response.text)
        except Exception as e:
            console.print(f"  [red]Could not fetch {url}: {e}[/red]")
            return ""

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to readable text while preserving structure."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")

            # Remove script and style elements
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            # Get text with some structure preserved
            text = soup.get_text(separator="\n")

            # Clean up whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines)

        except ImportError:
            # Regex fallback
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", "\n", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text

    async def _extract_with_llm(self, content: str, base_url: str) -> list[dict]:
        """Send page content to Claude for structured extraction."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=settings.llm.api_key)

            prompt = EXTRACTION_PROMPT.format(base_url=base_url, content=content[:80000])

            response = client.messages.create(
                model=settings.llm.model,
                max_tokens=settings.llm.max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()

            # Parse JSON — handle potential markdown wrapping
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)

            result = json.loads(text)
            if isinstance(result, list):
                return result
            return []

        except json.JSONDecodeError as e:
            console.print(f"  [yellow]LLM returned invalid JSON: {e}[/yellow]")
            return []
        except Exception as e:
            console.print(f"  [red]LLM extraction error: {e}[/red]")
            return []

    async def _fetch_job_details(self, url: str) -> Optional[dict]:
        """Fetch a single job page and extract details with LLM."""
        content = await self._fetch_page_content(url)
        if not content or len(content) < 100:
            return None

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=settings.llm.api_key)

            prompt = DETAIL_EXTRACTION_PROMPT.format(content=content[:40000])

            response = client.messages.create(
                model=settings.llm.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text)
                text = re.sub(r"\s*```$", "", text)

            return json.loads(text)

        except Exception as e:
            console.print(f"  [dim]Detail extraction failed: {e}[/dim]")
            return None

    def _parse_salary_string(self, s: str) -> tuple[Optional[int], Optional[int]]:
        """Parse salary strings like '$150k-$200k' or '$150,000 - $200,000'."""
        patterns = [
            r"\$(\d{1,3}(?:,\d{3})*)\s*[-–—to]+\s*\$(\d{1,3}(?:,\d{3})*)",
            r"\$(\d{2,3})k\s*[-–—to]+\s*\$(\d{2,3})k",
        ]
        for pattern in patterns:
            match = re.search(pattern, s, re.IGNORECASE)
            if match:
                low = int(match.group(1).replace(",", ""))
                high = int(match.group(2).replace(",", ""))
                if low < 1000:
                    low *= 1000
                if high < 1000:
                    high *= 1000
                return low, high
        return None, None

    async def close(self):
        """Clean up Playwright resources."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        await super().close()
