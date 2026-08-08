"""
Wellfound (formerly AngelList Talent) Scraper.

Scrapes job listings from wellfound.com using Playwright.
Catches startup PM roles that are often not on LinkedIn.
"""

import asyncio
from typing import Optional
from rich.console import Console

from scrapers.base import BaseScraper, JobData

console = Console()


class WellfoundScraper(BaseScraper):
    """Scrapes Wellfound (AngelList) for PM roles."""

    source_name = "wellfound"

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
        """Scrape Wellfound for PM/AI/Data roles."""
        jobs = []

        search_urls = [
            "https://wellfound.com/jobs?role=product_manager",
            "https://wellfound.com/jobs?role=product_manager&query=AI",
            "https://wellfound.com/jobs?role=product_manager&query=data",
            "https://wellfound.com/jobs?role=product_manager&query=technical",
        ]

        try:
            await self._ensure_browser()
            page = await self.browser.new_page()
            page.set_default_timeout(30000)

            for url in search_urls:
                try:
                    query_name = url.split("query=")[-1] if "query=" in url else "all"
                    console.print(f"  [dim]Fetching Wellfound: {query_name} PMs[/dim]")

                    await page.goto(url, wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(2)

                    # Scroll to load more
                    for _ in range(3):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(1)

                    # Get page content for LLM extraction
                    content = await page.inner_text("body")

                    if not content or len(content) < 200:
                        console.print(f"  [yellow]No content from Wellfound for {query_name}[/yellow]")
                        continue

                    console.print(f"  [dim]Extracted {len(content)} chars from Wellfound[/dim]")

                    # Use LLM to extract structured job data
                    extracted = await self._extract_with_llm(content[:80000], url)

                    for raw in extracted:
                        job = self._build_job(raw)
                        if job:
                            jobs.append(job)

                except Exception as e:
                    console.print(f"  [yellow]Wellfound error for {url}: {e}[/yellow]")

            await page.close()

        except Exception as e:
            console.print(f"  [red]Wellfound scraper error: {e}[/red]")

        # Deduplicate by URL
        seen = set()
        unique = []
        for job in jobs:
            key = f"{job.company_name}:{job.title}"
            if key not in seen:
                seen.add(key)
                unique.append(job)

        console.print(f"  [green]✓ Wellfound: {len(unique)} roles found[/green]")
        return unique

    async def _extract_with_llm(self, content: str, source_url: str) -> list[dict]:
        """Use Claude to extract jobs from page text."""
        try:
            import anthropic
            from config.settings import settings

            api_key = settings.llm.api_key
            if not api_key:
                console.print("  [red]No Anthropic API key for LLM extraction[/red]")
                return []

            client = anthropic.Anthropic(api_key=api_key)

            prompt = f"""Extract job listings from this Wellfound (AngelList) page.

PAGE CONTENT:
{content[:60000]}

Return a JSON array of product management roles. Each object should have:
- "title": job title
- "company": company name
- "url": job URL if visible (or null)
- "location": location
- "remote": true/false
- "salary_range": salary if mentioned (or null)
- "stage": company stage if shown (e.g., "Series A", "Seed")

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

            result = json.loads(text)
            console.print(f"  [dim]LLM found {len(result)} roles[/dim]")
            return result

        except Exception as e:
            console.print(f"  [yellow]LLM extraction error: {e}[/yellow]")
            return []

    def _build_job(self, raw: dict) -> Optional[JobData]:
        """Build a JobData from LLM-extracted dict."""
        try:
            title = raw.get("title") or ""
            if not title or not self.matches_role_filter(title):
                return None

            url = raw.get("url") or ""
            if url and not url.startswith("http"):
                url = f"https://wellfound.com{url}"

            # Parse salary
            salary_min, salary_max = None, None
            salary_str = raw.get("salary_range") or ""
            if salary_str:
                import re
                nums = re.findall(r'[\d,]+', salary_str.replace('k', '000').replace('K', '000'))
                if len(nums) >= 2:
                    salary_min = int(nums[0].replace(',', ''))
                    salary_max = int(nums[1].replace(',', ''))
                elif len(nums) == 1:
                    salary_min = int(nums[0].replace(',', ''))

            company = raw.get("company") or "Startup"
            stage = raw.get("stage") or ""
            tags = [stage] if stage else []

            return JobData(
                title=title,
                company_name=company,
                url=url or "https://wellfound.com/jobs",
                source=self.source_name,
                location=raw.get("location") or "",
                remote=bool(raw.get("remote", False)),
                description="",
                salary_min=salary_min,
                salary_max=salary_max,
                tags=tags,
                company_careers_url="https://wellfound.com/jobs",
            )
        except Exception:
            return None

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
