"""
Base scraper class with shared HTTP, rate-limiting, and role-matching logic.
"""

import asyncio
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

import httpx
from rich.console import Console

from config.settings import settings, CompanySource
from db.operations import compute_dedup_hash

console = Console()


class JobData:
    """Standardized job data structure returned by all scrapers."""

    def __init__(
        self,
        title: str,
        company_name: str,
        url: str,
        source: str,
        location: str = "",
        remote: bool = False,
        description: str = "",
        salary_min: int = None,
        salary_max: int = None,
        salary_currency: str = "USD",
        level: str = "",
        department: str = "",
        employment_type: str = "full-time",
        tags: list[str] = None,
        source_id: str = "",
        date_posted: datetime = None,
        company_careers_url: str = "",
    ):
        self.title = (title or "").strip()
        self.company_name = (company_name or "").strip()
        self.url = (url or "").strip()
        self.source = source or ""
        self.location = (location or "").strip()
        self.remote = remote or self._detect_remote(self.location)
        self.description = description
        self.salary_min = salary_min
        self.salary_max = salary_max
        self.salary_currency = salary_currency
        self.level = level or self._infer_level(title)
        self.department = department
        self.employment_type = employment_type
        self.tags = tags or self._infer_tags(title, description)
        self.source_id = source_id
        self.date_posted = date_posted
        self.company_careers_url = company_careers_url
        self.dedup_hash = compute_dedup_hash(company_name, title, location)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None and k != "dedup_hash"}

    @staticmethod
    def _detect_remote(location: str) -> bool:
        loc = (location or "").lower()
        return any(kw in loc for kw in ["remote", "anywhere", "distributed", "work from home"])

    @staticmethod
    def _infer_level(title: str) -> str:
        t = (title or "").lower()
        if "vp" in t or "vice president" in t:
            return "VP"
        if "director" in t:
            return "Director"
        if "head of" in t:
            return "Director"
        if "manager" in t and "product manager" not in t:
            return "Manager"
        if "principal" in t or "staff" in t:
            return "IC6"
        if "senior" in t or "sr." in t or "lead" in t:
            return "IC5"
        if "junior" in t or "jr." in t or "associate" in t:
            return "IC3"
        return "IC4"

    @staticmethod
    def _infer_tags(title: str, description: str) -> list[str]:
        text = f"{title or ''} {description or ''}".lower()
        tags = []
        tag_patterns = {
            "AI/ML": ["ai", "machine learning", "llm", "generative", "ml model", "deep learning", "neural", "nlp", "computer vision"],
            "GenAI": ["generative ai", "genai", "llm", "large language model", "foundation model", "chatbot"],
            "Data Platform": ["data platform", "data infrastructure", "data pipeline", "etl", "data warehouse", "lakehouse"],
            "Analytics": ["analytics", "business intelligence", "bi tool", "dashboar", "metrics", "insights"],
            "Platform": ["platform", "infrastructure", "developer experience", "api", "sdk"],
            "Cloud": ["cloud", "aws", "gcp", "azure", "kubernetes", "saas"],
            "B2B": ["b2b", "enterprise", "saas"],
            "B2C": ["b2c", "consumer", "marketplace"],
            "Growth": ["growth", "conversion", "acquisition", "retention", "a/b test", "experimentation"],
            "Mobile": ["mobile", "ios", "android", "react native", "flutter"],
            "Security": ["security", "privacy", "compliance", "trust", "identity"],
            "Fintech": ["fintech", "payments", "banking", "lending", "credit", "financial"],
            "0→1": ["0 to 1", "zero to one", "greenfield", "new product", "0-1", "incubation"],
            "Enterprise": ["enterprise", "fortune 500", "large customer"],
        }
        for tag, patterns in tag_patterns.items():
            if any(p in text for p in patterns):
                tags.append(tag)
        return tags


class BaseScraper(ABC):
    """Base class for all scrapers."""

    source_name: str = "unknown"

    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=settings.crawl.timeout,
            headers={"User-Agent": settings.crawl.user_agent},
            follow_redirects=True,
        )
        self._last_request_time = 0

    async def _rate_limit(self):
        """Enforce delay between requests."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < settings.crawl.request_delay:
            await asyncio.sleep(settings.crawl.request_delay - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def fetch(self, url: str, **kwargs) -> httpx.Response:
        """HTTP GET with rate limiting and retries."""
        await self._rate_limit()
        for attempt in range(3):
            try:
                response = await self.client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                if attempt == 2:
                    raise
                console.print(f"  [yellow]Retry {attempt + 1}/3 for {url}: {e}[/yellow]")
                await asyncio.sleep(2 ** attempt)

    def matches_role_filter(self, title: str) -> bool:
        """Check if a job title matches our target roles."""
        title_lower = title.lower()
        return any(kw in title_lower for kw in settings.crawl.role_keywords)

    @abstractmethod
    async def scrape(self, company: CompanySource) -> list[JobData]:
        """Scrape jobs from a source. Must be implemented by subclasses."""
        ...

    async def close(self):
        await self.client.aclose()
