"""
Resume Matcher & Job Scoring Utility.

Provides lightweight, transparent relevance scoring for jobs based on:
  - Title keyword overlap with target role
  - Tag matches (AI/ML, GenAI, Platform, etc.)
  - Seniority alignment
  - Remote/location fit
  - Salary range
  - Visa sponsorship detection
  - Negative filters (defense, clearance, etc.)

No embeddings required — this is fast, debuggable, and runs locally.

Also provides visa sponsorship detection by scanning job descriptions
for common sponsorship-related phrases.
"""

import re
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from db.models import Job


# ── Scoring Config ──────────────────────────────────────────────────────────

# Target role keywords — weighted higher
TARGET_KEYWORDS = {
    # Role (highest weight)
    "product manager": 25,
    "senior product manager": 30,
    "staff product manager": 35,
    "group product manager": 30,
    "principal product manager": 35,
    "head of product": 30,
    "director of product": 30,
    "vp of product": 30,
    # AI/ML (high weight)
    "ai product": 20,
    "ml product": 20,
    "ai/ml": 15,
    "machine learning": 10,
    "generative ai": 15,
    "genai": 15,
    "llm": 10,
    # Platform/infra (medium)
    "platform product": 15,
    "infrastructure product": 15,
    "data product": 15,
    # Growth/analytics (medium)
    "growth product": 12,
    "analytics product": 12,
}

# Tags that add to score
TAG_WEIGHTS = {
    "AI/ML": 15,
    "GenAI": 15,
    "Data Platform": 10,
    "Analytics": 8,
    "Platform": 10,
    "B2B": 5,
    "Growth": 8,
    "Fintech": 5,
    "0→1": 10,
    "Enterprise": 5,
}

# Seniority alignment (assume target is Senior PM / Staff PM)
SENIORITY_SCORE = {
    "IC5": 15,  # Senior
    "IC6": 15,  # Staff/Principal
    "VP": 10,
    "Director": 10,
    "IC4": 10,  # Mid (still acceptable)
    "Manager": 8,
    "IC3": 0,   # Junior (not ideal)
}

# Negative filters — subtract from score or skip entirely
NEGATIVE_KEYWORDS = {
    "defense": -40,
    "clearance": -40,
    "secret clearance": -50,
    "top secret": -50,
    "ts/sci": -50,
    "anduril": -50,
    "palantir": -15,
    "lockheed": -40,
    "raytheon": -40,
    "northrop": -40,
    "contractor": -10,
    "intern": -30,
    "junior": -10,
    "associate": -5,
    "entry level": -20,
}

# Visa sponsorship detection patterns
VISA_POSITIVE_PATTERNS = [
    r"visa\s+sponsorship",
    r"will\s+sponsor",
    r"offer\s+sponsorship",
    r"provide\s+visa",
    r"h1b\s+sponsor",
    r"opt\s+stem",
    r"work\s+visa\s+sponsorship",
    r"immigration\s+support",
    r"we\s+sponsor",
    r"sponsorship\s+available",
    r"sponsorship\s+is\s+available",
    r"may\s+provide\s+sponsorship",
]

VISA_NEGATIVE_PATTERNS = [
    r"no\s+visa\s+sponsorship",
    r"unable\s+to\s+sponsor",
    r"cannot\s+sponsor",
    r"no\s+sponsorship",
    r"not\s+offer\s+sponsorship",
    r"must\s+be\s+authorized\s+to\s+work",
    r"without\s+sponsorship",
    r"does\s+not\s+provide\s+sponsorship",
    r"unsponsored",
    r"no\s+future\s+sponsorship",
]


