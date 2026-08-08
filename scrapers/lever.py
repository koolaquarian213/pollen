"""
Lever ATS Scraper.

Lever exposes a public JSON API at:
  https://api.lever.co/v0/postings/{company_id}?mode=json

Used by Netflix, Spotify, Airtable, and many others.
"""

from datetime import datetime, timezone
from typing import Optional

from rich.console import Console

from config.settings import CompanySource
from scrapers.base import BaseScraper, JobData

console = Console()

LEVER_API = "https://api.lever.co/v0/postings/{company_id}"


class LeverScraper(BaseScraper):
    source_name = "lever"

    async def scrape(self, company: CompanySource) -> list[JobData]:
        if not company.ats_board_id:
            console.print(f"  [red]No board_id for {company.name}[/red]")
            return []

        jobs = []
        url = LEVER_API.format(company_id=company.ats_board_id)

        try:
            response = await self.fetch(f"{url}?mode=json")
            postings = response.json()

            if not isinstance(postings, list):
                console.print(f"  [yellow]Unexpected Lever response for {company.name}[/yellow]")
                return []

            console.print(f"  [dim]Found {len(postings)} total jobs on Lever for '{company.name}'[/dim]")

            for posting in postings:
                title = posting.get("text", "")

                if not self.matches_role_filter(title):
                    continue

                # Location
                categories = posting.get("categories", {})
                location = categories.get("location", "")
                department = categories.get("department", "")
                team = categories.get("team", "")
                commitment = categories.get("commitment", "full-time")

                # Posting URL
                job_url = posting.get("hostedUrl", "")
                if not job_url:
                    job_url = posting.get("applyUrl", "")

                # Posted date
                date_posted = None
                created_at = posting.get("createdAt")
                if created_at:
                    try:
                        date_posted = datetime.fromtimestamp(
                            created_at / 1000, tz=timezone.utc
                        )
                    except (ValueError, TypeError, OSError):
                        pass

                # Description — Lever provides it in lists of objects
                desc_parts = []
                desc_lists = posting.get("lists", [])
                for lst in desc_lists:
                    header = lst.get("text", "")
                    if header:
                        desc_parts.append(header)
                    for item in lst.get("content", []):
                        desc_parts.append(f"- {item}")

                additional = posting.get("additional", "")
                if additional:
                    desc_parts.append(additional)

                description = self._clean_html("\n".join(desc_parts))

                # Salary from description
                salary_min, salary_max = self._extract_salary(description)

                # Detect remote
                remote = "remote" in location.lower() or "remote" in title.lower()

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
                    department=department or team,
                    employment_type=commitment,
                    source_id=posting.get("id", ""),
                    date_posted=date_posted,
                    company_careers_url=company.careers_url,
                )
                jobs.append(job)

            console.print(f"  [green]✓ {company.name}: {len(jobs)} matching roles[/green]")

        except Exception as e:
            console.print(f"  [red]✗ {company.name} Lever error: {e}[/red]")

        return jobs

    def _clean_html(self, text: str) -> str:
        import re
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]

    def _extract_salary(self, text: str) -> tuple[Optional[int], Optional[int]]:
        import re
        patterns = [
            r"\$(\d{1,3}(?:,\d{3})*)\s*[-–—to]+\s*\$(\d{1,3}(?:,\d{3})*)",
            r"\$(\d{2,3})k\s*[-–—to]+\s*\$(\d{2,3})k",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                low = int(match.group(1).replace(",", ""))
                high = int(match.group(2).replace(",", ""))
                if low < 1000:
                    low *= 1000
                if high < 1000:
                    high *= 1000
                return low, high
        return None, None
