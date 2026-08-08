"""
Job Board Aggregator Scraper using python-jobspy.

Scrapes LinkedIn, Indeed, Glassdoor, and ZipRecruiter in a single call.
This is useful as a broad sweep in addition to targeted ATS scrapers.

Note: These are rate-limited and may require proxies at scale.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from rich.console import Console

from config.settings import CompanySource, settings
from scrapers.base import BaseScraper, JobData

console = Console()


class JobBoardScraper(BaseScraper):
    """
    Uses python-jobspy to scrape major job boards.
    Runs synchronously (jobspy is sync), wrapped in asyncio.to_thread.
    """
    source_name = "job_boards"

    async def scrape(self, company: CompanySource = None) -> list[JobData]:
        """
        Scrape job boards for Product/Design roles.
        `company` is ignored — this scrapes by keyword across all boards.
        """
        return await asyncio.to_thread(self._scrape_sync)

    def _scrape_sync(self) -> list[JobData]:
        """Synchronous scraping via jobspy."""
        try:
            from jobspy import scrape_jobs
        except ImportError:
            console.print("[red]python-jobspy not installed. Run: pip install python-jobspy[/red]")
            return []

        jobs = []

        # Search queries tailored for AI/Tech/Data PM roles
        queries = [
            "AI product manager",
            "technical product manager",
            "data product manager",
            "ML product manager",
            "platform product manager",
            "senior product manager AI",
            "group product manager data",
            "product manager machine learning",
        ]

        for query in queries:
            console.print(f"  [dim]Searching job boards for '{query}'...[/dim]")
            try:
                results = scrape_jobs(
                    site_name=["linkedin", "indeed", "glassdoor", "zip_recruiter"],
                    search_term=query,
                    location="United States",
                    results_wanted=25,
                    hours_old=168,  # last 7 days
                    country_indeed="USA",
                )

                if results is None or results.empty:
                    continue

                for _, row in results.iterrows():
                    title = str(row.get("title", ""))

                    if not self.matches_role_filter(title):
                        continue

                    # Determine source
                    site = str(row.get("site", "unknown")).lower()
                    source_map = {
                        "linkedin": "LinkedIn",
                        "indeed": "Indeed",
                        "glassdoor": "Glassdoor",
                        "zip_recruiter": "ZipRecruiter",
                    }
                    source = source_map.get(site, site)

                    # Parse salary
                    salary_min = None
                    salary_max = None
                    try:
                        sal_min = row.get("min_amount")
                        sal_max = row.get("max_amount")
                        if sal_min and float(sal_min) > 0:
                            salary_min = int(float(sal_min))
                        if sal_max and float(sal_max) > 0:
                            salary_max = int(float(sal_max))
                    except (ValueError, TypeError):
                        pass

                    # Parse date
                    date_posted = None
                    date_str = row.get("date_posted")
                    if date_str:
                        try:
                            date_posted = datetime.fromisoformat(str(date_str))
                            if date_posted.tzinfo is None:
                                date_posted = date_posted.replace(tzinfo=timezone.utc)
                        except (ValueError, TypeError):
                            pass

                    location = str(row.get("location", ""))
                    remote = bool(row.get("is_remote", False))

                    job = JobData(
                        title=title,
                        company_name=str(row.get("company_name", "Unknown")),
                        url=str(row.get("job_url", "")),
                        source=source,
                        location=location,
                        remote=remote,
                        description=str(row.get("description", ""))[:5000],
                        salary_min=salary_min,
                        salary_max=salary_max,
                        source_id=str(row.get("id", "")),
                        date_posted=date_posted,
                    )
                    jobs.append(job)

                # Rate limit between queries
                import time
                time.sleep(3)

            except Exception as e:
                console.print(f"  [yellow]Warning scraping '{query}': {e}[/yellow]")
                continue

        # Deduplicate within this batch by URL
        seen_urls = set()
        unique_jobs = []
        for j in jobs:
            if j.url not in seen_urls:
                seen_urls.add(j.url)
                unique_jobs.append(j)

        console.print(f"  [green]✓ Job boards: {len(unique_jobs)} unique matching roles[/green]")
        return unique_jobs
