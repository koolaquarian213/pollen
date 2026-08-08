"""
Database operations for jobs — insert, dedup, search, stats.
"""

import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, func, update, or_, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from thefuzz import fuzz

from db.models import Job, JobStatus, CrawlLog


def compute_dedup_hash(company: str, title: str, location: str = "") -> str:
    """Create a deterministic hash for deduplication."""
    normalized = f"{company.lower().strip()}|{title.lower().strip()}|{location.lower().strip()}"
    return hashlib.sha256(normalized.encode()).hexdigest()


async def upsert_job(session: AsyncSession, job_data: dict) -> tuple[Job, bool]:
    """
    Insert or update a job. Returns (job, is_new).

    job_data should contain at minimum: title, company_name, url, source
    """
    # Compute dedup hash
    dedup_hash = compute_dedup_hash(
        job_data.get("company_name", ""),
        job_data.get("title", ""),
        job_data.get("location", ""),
    )

    # Check if exists
    stmt = select(Job).where(Job.dedup_hash == dedup_hash)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Update last_seen and any changed fields
        existing.last_seen_at = datetime.now(timezone.utc)
        for key in ["description", "salary_min", "salary_max", "level", "tags", "remote"]:
            if key in job_data and job_data[key] is not None:
                setattr(existing, key, job_data[key])
        await session.commit()
        return existing, False

    # Also check by URL in case title/company changed slightly
    if job_data.get("url"):
        stmt = select(Job).where(Job.url == job_data["url"])
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.last_seen_at = datetime.now(timezone.utc)
            await session.commit()
            return existing, False

    # New job
    job = Job(dedup_hash=dedup_hash, **job_data)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job, True


async def search_jobs(
    session: AsyncSession,
    query: Optional[str] = None,
    status: Optional[str] = None,
    company: Optional[str] = None,
    location: Optional[str] = None,
    remote: Optional[bool] = None,
    source: Optional[str] = None,
    level: Optional[str] = None,
    salary_min: Optional[int] = None,
    tag: Optional[str] = None,
    days: Optional[int] = None,
    bookmarked: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    order_by: str = "newest",
) -> tuple[list[Job], int]:
    """Search and filter jobs. Returns (jobs, total_count)."""

    conditions = []

    if query:
        q = f"%{query.lower()}%"
        conditions.append(
            or_(
                func.lower(Job.title).like(q),
                func.lower(Job.company_name).like(q),
                func.lower(Job.description).like(q),
            )
        )

    if status:
        conditions.append(Job.status == status)
    if company:
        conditions.append(func.lower(Job.company_name) == company.lower())
    if location:
        conditions.append(func.lower(Job.location).like(f"%{location.lower()}%"))
    if remote is not None:
        conditions.append(Job.remote == remote)
    if source:
        conditions.append(Job.source == source)
    if level:
        conditions.append(Job.level == level)
    if salary_min:
        conditions.append(Job.salary_min >= salary_min)
    if tag:
        # JSON array contains — works in both SQLite (json) and PostgreSQL
        conditions.append(Job.tags.contains([tag]))
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        conditions.append(Job.first_seen_at >= cutoff)
    if bookmarked is not None:
        conditions.append(Job.bookmarked == bookmarked)

    where = and_(*conditions) if conditions else True

    # Count
    count_stmt = select(func.count(Job.id)).where(where)
    count_result = await session.execute(count_stmt)
    total = count_result.scalar()

    # Fetch
    stmt = select(Job).where(where).limit(limit).offset(offset)

    if order_by == "newest":
        stmt = stmt.order_by(Job.first_seen_at.desc())
    elif order_by == "salary_desc":
        stmt = stmt.order_by(Job.salary_max.desc().nullslast())
    elif order_by == "salary_asc":
        stmt = stmt.order_by(Job.salary_min.asc().nullslast())
    elif order_by == "company":
        stmt = stmt.order_by(Job.company_name.asc())

    result = await session.execute(stmt)
    jobs = list(result.scalars().all())

    return jobs, total


async def get_stats(session: AsyncSession) -> dict:
    """Dashboard analytics."""
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    total = (await session.execute(select(func.count(Job.id)))).scalar()
    new_this_week = (await session.execute(
        select(func.count(Job.id)).where(Job.first_seen_at >= week_ago)
    )).scalar()

    # Status breakdown
    status_counts = {}
    for s in JobStatus:
        count = (await session.execute(
            select(func.count(Job.id)).where(Job.status == s)
        )).scalar()
        status_counts[s.value] = count

    # Source breakdown
    source_stmt = select(Job.source, func.count(Job.id)).group_by(Job.source)
    source_result = await session.execute(source_stmt)
    source_counts = {row[0]: row[1] for row in source_result.all()}

    # Top companies
    company_stmt = (
        select(Job.company_name, func.count(Job.id))
        .group_by(Job.company_name)
        .order_by(func.count(Job.id).desc())
        .limit(15)
    )
    company_result = await session.execute(company_stmt)
    top_companies = [{"name": row[0], "count": row[1]} for row in company_result.all()]

    # Jobs per week (last 8 weeks)
    weekly_data = []
    for i in range(7, -1, -1):
        start = now - timedelta(weeks=i + 1)
        end = now - timedelta(weeks=i)
        count = (await session.execute(
            select(func.count(Job.id)).where(
                and_(Job.first_seen_at >= start, Job.first_seen_at < end)
            )
        )).scalar()
        weekly_data.append({
            "week": start.strftime("%b %d"),
            "jobs": count,
        })

    return {
        "total": total,
        "new_this_week": new_this_week,
        "by_status": status_counts,
        "by_source": source_counts,
        "top_companies": top_companies,
        "weekly_trend": weekly_data,
    }


async def update_job_status(session: AsyncSession, job_id: int, status: str) -> Optional[Job]:
    """Update a job's application status."""
    stmt = select(Job).where(Job.id == job_id)
    result = await session.execute(stmt)
    job = result.scalar_one_or_none()
    if job:
        job.status = JobStatus(status)
        job.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return job


async def log_crawl(
    session: AsyncSession,
    source: str,
    company_name: str = None,
    status: str = "running",
) -> CrawlLog:
    """Create a crawl log entry."""
    log = CrawlLog(
        source=source,
        company_name=company_name,
        started_at=datetime.now(timezone.utc),
        status=status,
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)
    return log


async def finish_crawl_log(
    session: AsyncSession,
    log_id: int,
    status: str = "success",
    jobs_found: int = 0,
    jobs_new: int = 0,
    jobs_updated: int = 0,
    error_message: str = None,
):
    """Update crawl log on completion."""
    stmt = select(CrawlLog).where(CrawlLog.id == log_id)
    result = await session.execute(stmt)
    log = result.scalar_one_or_none()
    if log:
        log.finished_at = datetime.now(timezone.utc)
        log.status = status
        log.jobs_found = jobs_found
        log.jobs_new = jobs_new
        log.jobs_updated = jobs_updated
        log.error_message = error_message
        await session.commit()
