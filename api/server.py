"""
FastAPI REST API for the Pollen job dashboard.

Provides endpoints for job search, filtering, status updates,
analytics, and manual crawl triggers.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from db.session import get_session, init_db
from db.models import Job, JobStatus, CrawlLog, SavedSearch
from db.operations import search_jobs, get_stats, update_job_status
from scrapers.orchestrator import CrawlOrchestrator


# ── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Pollen API",
    description="Job crawler and search API for Product & Design roles",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await init_db()


# ── Schemas ──────────────────────────────────────────────────────────────────

class JobResponse(BaseModel):
    id: int
    title: str
    company_name: str
    location: Optional[str] = None
    remote: bool = False
    url: str
    description: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = "USD"
    level: Optional[str] = None
    department: Optional[str] = None
    employment_type: Optional[str] = None
    tags: Optional[list] = []
    source: str
    status: str = "new"
    bookmarked: bool = False
    resume_match_score: Optional[int] = None
    resume_match_details: Optional[str] = None
    visa_sponsorship: Optional[str] = "unknown"
    date_posted: Optional[datetime] = None
    first_seen_at: datetime
    last_seen_at: datetime

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    limit: int
    offset: int


class StatusUpdate(BaseModel):
    status: str


class BookmarkUpdate(BaseModel):
    bookmarked: bool


class NotesUpdate(BaseModel):
    notes: str


class SavedSearchCreate(BaseModel):
    name: str
    query: Optional[str] = ""
    filters: Optional[dict] = {}
    alerts_enabled: bool = False
    alert_channels: Optional[list[str]] = ["email"]


class SavedSearchResponse(BaseModel):
    id: int
    name: str
    query: Optional[str]
    filters: Optional[dict]
    alerts_enabled: bool
    alert_channels: Optional[list]
    created_at: datetime

    class Config:
        from_attributes = True


class CrawlTrigger(BaseModel):
    sources: Optional[list[str]] = None
    limit: Optional[int] = None


# ── Job Endpoints ────────────────────────────────────────────────────────────

@app.get("/api/jobs", response_model=JobListResponse)
async def list_jobs(
    q: Optional[str] = Query(None, description="Search query"),
    status: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    remote: Optional[bool] = Query(None),
    source: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    salary_min: Optional[int] = Query(None),
    tag: Optional[str] = Query(None),
    days: Optional[int] = Query(None, description="Posted within N days"),
    bookmarked: Optional[bool] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    order_by: str = Query("newest"),
    session: AsyncSession = Depends(get_session),
):
    """Search and filter jobs with pagination."""
    jobs, total = await search_jobs(
        session,
        query=q, status=status, company=company, location=location,
        remote=remote, source=source, level=level, salary_min=salary_min,
        tag=tag, days=days, bookmarked=bookmarked,
        limit=limit, offset=offset, order_by=order_by,
    )
    return JobListResponse(
        jobs=[JobResponse.model_validate(j) for j in jobs],
        total=total, limit=limit, offset=offset,
    )


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)):
    """Get a single job by ID."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    return JobResponse.model_validate(job)


