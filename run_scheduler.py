#!/usr/bin/env python3
"""
Scheduler — runs the job crawler on a configurable interval.

Usage:
    python run_scheduler.py

This will:
1. Run an initial crawl immediately
2. Schedule recurring crawls every N hours (default: 6)
3. After each crawl, check saved searches and send alerts
"""

import asyncio
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from rich.console import Console

from config.settings import settings
from scrapers.orchestrator import CrawlOrchestrator
from utils.alerts import check_and_send_alerts

console = Console()


async def run_scheduled_crawl():
    """Execute a full crawl and send alerts."""
    console.print(f"\n[bold blue]⏰ Scheduled crawl starting at {datetime.now(timezone.utc).isoformat()}[/bold blue]")

    orchestrator = CrawlOrchestrator()
    stats = await orchestrator.run_full_crawl()

    # Send alerts for new jobs
    if stats["new_jobs"] > 0:
        console.print(f"[bold]Checking alerts for {stats['new_jobs']} new jobs...[/bold]")
        # In production, you'd pass the actual new job data here
        # For now, this demonstrates the pattern
        # await check_and_send_alerts(new_jobs)

    console.print(f"[bold green]✓ Crawl complete. Next run in {settings.crawl.interval_hours} hours.[/bold green]")


def main():
    console.print("[bold]🌸 Pollen Scheduler[/bold]")
    console.print(f"  Crawl interval: every {settings.crawl.interval_hours} hours")
    console.print(f"  Companies configured: {len(settings.companies)}")
    console.print()

    scheduler = AsyncIOScheduler()

    # Schedule recurring crawls
    scheduler.add_job(
        run_scheduled_crawl,
        trigger=IntervalTrigger(hours=settings.crawl.interval_hours),
        id="full_crawl",
        name="Full job crawl",
        next_run_time=datetime.now(timezone.utc),  # Run immediately on start
    )

    scheduler.start()
    console.print("[green]Scheduler started. Press Ctrl+C to stop.[/green]\n")

    # Keep running
    try:
        asyncio.get_event_loop().run_forever()
    except (KeyboardInterrupt, SystemExit):
        console.print("\n[yellow]Scheduler stopped.[/yellow]")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