def score_job(job: "Job", target_keywords: dict = None, tag_weights: dict = None,
               negative_keywords: dict = None) -> tuple[int, str]:
    """
    Calculate a relevance score for a job (0-100).

    Returns (score, details_string).
    """
    kw = target_keywords or TARGET_KEYWORDS
    tw = tag_weights or TAG_WEIGHTS
    nk = negative_keywords or NEGATIVE_KEYWORDS

    title = (job.title or "").lower()
    description = (job.description or "").lower()
    full_text = f"{title} {description}"
    tags = job.tags or []
    level = job.level or ""
    company = (job.company_name or "").lower()

    score = 0
    details = []

    # ── Title keyword matching ──
    title_score = 0
    for keyword, weight in kw.items():
        if keyword in title:
            title_score = max(title_score, weight)
            details.append(f"title:'{keyword}'(+{weight})")
    score += title_score

    # ── Tag matching ──
    tag_score = 0
    for tag in tags:
        if tag in tw:
            tag_score += tw[tag]
            details.append(f"tag:{tag}(+{tw[tag]})")
    score += min(tag_score, 30)  # cap tag bonus

    # ── Seniority ──
    level_score = SENIORITY_SCORE.get(level, 0)
    if level_score:
        score += level_score
        details.append(f"level:{level}(+{level_score})")

    # ── Remote bonus ──
    if job.remote:
        score += 5
        details.append("remote(+5)")

    # ── Salary bonus ──
    if job.salary_max and job.salary_max >= 150000:
        score += 5
        details.append(f"salary${job.salary_max//1000}k(+5)")

    # ── Visa sponsorship bonus ──
    if job.visa_sponsorship == "yes":
        score += 10
        details.append("visa_sponsor(+10)")

    # ── Negative filters ──
    for keyword, penalty in nk.items():
        if keyword in full_text or keyword in company:
            score += penalty  # penalty is negative
            details.append(f"neg:'{keyword}'({penalty})")

    # Clamp to 0-100
    score = max(0, min(100, score))

    return score, "; ".join(details)


def detect_visa_sponsorship(description: str) -> str:
    """
    Scan a job description for visa sponsorship signals.
    Returns 'yes', 'no', or 'unknown'.
    """
    if not description:
        return "unknown"

    desc_lower = description.lower()

    # Check negative patterns first (they're more definitive)
    for pattern in VISA_NEGATIVE_PATTERNS:
        if re.search(pattern, desc_lower):
            return "no"

    # Check positive patterns
    for pattern in VISA_POSITIVE_PATTERNS:
        if re.search(pattern, desc_lower):
            return "yes"

    return "unknown"


async def run_batch_matching(limit: int = 50, force: bool = False) -> dict:
    """
    Score all unscored (or all if force=True) jobs.
    Updates resume_match_score, resume_match_details, job_score, and visa_sponsorship in DB.
    """
    from db.session import async_session
    from db.models import Job
    from sqlalchemy import select

    async with async_session() as session:
        if force:
            stmt = select(Job).order_by(Job.first_seen_at.desc()).limit(limit)
        else:
            stmt = (
                select(Job)
                .where(Job.job_score.is_(None))
                .order_by(Job.first_seen_at.desc())
                .limit(limit)
            )

        result = await session.execute(stmt)
        jobs = list(result.scalars().all())

        scored = 0
        visa_detected = 0

        for job in jobs:
            # Detect visa sponsorship if unknown
            if job.visa_sponsorship == "unknown" or job.visa_sponsorship is None:
                visa = detect_visa_sponsorship(job.description or "")
                job.visa_sponsorship = visa
                if visa != "unknown":
                    visa_detected += 1

            # Score the job
            score, details = score_job(job)
            job.job_score = score
            job.resume_match_score = score
            job.resume_match_details = details
            scored += 1

        await session.commit()

    return {
        "scored": scored,
        "visa_detected": visa_detected,
        "total": len(jobs),
    }


async def run_visa_detection_only() -> dict:
    """
    Scan all job descriptions for visa sponsorship info without scoring.
    """
    from db.session import async_session
    from db.models import Job
    from sqlalchemy import select

    async with async_session() as session:
        stmt = (
            select(Job)
            .where(Job.visa_sponsorship == "unknown")
            .order_by(Job.first_seen_at.desc())
        )
        result = await session.execute(stmt)
        jobs = list(result.scalars().all())

        detected = 0
        for job in jobs:
            visa = detect_visa_sponsorship(job.description or "")
            if visa != "unknown":
                job.visa_sponsorship = visa
                detected += 1

        await session.commit()

    return {"total_scanned": len(jobs), "detected": detected}
