"""
Pollen Web App — serves the API + frontend in one process.

Usage:
    uvicorn webapp:app --host 0.0.0.0 --port 8000
"""

import os
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Import the existing API app
from api.server import app


# ── Onboarding Endpoint ──

@app.post("/api/onboard")
async def onboard_resume(resume: UploadFile = File(...)):
    """Parse an uploaded resume and return extracted profile + search config."""
    import tempfile
    import json
    from onboard import ResumeParser, OnboardingEngine

    # Save uploaded file temporarily
    suffix = Path(resume.filename).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await resume.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        parser = ResumeParser()
        resume_text = parser.read_resume(tmp_path)

        if not resume_text or len(resume_text) < 50:
            return JSONResponse({"error": "Could not read resume. Try PDF, DOCX, or TXT."}, status_code=400)

        # Parse with Claude
        data = await parser.parse(resume_text)
        if not data:
            return JSONResponse({"error": "Failed to parse resume. Check API key."}, status_code=500)

        # Generate configs
        engine = OnboardingEngine()
        search_config = engine.generate_search_config(data)
        matcher_config = engine.generate_matcher_config(data)

        return {
            "profile": data,
            "search_config": search_config,
            "matcher_config": matcher_config,
            "resume_text": resume_text[:2000],
        }

    finally:
        os.unlink(tmp_path)


@app.post("/api/onboard/save-profile")
async def save_profile(profile: dict):
    """Save the profile.yaml from onboarding data."""
    import yaml
    from onboard import OnboardingEngine

    engine = OnboardingEngine()
    engine.generate_profile(profile, profile.get("resume_path", "./resume.pdf"))
    return {"status": "saved", "path": "profile.yaml"}


@app.post("/api/score-all")
async def score_all_jobs(min_score: int = 0):
    """Score all unscored jobs using the resume matcher."""
    try:
        from utils.resume_matcher import ResumeMatcher
        import sqlite3

        matcher = ResumeMatcher()
        conn = sqlite3.connect("jobs.db")
        c = conn.cursor()
        c.execute("SELECT id, title, description, company_name, location, remote, salary_min, salary_max, source, level FROM jobs WHERE job_score IS NULL OR job_score = 0")
        rows = c.fetchall()

        scored = 0
        for row in rows:
            job_id = row[0]
            score = matcher.score({
                "title": row[1] or "", "description": row[2] or "",
                "company_name": row[3] or "", "location": row[4] or "",
                "remote": row[5], "salary_min": row[6], "salary_max": row[7],
                "source": row[8] or "", "level": row[9] or "",
            })
            c.execute("UPDATE jobs SET job_score = ? WHERE id = ?", (score, job_id))
            scored += 1

        conn.commit()
        conn.close()
        return {"scored": scored, "total": len(rows)}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Serve frontend ──

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the main dashboard."""
    for name in ["index.html", "dashboard-v3.html", "dashboard-v2.html"]:
        if Path(name).exists():
            return HTMLResponse(Path(name).read_text())
    # Fallback — always return 200 so Render health check passes
    return HTMLResponse("""<!DOCTYPE html>
<html><head><title>Pollen</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;background:#FBFBFA;color:#1A1A18}
.c{text-align:center}h1{font-size:28px;margin-bottom:8px}p{color:#6B6B65;font-size:14px}a{color:#2C5FE0}code{background:#EFEFED;padding:2px 6px;border-radius:4px;font-size:12px}</style>
</head><body><div class="c">
<h1>🌸 Pollen</h1>
<p>Job search automation — API is running</p>
<p style="margin-top:16px"><a href="/docs">API Docs</a> &middot; <a href="/onboard">Upload Resume</a></p>
<p style="margin-top:12px;font-size:12px;color:#9B9B95">Try: <code>/api/jobs?limit=10</code></p>
</div></body></html>""")


@app.get("/onboard")
async def serve_onboard():
    """Serve the onboarding page."""
    if Path("onboard.html").exists():
        return FileResponse("onboard.html")
    return JSONResponse({"error": "Onboarding page not found"}, status_code=404)
