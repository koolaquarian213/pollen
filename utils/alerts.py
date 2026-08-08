"""
Alert system — sends notifications when new jobs match saved searches.
Supports Slack webhooks, email (SMTP), and extensible to Discord/Telegram.
"""

import json
from datetime import datetime, timezone
from typing import Optional

import httpx
from rich.console import Console

from config.settings import settings

console = Console()


async def send_slack_alert(jobs: list[dict], search_name: str = ""):
    """Send a Slack webhook notification for new jobs."""
    webhook_url = settings.alerts.slack_webhook_url
    if not webhook_url:
        return

    # Build message blocks
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🌸 {len(jobs)} new job{'s' if len(jobs) != 1 else ''} found!"
            }
        }
    ]

    if search_name:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"Matching saved search: *{search_name}*"}]
        })

    blocks.append({"type": "divider"})

    for job in jobs[:10]:  # Limit to 10 in notification
        salary = ""
        if job.get("salary_min") and job.get("salary_max"):
            salary = f" · ${job['salary_min']//1000}k–${job['salary_max']//1000}k"

        remote_badge = " 🏠 Remote" if job.get("remote") else ""

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*<{job.get('url', '#')}|{job.get('title', 'Unknown')}>*\n"
                    f"_{job.get('company_name', 'Unknown')}_ · "
                    f"{job.get('location', 'Unknown')}{remote_badge}{salary}"
                )
            }
        })

    if len(jobs) > 10:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_...and {len(jobs) - 10} more_"}]
        })

    payload = {"blocks": blocks}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code == 200:
                console.print(f"  [green]✓ Slack alert sent ({len(jobs)} jobs)[/green]")
            else:
                console.print(f"  [red]Slack webhook error: {resp.status_code}[/red]")
    except Exception as e:
        console.print(f"  [red]Slack alert failed: {e}[/red]")


async def send_email_alert(
    jobs: list[dict],
    search_name: str = "",
    recipient: Optional[str] = None,
):
    """Send an email notification for new jobs."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    cfg = settings.alerts
    if not cfg.smtp_host or not cfg.smtp_user:
        return

    to_addr = recipient or cfg.alert_email
    if not to_addr:
        return

    # Build HTML email
    job_rows = ""
    for job in jobs[:20]:
        salary = ""
        if job.get("salary_min") and job.get("salary_max"):
            salary = f"${job['salary_min']//1000}k–${job['salary_max']//1000}k"
        remote = "🏠 Remote" if job.get("remote") else ""

        job_rows += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #eee;">
                <a href="{job.get('url', '#')}" style="color: #7C3AED; font-weight: 600; text-decoration: none;">
                    {job.get('title', 'Unknown')}
                </a><br>
                <span style="color: #666; font-size: 13px;">
                    {job.get('company_name', '')} · {job.get('location', '')} {remote}
                </span>
            </td>
            <td style="padding: 12px; border-bottom: 1px solid #eee; color: #666; font-size: 13px;">
                {salary}
            </td>
        </tr>
        """

    html = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: linear-gradient(135deg, #C8A2F8, #9B6FD4); padding: 24px; border-radius: 12px 12px 0 0;">
            <h1 style="color: white; margin: 0; font-size: 20px;">🌸 {len(jobs)} New Jobs Found</h1>
            <p style="color: rgba(255,255,255,0.8); margin: 4px 0 0;">
                {'Matching: ' + search_name if search_name else 'From your Pollen job tracker'}
            </p>
        </div>
        <table style="width: 100%; border-collapse: collapse; background: white; border: 1px solid #eee;">
            <thead>
                <tr style="background: #f8f8f8;">
                    <th style="padding: 10px 12px; text-align: left; font-size: 12px; color: #999;">ROLE</th>
                    <th style="padding: 10px 12px; text-align: left; font-size: 12px; color: #999;">SALARY</th>
                </tr>
            </thead>
            <tbody>
                {job_rows}
            </tbody>
        </table>
        <div style="padding: 16px; text-align: center; color: #999; font-size: 12px;">
            Sent by Pollen Job Tracker · <a href="#" style="color: #7C3AED;">Manage alerts</a>
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌸 {len(jobs)} new {search_name or 'Product/Design'} jobs found"
    msg["From"] = cfg.smtp_user
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
            server.starttls()
            server.login(cfg.smtp_user, cfg.smtp_password)
            server.send_message(msg)
        console.print(f"  [green]✓ Email alert sent to {to_addr}[/green]")
    except Exception as e:
        console.print(f"  [red]Email alert failed: {e}[/red]")


async def check_and_send_alerts(new_jobs: list[dict]):
    """
    Check new jobs against saved searches and send alerts.
    Called after each crawl run.
    """
    from db.session import async_session
    from db.models import SavedSearch
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(SavedSearch).where(SavedSearch.alerts_enabled == True)
        )
        saved_searches = result.scalars().all()

    for search in saved_searches:
        matching = []
        query = (search.query or "").lower()
        filters = search.filters or {}

        for job in new_jobs:
            title = job.get("title", "").lower()
            company = job.get("company_name", "").lower()

            # Check query match
            if query and query not in title and query not in company:
                continue

            # Check filters
            if filters.get("remote") and not job.get("remote"):
                continue
            if filters.get("location"):
                if filters["location"].lower() not in job.get("location", "").lower():
                    continue

            matching.append(job)

        if matching:
            channels = search.alert_channels or ["email"]
            if "slack" in channels:
                await send_slack_alert(matching, search.name)
            if "email" in channels:
                await send_email_alert(matching, search.name)