@app.post("/api/jobs/{job_id}/status", response_model=JobResponse)
async def set_job_status(
    job_id: int,
    body: StatusUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update a job's application status."""
    try:
        JobStatus(body.status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {body.status}")

    job = await update_job_status(session, job_id, body.status)
    if not job:
        raise HTTPException(404, "Job not found")
    return JobResponse.model_validate(job)


@app.post("/api/jobs/{job_id}/bookmark", response_model=JobResponse)
async def toggle_bookmark(
    job_id: int,
    body: BookmarkUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Toggle bookmark on a job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    job.bookmarked = body.bookmarked
    await session.commit()
    return JobResponse.model_validate(job)


@app.post("/api/jobs/{job_id}/notes", response_model=JobResponse)
async def update_notes(
    job_id: int,
    body: NotesUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Update notes on a job."""
    result = await session.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")
    job.notes = body.notes
    await session.commit()
    return JobResponse.model_validate(job)


# ── Analytics ────────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def dashboard_stats(session: AsyncSession = Depends(get_session)):
    """Get dashboard analytics data."""
    return await get_stats(session)


@app.get("/api/sources")
async def source_status(session: AsyncSession = Depends(get_session)):
    """Get status of each crawl source."""
    result = await session.execute(
        select(CrawlLog)
        .order_by(CrawlLog.started_at.desc())
        .limit(20)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "source": log.source,
            "company": log.company_name,
            "status": log.status,
            "started_at": log.started_at,
            "finished_at": log.finished_at,
            "jobs_found": log.jobs_found,
            "jobs_new": log.jobs_new,
            "error": log.error_message,
        }
        for log in logs
    ]


# ── Saved Searches ──────────────────────────────────────────────────────────

@app.get("/api/saved-searches", response_model=list[SavedSearchResponse])
async def list_saved_searches(session: AsyncSession = Depends(get_session)):
    """Get all saved searches."""
    result = await session.execute(select(SavedSearch).order_by(SavedSearch.created_at.desc()))
    return [SavedSearchResponse.model_validate(s) for s in result.scalars().all()]


@app.post("/api/saved-searches", response_model=SavedSearchResponse)
async def create_saved_search(
    body: SavedSearchCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new saved search."""
    search = SavedSearch(
        name=body.name,
        query=body.query,
        filters=body.filters,
        alerts_enabled=body.alerts_enabled,
        alert_channels=body.alert_channels,
    )
    session.add(search)
    await session.commit()
    await session.refresh(search)
    return SavedSearchResponse.model_validate(search)


@app.delete("/api/saved-searches/{search_id}")
async def delete_saved_search(search_id: int, session: AsyncSession = Depends(get_session)):
    """Delete a saved search."""
    result = await session.execute(select(SavedSearch).where(SavedSearch.id == search_id))
    search = result.scalar_one_or_none()
    if not search:
        raise HTTPException(404, "Saved search not found")
    await session.delete(search)
    await session.commit()
    return {"deleted": True}


@app.patch("/api/saved-searches/{search_id}/alerts")
async def toggle_alerts(
    search_id: int,
    enabled: bool = Query(...),
    session: AsyncSession = Depends(get_session),
):
    """Toggle alerts on a saved search."""
    result = await session.execute(select(SavedSearch).where(SavedSearch.id == search_id))
    search = result.scalar_one_or_none()
    if not search:
        raise HTTPException(404, "Saved search not found")
    search.alerts_enabled = enabled
    await session.commit()
    return {"alerts_enabled": enabled}


# ── Crawl Management ────────────────────────────────────────────────────────

@app.post("/api/crawl/trigger")
async def trigger_crawl(body: CrawlTrigger, background_tasks: BackgroundTasks):
    """Manually trigger a crawl run (runs in background)."""
    background_tasks.add_task(_run_crawl, body.sources, body.limit)
    return {"status": "crawl_started", "sources": body.sources}


async def _run_crawl(sources=None, limit=None):
    """Background task to run the crawler."""
    orchestrator = CrawlOrchestrator()
    await orchestrator.run_full_crawl(sources=sources, limit=limit)


# ── Health Check ─────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "pollen", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Resume Matching ──────────────────────────────────────────────────────────

@app.post("/api/resume/match")
async def trigger_resume_matching(
    limit: int = Query(50),
    force: bool = Query(False),
    background_tasks: BackgroundTasks = None,
):
    """Trigger resume matching for unscored jobs (runs in background)."""
    background_tasks.add_task(_run_matching, limit, force)
    return {"status": "matching_started", "limit": limit}


async def _run_matching(limit=50, force=False):
    from utils.resume_matcher import run_batch_matching
    await run_batch_matching(limit=limit, force=force)


@app.post("/api/resume/visa-scan")
async def trigger_visa_scan(background_tasks: BackgroundTasks):
    """Scan all job descriptions for visa sponsorship info (no API needed)."""
    background_tasks.add_task(_run_visa_scan)
    return {"status": "visa_scan_started"}


async def _run_visa_scan():
    from utils.resume_matcher import run_visa_detection_only
    await run_visa_detection_only()


@app.get("/api/resume/top-matches")
async def top_resume_matches(
    limit: int = Query(20),
    min_score: int = Query(0),
    session: AsyncSession = Depends(get_session),
):
    """Get top resume-matched jobs sorted by score."""
    from db.models import Job
    stmt = (
        select(Job)
        .where(Job.resume_match_score.isnot(None))
        .where(Job.resume_match_score >= min_score)
        .order_by(Job.resume_match_score.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    jobs = result.scalars().all()
    return {
        "jobs": [JobResponse.model_validate(j) for j in jobs],
        "total": len(jobs),
    }


# ── Resume Tailoring Endpoint ──

class TailorRequest(BaseModel):
    resume: str
    job_title: str
    company_name: str
    job_description: str


@app.post("/api/tailor-resume")
async def tailor_resume(body: TailorRequest):
    """Use Claude to tailor a resume for a specific job."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        from config.settings import settings
        api_key = settings.llm.api_key

    if not api_key:
        return {"error": "No Anthropic API key configured. Set ANTHROPIC_API_KEY in .env"}

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""You are an expert resume consultant. I need you to tailor my resume for a specific job.

JOB TITLE: {body.job_title}
COMPANY: {body.company_name}
JOB DESCRIPTION:
{body.job_description[:3000]}

MY CURRENT RESUME:
{body.resume[:4000]}

Please:
1. Rewrite my resume to better match this specific role. Keep it truthful — only reorganize, reword, and emphasize existing experience.
2. Move the most relevant experience to the top.
3. Mirror key terms from the job description where my experience genuinely matches.
4. Add a tailored professional summary for this role.
5. Keep the format clean and ATS-friendly (no tables, columns, or graphics).

Return the tailored resume text, then on a new line write "---CHANGES---" followed by a bullet list of the key changes you made."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text
        parts = text.split("---CHANGES---")
        return {
            "tailored_resume": parts[0].strip(),
            "changes": parts[1].strip() if len(parts) > 1 else "Changes applied.",
        }
    except Exception as e:
        return {"error": str(e)}
