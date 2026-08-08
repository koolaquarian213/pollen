"""
Crawl Orchestrator.

Coordinates all scrapers, handles deduplication, saves to DB,
and sends alerts for new jobs.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.table import Table

from config.settings import settings, CompanySource
from db.session import async_session, init_db
from db.operations import upsert_job, log_crawl, finish_crawl_log
from scrapers.greenhouse import GreenhouseScraper
from scrapers.lever import LeverScraper
from scrapers.ashby import AshbyScraper
from scrapers.job_boards import JobBoardScraper
from scrapers.company_website import CompanyWebsiteScraper
from scrapers.yc_startups import YCStartupScraper
from scrapers.wellfound import WellfoundScraper
from scrapers.base import JobData

console = Console()


# Map ATS names to scraper classes
ATS_SCRAPERS = {
    "greenhouse": GreenhouseScraper,
    "lever": LeverScraper,
    "ashby": AshbyScraper,
}


class CrawlOrchestrator:
    """Runs all configured scrapers and saves results."""

    def __init__(self):
        self.scrapers = {}
        self.website_scraper = CompanyWebsiteScraper()
        self.board_scraper = JobBoardScraper()
        self.yc_scraper = YCStartupScraper()
        self.wellfound_scraper = WellfoundScraper()
        self.stats = {
            "total_found": 0,
            "new_jobs": 0,
            "updated_jobs": 0,
            "errors": 0,
        }

    def _get_scraper(self, ats: str):
        """Get or create a scraper instance for an ATS type."""
        if ats not in self.scrapers:
            scraper_cls = ATS_SCRAPERS.get(ats)
            if scraper_cls:
                self.scrapers[ats] = scraper_cls()
        return self.scrapers.get(ats)

    async def run_full_crawl(
        self,
        sources: Optional[list[str]] = None,
        limit: Optional[int] = None,
        skip: int = 0,
        company_filter: Optional[list[str]] = None,
        dry_run: bool = False,
    ):
        """
        Run a complete crawl across all configured sources.

        Args:
            sources: Filter to specific sources (e.g., ["greenhouse", "lever"])
            limit: Max companies to crawl (useful for testing)
            skip: Skip the first N companies (for batch crawling)
            company_filter: Only crawl these specific company names
            dry_run: If True, scrape but don't save to DB
        """
        console.print("\n[bold]🌸 Pollen Crawler — Starting full crawl[/bold]\n")

        await init_db()

        companies = settings.companies

        # Filter to specific companies by name
        if company_filter:
            filter_lower = [c.lower() for c in company_filter]
            companies = [c for c in companies if c.name.lower() in filter_lower]
            console.print(f"[dim]Filtered to {len(companies)} companies: {', '.join(c.name for c in companies)}[/dim]\n")

        # Apply skip and limit
        if skip:
            companies = companies[skip:]
            console.print(f"[dim]Skipped first {skip} companies[/dim]")
        if limit:
            companies = companies[:limit]
            console.print(f"[dim]Limited to {limit} companies[/dim]")

        # Group companies by ATS type
        ats_groups: dict[str, list[CompanySource]] = {}
        website_companies: list[CompanySource] = []

        for company in companies:
            if company.ats:
                if sources and company.ats not in sources:
                    continue
                ats_groups.setdefault(company.ats, []).append(company)
            else:
                if sources and "company-websites" not in sources and "website" not in sources:
                    continue
                website_companies.append(company)

        all_jobs: list[JobData] = []

        # ── Phase 1: ATS Scrapers (structured, fast) ──
        for ats_name, ats_companies in ats_groups.items():
            scraper = self._get_scraper(ats_name)
            if not scraper:
                console.print(f"[yellow]No scraper for ATS: {ats_name}[/yellow]")
                continue

            console.print(f"\n[bold cyan]▸ Scraping {ats_name.upper()} ({len(ats_companies)} companies)[/bold cyan]")

            for company in ats_companies:
                try:
                    jobs = await scraper.scrape(company)
                    all_jobs.extend(jobs)
                except Exception as e:
                    console.print(f"  [red]Error scraping {company.name}: {e}[/red]")
                    self.stats["errors"] += 1

        # ── Phase 2: Job Board Aggregator ──
        if not sources or "job_boards" in sources or "boards" in sources:
            console.print(f"\n[bold cyan]▸ Scraping Job Boards (LinkedIn, Indeed, etc.)[/bold cyan]")
            try:
                board_jobs = await self.board_scraper.scrape()
                all_jobs.extend(board_jobs)
            except Exception as e:
                console.print(f"  [red]Job board error: {e}[/red]")
                self.stats["errors"] += 1

        # ── Phase 2b: YC Work at a Startup ──
        if not sources or "yc" in sources or "yc_startups" in sources:
            console.print(f"\n[bold cyan]▸ Scraping YC Work at a Startup[/bold cyan]")
            try:
                yc_jobs = await self.yc_scraper.scrape()
                all_jobs.extend(yc_jobs)
            except Exception as e:
                console.print(f"  [red]YC scraper error: {e}[/red]")
                self.stats["errors"] += 1

        # ── Phase 2c: Wellfound (AngelList) ──
        if not sources or "wellfound" in sources or "angellist" in sources:
            console.print(f"\n[bold cyan]▸ Scraping Wellfound (AngelList)[/bold cyan]")
            try:
                wf_jobs = await self.wellfound_scraper.scrape()
                all_jobs.extend(wf_jobs)
            except Exception as e:
                console.print(f"  [red]Wellfound scraper error: {e}[/red]")
                self.stats["errors"] += 1

        # ── Phase 3: Company Website Scrapers (LLM-powered, slower) ──
        if website_companies:
            console.print(
                f"\n[bold cyan]▸ Scraping Company Websites "
                f"({len(website_companies)} companies, LLM-powered)[/bold cyan]"
            )
            for company in website_companies:
                try:
                    jobs = await self.website_scraper.scrape(company)
                    all_jobs.extend(jobs)
                except Exception as e:
                    console.print(f"  [red]Error scraping {company.name}: {e}[/red]")
                    self.stats["errors"] += 1

        self.stats["total_found"] = len(all_jobs)
        console.print(f"\n[bold]Total jobs found: {len(all_jobs)}[/bold]")

        # ── Phase 4: Save to database ──
        if dry_run:
            console.print("[yellow]DRY RUN — not saving to database[/yellow]")
            self._print_results(all_jobs)
        else:
            await self._save_jobs(all_jobs)

        # ── Cleanup ──
        await self._cleanup()

        # ── Print summary ──
        self._print_summary()

        return self.stats

    async def _save_jobs(self, all_jobs: list[JobData]):
        """Save all crawled jobs to the database with dedup."""
        console.print(f"\n[bold]Saving to database...[/bold]")

        async with async_session() as session:
            crawl_log = await log_crawl(session, source="full_crawl")

            for job_data in all_jobs:
                try:
                    job, is_new = await upsert_job(session, job_data.to_dict())
                    if is_new:
                        self.stats["new_jobs"] += 1
                    else:
                        self.stats["updated_jobs"] += 1
                except Exception as e:
                    console.print(f"  [red]Error saving job: {e}[/red]")
                    self.stats["errors"] += 1

            await finish_crawl_log(
                session, crawl_log.id,
                status="success",
                jobs_found=self.stats["total_found"],
                jobs_new=self.stats["new_jobs"],
                jobs_updated=self.stats["updated_jobs"],
            )

        console.print(
            f"  [green]✓ Saved: {self.stats['new_jobs']} new, "
            f"{self.stats['updated_jobs']} updated[/green]"
        )

    async def run_single_source(self, source: str, company_name: Optional[str] = None):
        """Run crawler for a single source/company."""
        return await self.run_full_crawl(sources=[source])

    def _print_results(self, jobs: list[JobData]):
        """Print crawl results as a rich table."""
        table = Table(title="Crawled Jobs (Dry Run)")
        table.add_column("Company", style="cyan")
        table.add_column("Title", style="white")
        table.add_column("Location", style="dim")
        table.add_column("Remote", style="green")
        table.add_column("Source", style="yellow")
        table.add_column("Salary", style="magenta")

        for job in jobs[:50]:
            salary = ""
            if job.salary_min and job.salary_max:
                salary = f"${job.salary_min // 1000}k–${job.salary_max // 1000}k"
            table.add_row(
                job.company_name,
                job.title[:50],
                job.location[:30],
                "✓" if job.remote else "",
                job.source,
                salary,
            )

        console.print(table)
        if len(jobs) > 50:
            console.print(f"  ... and {len(jobs) - 50} more")

    def _print_summary(self):
        """Print crawl summary."""
        console.print("\n" + "─" * 50)
        console.print("[bold]Crawl Summary[/bold]")
        console.print(f"  Total found:  {self.stats['total_found']}")
        console.print(f"  New jobs:     [green]{self.stats['new_jobs']}[/green]")
        console.print(f"  Updated:      [blue]{self.stats['updated_jobs']}[/blue]")
        console.print(f"  Errors:       [red]{self.stats['errors']}[/red]")
        console.print("─" * 50 + "\n")

    async def _cleanup(self):
        """Close all scraper connections."""
        for scraper in self.scrapers.values():
            await scraper.close()
        await self.board_scraper.close()
        await self.website_scraper.close()
        await self.yc_scraper.close()
        await self.wellfound_scraper.close()
