"""
Database models for the job crawler.

Supports both SQLite (dev) and PostgreSQL (production).
Uses SQLAlchemy 2.0 async style.
"""

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, Enum, ForeignKey,
    Index, UniqueConstraint, JSON, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobStatus(str, enum.Enum):
    new = "new"
    saved = "saved"
    applied = "applied"
    interviewing = "interviewing"
    offer = "offer"
    rejected = "rejected"
    archived = "archived"


class Job(Base):
    """A single job listing."""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Core fields
    title: Mapped[str] = mapped_column(String(500), index=True)
    company_name: Mapped[str] = mapped_column(String(200), index=True)
    location: Mapped[Optional[str]] = mapped_column(String(300))
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    url: Mapped[str] = mapped_column(Text, unique=True)

    # Details
    description: Mapped[Optional[str]] = mapped_column(Text)
    salary_min: Mapped[Optional[int]] = mapped_column(Integer)
    salary_max: Mapped[Optional[int]] = mapped_column(Integer)
    salary_currency: Mapped[Optional[str]] = mapped_column(String(10), default="USD")
    level: Mapped[Optional[str]] = mapped_column(String(50))       # IC3, IC4, Manager, etc.
    department: Mapped[Optional[str]] = mapped_column(String(200))
    employment_type: Mapped[Optional[str]] = mapped_column(String(50))  # full-time, contract, etc.

    # Tags stored as JSON array
    tags: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    # Source tracking
    source: Mapped[str] = mapped_column(String(100), index=True)   # greenhouse, lever, linkedin, etc.
    source_id: Mapped[Optional[str]] = mapped_column(String(200))  # ID from the source system
    company_careers_url: Mapped[Optional[str]] = mapped_column(Text)

    # Timestamps
    date_posted: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # User-facing status
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.new, index=True
    )
    bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Deduplication hash
    dedup_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    __table_args__ = (
        Index("ix_jobs_company_title", "company_name", "title"),
        Index("ix_jobs_first_seen", "first_seen_at"),
        Index("ix_jobs_status_date", "status", "first_seen_at"),
    )

    def __repr__(self):
        return f"<Job {self.id}: {self.title} @ {self.company_name}>"


class CrawlLog(Base):
    """Track each crawl run for monitoring."""
    __tablename__ = "crawl_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(100))
    company_name: Mapped[Optional[str]] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))  # running, success, error
    jobs_found: Mapped[int] = mapped_column(Integer, default=0)
    jobs_new: Mapped[int] = mapped_column(Integer, default=0)
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class SavedSearch(Base):
    """User's saved search queries with optional alerts."""
    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    query: Mapped[Optional[str]] = mapped_column(String(500))
    filters: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    alert_channels: Mapped[Optional[list]] = mapped_column(JSON, default=lambda: ["email"])
    last_alert_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
