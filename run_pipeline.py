#!/usr/bin/env python3
"""
Pipeline — crawl -> score -> mark saved -> optionally auto-apply.

This is the main entry point for scheduled runs. It combines:
  1. Crawl all sources for new jobs
  2. Score jobs with relevance scoring + visa detection
  3. Auto-mark high-scoring jobs as 'saved' (ready to apply)
  4. Optionally auto-apply to saved jobs

Modes (set via --mode or PIPELINE_MODE env var):
  - crawl:       Just crawl and score (safest, no status changes)
  - rank:        Crawl, score, and auto-mark high-scoring jobs as saved
  - dry-apply:   Crawl, score, mark saved, then dry-run auto-apply (fills forms, no submit)
  - apply:       Full pipeline — crawl, score, mark saved, auto-apply (SUBMITS applications)

Usage:
    python run_pipeline.py                              # Default: rank mode
    python run_pipeline.py --mode crawl                 # Just crawl + score
    python run_pipeline.py --mode rank --min-score 40   # Mark jobs scoring 40+ as saved
    python run_pipeline.py --mode dry-apply              # Fill forms but don't submit
    python run_pipeline.py --mode apply --limit 20      # Submit up to 20 applications
    python run_pipeline.py --profile profile.yaml --mode apply

Environment variables:
    PIPELINE_MODE     - Same as --mode (crawl, rank, dry-apply, apply)
    PIPELINE_MIN_SCORE - Minimum score to auto-save (default: 30)
    PIPELINE_APPLY_LIMIT - Max applications per run (default: 50)
    PIPELINE_SOURCES  - Comma-separated sources (default: all)
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console
from rich.table import Table

console = Console()


async def run_pipeline(
    mode: str = "rank",
    min_score: int = 30,
    apply_limit: int = 50,
    sources: Optional[list[str]] = None,
    profile_path: str = "profile.yaml",
    headless: bool = True,
):
    """
    Run the full pipeline.

    Args:
        mode: 'crawl', 'rank', 'dry-apply', or 'apply'
        min_score: Minimum job_score to auto-mark as saved
        apply_limit: Max applications to submit per run
        sources: Filter to specific crawl sources
        profile_path: Path to profile.yaml (for apply modes)
        headless: Run browser headless for auto-apply
    """
    pipeline_start = datetime.now(timezone.utc)
    console.print(f"\n[bold]🌸 Pollen Pipeline — mode: {mode}[/bold]")
    console.print(f"  Started: {pipeline_start.isoformat()}")
    console.print(f"  Min score for auto-save: {min_score}")
    if mode in ("dry-apply", "apply"):
        console.print(f"  Apply limit: {apply_limit}")
    console.print()

    # ── Step 1: Crawl ──
    console.print("[bold cyan]━━ Step 1: Crawl ━━[/bold cyan]")
    from scrapers.orchestrator import CrawlOrchestrator

    orchestrator = CrawlOrchestrator()
    stats = await orchestrator.run_full_crawl(sources=sources)

    new_jobs = getattr(orchestrator, "new_jobs", [])
    console.print(f"  Crawl: {stats['total_found']} found, {stats['new_jobs']} new, {stats['errors']} errors\n")

    # ── Step 2: Score all unscored jobs ──
    console.print("[bold cyan]━━ Step 2: Score & Rank ━━[/bold cyan]")
    from utils.resume_matcher import run_batch_matching, run_visa_detection_only

    # First detect visa sponsorship for all unknown jobs
    visa_results = await run_visa_detection_only()
    console.print(f"  Visa detection: {visa_results['detected']} found in {visa_results['total_scanned']} scanned")

    # Score all unscored jobs
    scoring_results = await run_batch_matching(limit=500, force=False)
    console.print(f"  Scoring: {scoring_results['scored']} jobs scored\n")

    # ── Step 3: Auto-mark high-scoring jobs as saved ──
    if mode in ("rank", "dry-apply", "apply"):
        console.print("[bold cyan]━━ Step 3: Auto-mark high-scoring jobs ━━[/bold cyan]")
        marked = await _auto_mark_saved(min_score)
        console.print(f"  Marked {marked} jobs as saved (score >= {min_score})\n")
    else:
        console.print("[dim]  Skipping auto-mark (crawl mode only)[/dim]\n")

    # ── Step 4: Send alerts ──
    console.print("[bold cyan]━━ Step 4: Alerts ━━[/bold cyan]")
    if new_jobs:
        from utils.alerts import check_and_send_alerts
        try:
            await check_and_send_alerts(new_jobs)
            console.print(f"  Alerts sent for {len(new_jobs)} new jobs\n")
        except Exception as e:
            console.print(f"  [red]Alert error: {e}[/red]\n")
    else:
        console.print("  No new jobs to alert on\n")

    # ── Step 5: Auto-apply ──
    if mode in ("dry-apply", "apply"):
        console.print("[bold cyan]━━ Step 5: Auto-Apply ━━[/bold cyan]")
        console.print(f"  Mode: {'DRY RUN (no submit)' if mode == 'dry-apply' else 'SUBMITTING'}")
        console.print(f"  Limit: {apply_limit} jobs\n")

        # Import and run auto-apply directly
        from auto_apply import ApplicantProfile, GreenhouseAutoApply

        profile = ApplicantProfile(profile_path)
        missing = profile.validate()
        if missing:
            console.print(f"  [red]Missing profile fields: {', '.join(missing)}[/red]")
            console.print("  Skipping auto-apply step.\n")
        else:
            applier = GreenhouseAutoApply(
                profile,
                dry_run=(mode == "dry-apply"),
                auto_only=True,
                headless=headless,
            )
            await applier.start()
            try:
                # Query saved greenhouse jobs directly from DB
                from db.session import async_session, init_db
                from db.models import Job
                from sqlalchemy import select

                await init_db()
                async with async_session() as session:
                    stmt = (
                        select(Job)
                        .where(Job.source == "greenhouse", Job.status == "saved")
                        .order_by(Job.job_score.desc().nullslast(), Job.first_seen_at.desc())
                        .limit(apply_limit)
                    )
                    result = await session.execute(stmt)
                    jobs = result.scalars().all()

                if jobs:
                    console.print(f"  Found {len(jobs)} saved greenhouse jobs to apply to\n")
                    for job in jobs:
                        await applier.apply_to_job(job.url, job.company_name, job.title)
                        if mode == "apply":
                            delay = asyncio.get_event_loop().time()
                            import random as _r
                            wait = _r.uniform(3, 8)
                            console.print(f"  [dim]Waiting {wait:.0f}s...[/dim]")
                            await asyncio.sleep(wait)
                else:
                    console.print("  No saved greenhouse jobs to apply to\n")
            finally:
                await applier.close()

            applier.print_results()
    else:
        console.print("[dim]  Skipping auto-apply (crawl/rank mode only)[/dim]")

    # ── Summary ──
    elapsed = datetime.now(timezone.utc) - pipeline_start
    console.print(f"\n[bold green]━━ Pipeline Complete ━━[/bold green]")
    console.print(f"  Mode: {mode}")
    console.print(f"  Elapsed: {elapsed.total_seconds():.0f}s")
    console.print(f"  Jobs found: {stats['total_found']}")
    console.print(f"  New jobs: {stats['new_jobs']}")
    if mode in ("rank", "dry-apply", "apply"):
        console.print(f"  Auto-marked saved: {marked}")
    console.print()

    return {
        "mode": mode,
        "elapsed_seconds": elapsed.total_seconds(),
        "crawl_stats": stats,
        "new_jobs_count": len(new_jobs),
    }


async def _auto_mark_saved(min_score: int) -> int:
    """Mark all 'new' greenhouse jobs with score >= min_score as 'saved'."""
    from db.session import async_session
    from db.models import Job, JobStatus
    from sqlalchemy import select, update as sql_update

    marked = 0
    async with async_session() as session:
        stmt = (
            sql_update(Job)
            .where(
                Job.status == JobStatus.new,
                Job.source == "greenhouse",
                Job.job_score >= min_score,
            )
            .values(status=JobStatus.saved)
        )
        result = await session.execute(stmt)
        marked = result.rowcount
        await session.commit()

    return marked


def main():
    parser = argparse.ArgumentParser(description="Pollen Pipeline — crawl, score, and apply")
    parser.add_argument(
        "--mode", "-m",
        choices=["crawl", "rank", "dry-apply", "apply"],
        default=os.getenv("PIPELINE_MODE", "rank"),
        help="Pipeline mode (default: rank, or PIPELINE_MODE env var)",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=int(os.getenv("PIPELINE_MIN_SCORE", "30")),
        help="Minimum job_score to auto-mark as saved (default: 30)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("PIPELINE_APPLY_LIMIT", "50")),
        help="Max applications per run (default: 50, or PIPELINE_APPLY_LIMIT env var)",
    )
    parser.add_argument(
        "--sources", "-s",
        type=str,
        default=os.getenv("PIPELINE_SOURCES", ""),
        help="Comma-separated sources (default: all)",
    )
    parser.add_argument(
        "--profile", "-p",
        default="profile.yaml",
        help="Path to profile.yaml (for apply modes)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Show browser window during auto-apply (default: headless)",
    )
    args = parser.parse_args()

    sources = None
    if args.sources:
        sources = [s.strip() for s in args.sources.split(",")]

    result = asyncio.run(run_pipeline(
        mode=args.mode,
        min_score=args.min_score,
        apply_limit=args.limit,
        sources=sources,
        profile_path=args.profile,
        headless=not args.no_headless,
    ))


if __name__ == "__main__":
    main()
