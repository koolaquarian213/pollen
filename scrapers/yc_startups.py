"""
YC Work at a Startup Scraper.

Scrapes job listings from workatastartup.com using Playwright.
This catches jobs from YC-backed startups that often don't post on LinkedIn/Indeed.
"""

import asyncio
from typing import Optional
from rich.console import Console

from scrapers.base import BaseScraper, JobData

console = Console()


class YCStartupScraper(BaseScraper):
    """Scrapes YC Work at a Startup for PM roles."""

    source_name = "yc_startups"

    def __init__(self):
        super().__init__()
        self.browser = None
        self.playwright = None

    async def _ensure_browser(self):
        if not self.browser:
            from playwright.async_api import async_playwright
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=True)

    async def scrape(self, company=None) -> list[JobData]:
        """Scrape YC Work at a Startup for PM/AI/Data roles."""
        jobs = []

        search_queries = [
            "product manager",
            "AI product manager",
            "technical product manager",
            "data product manager",
        ]

        try:
            await self._ensure_browser()
            page = await self.browser.new_page()
            page.set_default_timeout(30000)

            for query in search_queries:
                try:
                    url = f"https://www.workatastartup.com/jobs?query={query.replace(' ', '+')}"
                    console.print(f"  [dim]Fetching YC jobs: {query}[/dim]")

                    await page.goto(url, wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(2)

                    # Scroll down to load more results
                    for _ in range(3):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(1)

                    # Extract job cards
                    job_elements = await page.query_selector_all("[class*='job'], [class*='listing'], a[href*='/jobs/']")

                    if not job_elements:
                        # Fallback: extract all text and use LLM
                        content = await page.inner_text("body")
                        if content and len(content) > 200:
                            extracted = await self._extract_with_llm(content[:80000], url)
                            for raw in extracted:
                                job = self._build_job(raw)
                                if job:
                                    jobs.append(job)
                        continue

                    # Try structured extraction first
                    for elem in job_elements[:50]:
                        try:
                            text = await elem.inner_text()
                            href = await elem.get_attribute("href")

                            if not text or len(text) < 10:
                                continue

                            # Parse from text
                            lines = [l.strip() for l in text.split("\n") if l.strip()]
                            if len(lines) < 2:
                                continue

                            title = lines[0]
                            company_name = lines[1] if len(lines) > 1 else ""
                            location = ""
                            remote = False

                            for line in lines:
                                ll = line.lower()
                                if any(kw in ll for kw in ["remote", "sf", "new york", "nyc", "san francisco", "los angeles", "seattle", "usa", "worldwide"]):
                                    location = line
                                if "remote" in ll:
                                    remote = True

                            if not self.matches_role_filter(title):
                                continue

                            job_url = href or ""
                            if job_url and not job_url.startswith("http"):
                                job_url = f"https://www.workatastartup.com{job_url}"

                            job = JobData(
                                title=title,
                                company_name=company_name or "YC Startup",
                                url=job_url or url,
                                source=self.source_name,
                                location=location,
                                remote=remote,
                                description=" ".join(lines),
                                company_careers_url=url,
                            )
                            jobs.append(job)
                        except Exception:
                            continue

                except Exception as e:
                    console.print(f"  [yellow]YC search error for '{query}': {e}[/yellow]")

            await page.close()

        except Exception as e:
            console.print(f"  [red]YC Startup scraper error: {e}[/red]")

        # Deduplicate by URL
        seen_urls = set()
        unique_jobs = []
        for job in jobs:
            if job.url not in seen_urls:
                seen_urls.add(job.url)
                unique_jobs.append(job)

        console.print(f"  [green]✓ YC Startups: {len(unique_jobs)} roles found[/green]")
        return unique_jobs

    async def _extract_with_llm(self, content: str, source_url: str) -> list[dict]:
        """Use Claude to extract jobs from page text."""
        try:
            import anthropic
            from config.settings import settings

            api_key = settings.llm.api_key
            if not api_key:
                return []

            client = anthropic.Anthropic(api_key=api_key)

            prompt = f"""Extract job listings from this YC Work at a Startup page.

PAGE CONTENT:
{content[:60000]}

Return a JSON array of matching product management roles. Each object should have:
- "title": job title
- "company": company name
- "url": job URL if visible (or null)
- "location": location
- "remote": true/false
- "salary_range": salary if mentioned (or null)

Only include roles matching: product manager, technical PM, AI PM, data PM, or similar.
Return ONLY the JSON array, no other text."""

            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            import json
            text = message.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)

        except Exception as e:
            console.print(f"  [yellow]LLM extraction error: {e}[/yellow]")
            return []

    def _build_job(self, raw: dict) -> Optional[JobData]:
        """Build a JobData from LLM-extracted dict."""
        try:
            title = raw.get("title") or ""
            if not title or not self.matches_role_filter(title):
                return None

            return JobData(
                title=title,
                company_name=raw.get("company") or "YC Startup",
                url=raw.get("url") or "https://www.workatastartup.com/jobs",
                source=self.source_name,
                location=raw.get("location") or "",
                remote=bool(raw.get("remote", False)),
                description="",
                company_careers_url="https://www.workatastartup.com/jobs",
            )
        except Exception:
            return None

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
