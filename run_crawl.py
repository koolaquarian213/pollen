#!/usr/bin/env python3
"""
Run a crawl manually from the command line.

Usage:
    python run_crawl.py                          # Full crawl
    python run_crawl.py --source greenhouse      # Only Greenhouse
    python run_crawl.py --source company-websites --limit 3
    python run_crawl.py --dry-run                # Scrape but don't save
"""

import argparse
import asyncio

from scrapers.orchestrator import CrawlOrchestrator


def main():
    parser = argparse.ArgumentParser(description="Pollen Job Crawler")
    parser.add_argument(
        "--source", "-s",
        type=str, default=None,
        help="Specific source to crawl (greenhouse, lever, ashby, job_boards, company-websites)",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int, default=None,
        help="Max number of companies to crawl",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape but don't save to database",
    )
    args = parser.parse_args()

    sources = [args.source] if args.source else None

    orchestrator = CrawlOrchestrator()
    stats = asyncio.run(
        orchestrator.run_full_crawl(
            sources=sources,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )

    # Exit code: 1 if all errors, 0 otherwise
    if stats["errors"] > 0 and stats["total_found"] == 0:
        exit(1)


if __name__ == "__main__":
    main()
