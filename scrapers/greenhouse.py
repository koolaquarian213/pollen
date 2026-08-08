"""
Greenhouse ATS Scraper.

Greenhouse exposes a public JSON API at:
  https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs

This is the most reliable scraper — structured JSON, no auth needed.
Many top tech companies use Greenhouse (Stripe, Figma, Notion, Airbnb, etc.)
"""

from datetime import datetime, timezone
from typing import Optional

from rich.console import Console

from config.settings import CompanySource
from scrapers.base import BaseScraper, JobData

console = Console()

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs"
GREENHOUSE_JOB_DETAIL = "https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs/{job_id}"


class GreenhouseScraper(BaseScraper):
    source_name = "greenhouse"

    async def scrape(self, company: CompanySource) -> list[JobData]:
        """
        Fetch all jobs from a Greenhouse board.
        First gets the listing, then fetches details for matching roles.
        """
        if not company.ats_board_id:
            console.print(f"  [red]No board_id for {company.name}[/red]")
            return []

        jobs = []
        url = GREENHOUSE_API.format(board_id=company.ats_board_id)

        try:
            # Fetch job listing (includes content=true for descriptions)
            response = await self.fetch(f"{url}?content=true")
            data = response.json()

            all_jobs = data.get("jobs", [])
            console.print(
                f"  [dim]Found {len(all_jobs)} total jobs on "
                f"Greenhouse board '{company.ats_board_id}'[/dim]"
            )

            for raw_job in all_jobs:
                title = raw_job.get("title", "")

                # Filter for Product/Design roles
                if not self.matches_role_filter(title):
                    continue

                # Parse location
                location = self._parse_location(raw_job)
                loc_data = raw_job.get("location") or {}
                loc_locations = loc_data.get("locations") if isinstance(loc_data, dict) else None
                remote = any(
                    "remote" in (loc.get("name", "")).lower()
                    for loc in (loc_locations or [])
                )

                # Parse dates
                date_posted = None
                if raw_job.get("updated_at"):
                    try:
                        date_posted = datetime.fromisoformat(
                            raw_job["updated_at"].replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        pass

                # Build job URL
                job_id = raw_job.get("id")
                job_url = raw_job.get("absolute_url", "")
                if not job_url:
                    job_url = f"https://boards.greenhouse.io/{company.ats_board_id}/jobs/{job_id}"

                # Extract department
                departments = raw_job.get("departments", [])
                department = departments[0].get("name", "") if departments else ""

                # Extract description HTML and clean it
                description = raw_job.get("content", "")
                description = self._clean_html(description)

                # Parse salary from metadata if available
                salary_min, salary_max = self._parse_salary(raw_job)

                job = JobData(
                    title=title,
                    company_name=company.name,
                    url=job_url,
                    source=self.source_name,
                    location=location,
                    remote=remote,
                    description=description,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    level="",
                    department=department,
                    tags=[],
                    source_id=str(job_id),
                    date_posted=date_posted,
                    company_careers_url=company.careers_url,
                )
                jobs.append(job)

            console.print(
                f"  [green]✓ {company.name}: {len(jobs)} matching Product/Design roles[/green]"
            )

        except Exception as e:
            console.print(f"  [red]✗ {company.name} Greenhouse error: {e}[/red]")

        return jobs

    def _parse_location(self, raw_job: dict) -> str:
        """Extract a readable location string."""
        loc_data = raw_job.get("location") or {}
        if isinstance(loc_data, dict):
            name = loc_data.get("name", "")
            if name:
                return name
        # Fallback: collect from offices
        offices = raw_job.get("offices") or []
        if offices:
            return ", ".join(o.get("name", "") for o in offices[:3] if isinstance(o, dict))
        return ""

    def _clean_html(self, html: str) -> str:
        """Strip HTML tags for plain text description."""
        if not html:
            return ""
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]  # Limit length

    def _parse_salary(self, raw_job: dict) -> tuple[Optional[int], Optional[int]]:
        """Try to extract salary from metadata or compliance fields."""
        # Greenhouse sometimes includes pay range in metadata
        metadata = raw_job.get("metadata") or []
        for meta in (metadata if isinstance(metadata, list) else []):
            if meta.get("name", "").lower() in ("salary", "pay_range", "compensation"):
                value = str(meta.get("value", ""))
                return self._extract_salary_range(value)

        # Also check in the content/description
        content = raw_job.get("content", "")
        if "$" in content:
            return self._extract_salary_range(content)

        return None, None

    def _extract_salary_range(self, text: str) -> tuple[Optional[int], Optional[int]]:
        """Parse salary ranges like '$120,000 - $180,000' or '$150k-$200k'."""
        import re

        # Match patterns like $120,000 - $180,000 or $150k-$200k
        patterns = [
            r"\$(\d{1,3}(?:,\d{3})*)\s*[-–—to]+\s*\$(\d{1,3}(?:,\d{3})*)",
            r"\$(\d{2,3})k\s*[-–—to]+\s*\$(\d{2,3})k",
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                low = int(match.group(1).replace(",", ""))
                high = int(match.group(2).replace(",", ""))
                # Normalize to thousands if needed
                if low < 1000:
                    low *= 1000
                if high < 1000:
                    high *= 1000
                return low, high

        return None, None
