"""
Ashby ATS Scraper.

Ashby uses a GraphQL-like API at:
  https://api.ashbyhq.com/posting-api/job-board/{board_id}

Growing ATS used by many startups.
"""

from datetime import datetime, timezone
from typing import Optional

from rich.console import Console

from config.settings import CompanySource
from scrapers.base import BaseScraper, JobData

console = Console()

ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{board_id}"


class AshbyScraper(BaseScraper):
    source_name = "ashby"

    async def scrape(self, company: CompanySource) -> list[JobData]:
        if not company.ats_board_id:
            console.print(f"  [red]No board_id for {company.name}[/red]")
            return []

        jobs = []
        url = ASHBY_API.format(board_id=company.ats_board_id)

        try:
            response = await self.fetch(url)
            data = response.json()

            all_jobs = data.get("jobs", [])
            console.print(f"  [dim]Found {len(all_jobs)} total jobs on Ashby for '{company.name}'[/dim]")

            for raw_job in all_jobs:
                title = raw_job.get("title", "")

                if not self.matches_role_filter(title):
                    continue

                # Location
                location = raw_job.get("location", "")
                if isinstance(location, dict):
                    location = location.get("name", "")

                # Secondary locations
                secondary = raw_job.get("secondaryLocations", [])
                if secondary:
                    extra = ", ".join(
                        loc.get("name", "") if isinstance(loc, dict) else str(loc)
                        for loc in secondary[:2]
                    )
                    if extra:
                        location = f"{location}, {extra}"

                # URL
                job_id = raw_job.get("id", "")
                job_url = f"https://jobs.ashbyhq.com/{company.ats_board_id}/{job_id}"

                # Department
                department = raw_job.get("department", "")
                team = raw_job.get("team", "")

                # Employment type
                employment_type = raw_job.get("employmentType", "FullTime")
                if employment_type:
                    employment_type = employment_type.replace("_", " ").lower()

                # Description
                description = raw_job.get("descriptionHtml", "") or raw_job.get("descriptionPlain", "")
                description = self._clean_html(description)

                # Posted date
                date_posted = None
                published = raw_job.get("publishedAt") or raw_job.get("updatedAt")
                if published:
                    try:
                        date_posted = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        pass

                # Compensation
                salary_min, salary_max = None, None
                comp = raw_job.get("compensation")
                if comp:
                    salary_min = comp.get("min")
                    salary_max = comp.get("max")

                remote = raw_job.get("isRemote", False)

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
                    employment_type=employment_type,
                    source_id=job_id,
                    date_posted=date_posted,
                    company_careers_url=company.careers_url,
                )
                jobs.append(job)

            console.print(f"  [green]✓ {company.name}: {len(jobs)} matching roles[/green]")

        except Exception as e:
            console.print(f"  [red]✗ {company.name} Ashby error: {e}[/red]")

        return jobs

    def _clean_html(self, html: str) -> str:
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:5000]
