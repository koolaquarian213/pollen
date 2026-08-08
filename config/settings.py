"""
Configuration management for the job crawler.
Loads settings from environment variables and YAML config.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yaml
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent


@dataclass
class DatabaseConfig:
    url: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{BASE_DIR}/jobs.db")
    echo: bool = False


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 4096


@dataclass
class AlertConfig:
    slack_webhook_url: str = os.getenv("SLACK_WEBHOOK_URL", "")
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    alert_email: str = os.getenv("ALERT_EMAIL", "")


@dataclass
class CrawlConfig:
    interval_hours: int = 6
    max_concurrent: int = 5
    request_delay: float = 1.5        # seconds between requests per domain
    timeout: int = 30                   # request timeout in seconds
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    # Job title keywords to filter for Product/Design roles
    role_keywords: list[str] = field(default_factory=lambda: [
        "product design", "ux design", "ui design", "interaction design",
        "visual design", "brand design", "design system", "ux research",
        "content design", "ux writ", "design technolog", "design engineer",
        "design lead", "design director", "head of design", "vp design",
        "product manager", "group product manager", "design manager",
    ])


@dataclass
class CompanySource:
    """A single company to crawl."""
    name: str
    careers_url: str
    ats: Optional[str] = None          # greenhouse, lever, ashby, workday, or None
    ats_board_id: Optional[str] = None  # e.g., "stripe" for boards.greenhouse.io/stripe


@dataclass
class Settings:
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    crawl: CrawlConfig = field(default_factory=CrawlConfig)
    companies: list[CompanySource] = field(default_factory=list)

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Settings":
        """Load settings, merging YAML config file with env vars."""
        settings = cls()

        path = config_path or (BASE_DIR / "config" / "settings.yaml")
        if Path(path).exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}

            # Load companies from YAML
            for c in data.get("companies", []):
                settings.companies.append(CompanySource(**c))

            # Override crawl settings
            crawl_data = data.get("crawl", {})
            for k, v in crawl_data.items():
                if hasattr(settings.crawl, k):
                    setattr(settings.crawl, k, v)

        # Load default companies if none configured
        if not settings.companies:
            settings.companies = get_default_companies()

        return settings


def get_default_companies() -> list[CompanySource]:
    """Default list of top Product/Design companies to crawl."""
    return [
        # ── Greenhouse ATS ──
        CompanySource(name="Stripe", careers_url="https://stripe.com/jobs", ats="greenhouse", ats_board_id="stripe"),
        CompanySource(name="Airbnb", careers_url="https://careers.airbnb.com", ats="greenhouse", ats_board_id="airbnb"),
        CompanySource(name="Notion", careers_url="https://notion.so/careers", ats="greenhouse", ats_board_id="notion"),
        CompanySource(name="Figma", careers_url="https://figma.com/careers", ats="greenhouse", ats_board_id="figma"),
        CompanySource(name="Linear", careers_url="https://linear.app/careers", ats="greenhouse", ats_board_id="linear"),
        CompanySource(name="Vercel", careers_url="https://vercel.com/careers", ats="greenhouse", ats_board_id="vercel"),
        CompanySource(name="Ramp", careers_url="https://ramp.com/careers", ats="greenhouse", ats_board_id="ramp"),
        CompanySource(name="Coinbase", careers_url="https://coinbase.com/careers", ats="greenhouse", ats_board_id="coinbase"),
        CompanySource(name="Plaid", careers_url="https://plaid.com/careers", ats="greenhouse", ats_board_id="plaid"),
        CompanySource(name="Loom", careers_url="https://loom.com/careers", ats="greenhouse", ats_board_id="loom"),
        CompanySource(name="Discord", careers_url="https://discord.com/careers", ats="greenhouse", ats_board_id="discord"),
        CompanySource(name="Canva", careers_url="https://canva.com/careers", ats="greenhouse", ats_board_id="canva"),
        CompanySource(name="Webflow", careers_url="https://webflow.com/careers", ats="greenhouse", ats_board_id="webflow"),
        CompanySource(name="Retool", careers_url="https://retool.com/careers", ats="greenhouse", ats_board_id="retool"),
        CompanySource(name="Databricks", careers_url="https://databricks.com/careers", ats="greenhouse", ats_board_id="databricks"),

        # ── Lever ATS ──
        CompanySource(name="Netflix", careers_url="https://jobs.netflix.com", ats="lever", ats_board_id="netflix"),
        CompanySource(name="Spotify", careers_url="https://lifeatspotify.com", ats="lever", ats_board_id="spotify"),
        CompanySource(name="Airtable", careers_url="https://airtable.com/careers", ats="lever", ats_board_id="airtable"),

        # ── Ashby ATS ──
        CompanySource(name="Ramp", careers_url="https://ramp.com/careers", ats="ashby", ats_board_id="ramp"),

        # ── Custom career pages (will use Playwright + LLM) ──
        CompanySource(name="Apple", careers_url="https://jobs.apple.com/en-us/search?search=design&sort=relevance"),
        CompanySource(name="Google", careers_url="https://www.google.com/about/careers/applications/jobs/results?q=product%20design"),
        CompanySource(name="Meta", careers_url="https://www.metacareers.com/jobs?q=product%20design"),
        CompanySource(name="Microsoft", careers_url="https://careers.microsoft.com/us/en/search-results?keywords=product%20design"),
    ]


# Global singleton
settings = Settings.load()
