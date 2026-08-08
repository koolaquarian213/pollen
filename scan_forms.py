"""
Greenhouse Form Scanner.

Scans all Greenhouse job application forms and builds a consolidated
report of every field/question encountered — then shows what your
profile.yaml can answer vs. what's missing.

Usage:
    python scan_forms.py --profile profile.yaml --limit 10
    python scan_forms.py --profile profile.yaml              # scan all saved greenhouse jobs
    python scan_forms.py --profile profile.yaml --status new  # scan new jobs
"""

import argparse
import asyncio
import json
import re
import random
from collections import Counter
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console
from rich.table import Table

console = Console()


class FormScanner:
    """Scans Greenhouse forms and catalogs all fields."""

    def __init__(self, profile_path: str):
        self.profile_path = profile_path
        with open(profile_path) as f:
            self.profile = yaml.safe_load(f) or {}
        self.browser = None
        self.playwright = None

        # Collected data
        self.all_fields = []       # Every field from every form
        self.by_job = {}           # job_url -> [fields]
        self.field_counter = Counter()  # field_label -> count across jobs
        self.required_counter = Counter()
        self.unanswered = Counter()  # required fields we can't fill
        self.answered = Counter()    # required fields we can fill
        self.errors = []

    async def start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    def _get_greenhouse_url(self, url: str) -> str:
        """Convert to direct Greenhouse application form URL."""
        if "greenhouse.io" in url:
            if "/jobs/" in url and "#app" not in url:
                return url + "#app"
            return url
        gh_match = re.search(r'gh_jid=(\d+)', url)
        if gh_match:
            return f"https://boards.greenhouse.io/embed/job_app?token={gh_match.group(1)}"
        job_match = re.search(r'/jobs?/(\d+)', url)
        if job_match:
            return f"https://boards.greenhouse.io/embed/job_app?token={job_match.group(1)}"
        return url

    async def scan_job(self, job_url: str, company: str = "", title: str = "") -> list[dict]:
        """Scan a single job's application form and return all fields found."""
        fields = []
        display = f"{title} at {company}" if company else job_url[:60]

        try:
            page = await self.browser.new_page()
            page.set_default_timeout(30000)

            direct_url = self._get_greenhouse_url(job_url)

            try:
                await page.goto(direct_url, wait_until="domcontentloaded", timeout=25000)
            except Exception:
                if direct_url != job_url:
                    await page.goto(job_url, wait_until="domcontentloaded", timeout=25000)

            await asyncio.sleep(2)

            # Click "Apply" button if present
            apply_btn = await page.query_selector(
                'a[href*="application"], button:has-text("Apply"), '
                'a:has-text("Apply for this job"), a:has-text("Apply Now")'
            )
            if apply_btn:
                try:
                    await apply_btn.click()
                    await page.wait_for_load_state("domcontentloaded")
                    await asyncio.sleep(1)
                except Exception:
                    pass

            # Extract all form fields
            field_elements = await page.query_selector_all(
                'input, select, textarea'
            )

            for el in field_elements:
                try:
                    field_info = await el.evaluate("""el => {
                        const tag = el.tagName.toLowerCase();
                        const type = el.type || tag;
                        const name = el.name || '';
                        const id = el.id || '';
                        const placeholder = el.placeholder || '';
                        const required = el.required || el.getAttribute('aria-required') === 'true';
                        const visible = el.offsetParent !== null;

                        // Skip individual radio/checkbox options — we want the group label, not each option
                        if (type === 'radio' || type === 'checkbox') {
                            return { tag, type, name, id, label: '', placeholder, required, visible, options: [], skip: true };
                        }

                        // Find label
                        let label = '';
                        if (id) {
                            const lbl = document.querySelector('label[for="' + id + '"]');
                            if (lbl) label = lbl.textContent.trim();
                        }
                        if (!label) {
                            const parent = el.closest('.field, .form-group, fieldset, div, li');
                            if (parent) {
                                const lbl = parent.querySelector('label, legend, .label');
                                if (lbl) label = lbl.textContent.trim();
                            }
                        }
                        if (!label) label = placeholder || name;

                        // Get options for select
                        let options = [];
                        if (tag === 'select') {
                            options = Array.from(el.options).map(o => o.text.trim()).filter(t => t && t !== '' && t !== '--');
                        }

                        return { tag, type, name, id, label, placeholder, required, visible, options, skip: false };
                    }""")

                    if not field_info.get("visible"):
                        continue
                    if field_info.get("type") == "hidden":
                        continue
                    if field_info.get("skip"):
                        continue

                    if not field_info.get("visible"):
                        continue
                    if field_info.get("type") == "hidden":
                        continue

                    label = field_info.get("label", "").replace("*", "").strip()
                    if not label or len(label) < 2:
                        continue

                    # Clean up long labels
                    label = re.sub(r'\s+', ' ', label)[:100]

                    field = {
                        "label": label,
                        "type": field_info.get("type", "text"),
                        "required": field_info.get("required", False),
                        "name": field_info.get("name", ""),
                        "options": field_info.get("options", []),
                        "company": company,
                        "title": title,
                        "can_answer": False,
                    }

                    # Check if we can answer this
                    field["can_answer"] = self._can_answer(label)

                    fields.append(field)
                    self.all_fields.append(field)
                    self.field_counter[label] += 1

                    if field["required"]:
                        self.required_counter[label] += 1
                        if field["can_answer"]:
                            self.answered[label] += 1
                        else:
                            self.unanswered[label] += 1

                except Exception:
                    continue

            self.by_job[job_url] = fields
            console.print(f"  [dim]✓ {display}: {len(fields)} fields ({sum(1 for f in fields if f['required'])} required)[/dim]")

            await page.close()

        except Exception as e:
            self.errors.append({"url": job_url, "company": company, "title": title, "error": str(e)})
            console.print(f"  [red]✗ {display}: {e}[/red]")

        return fields

    def _can_answer(self, label: str) -> bool:
        """Check if the profile can answer this field using broad fuzzy matching."""
        lt = label.lower()

        # Skip noise — common non-question labels
        noise = ["select", "choose", "please select", "job_application", "submit", "apply"]
        if lt.strip() in noise:
            return True

        # ── Name fields (many variants) ──
        if any(w in lt for w in ["first name", "given name", "legal name", "full name"]):
            return bool(self.profile.get("first_name"))
        if any(w in lt for w in ["last name", "family name", "surname"]):
            return bool(self.profile.get("last_name"))

        # ── Contact ──
        if "email" in lt:
            return bool(self.profile.get("email"))
        if "phone" in lt or "mobile" in lt or "telephone" in lt:
            return bool(self.profile.get("phone"))
        if "linkedin" in lt:
            return bool(self.profile.get("linkedin"))
        if any(w in lt for w in ["website", "portfolio", "personal site", "url", "github"]):
            return bool(self.profile.get("website"))

        # ── Resume / Cover Letter ──
        if any(w in lt for w in ["resume", "cv", "cover letter", "file"]):
            return bool(self.profile.get("resume_path"))

        # ── Location variants ──
        if any(w in lt for w in ["location", "where are you based", "where do you live"]):
            return bool(self.profile.get("location"))
        if any(w in lt for w in ["city", "town"]):
            return bool(self.profile.get("city") or self.profile.get("location"))
        if any(w in lt for w in ["state", "province", "region"]):
            return bool(self.profile.get("state") or self.profile.get("location"))
        if "country" in lt:
            return bool(self.profile.get("country"))
        if any(w in lt for w in ["zip", "postal"]):
            return bool(self.profile.get("zip_code"))
        if "address" in lt:
            return bool(self.profile.get("location"))

        # ── Education variants ──
        if any(w in lt for w in ["education", "highest degree", "level of education", "academic"]):
            return bool(self.profile.get("education_level"))
        if any(w in lt for w in ["school", "university", "college", "institution", "alma mater"]):
            return bool(self.profile.get("school"))
        if any(w in lt for w in ["degree", "diploma", "certification"]):
            return bool(self.profile.get("degree"))
        if any(w in lt for w in ["major", "field of study", "area of study", "discipline", "concentration", "subject"]):
            return bool(self.profile.get("major"))
        if any(w in lt for w in ["graduation", "grad year", "year of completion"]):
            return bool(self.profile.get("graduation_year"))

        # ── Current work variants ──
        if any(w in lt for w in ["current company", "current employer", "present employer",
                                  "most recent company", "name of your current employer",
                                  "where do you currently work", "current organization"]):
            return bool(self.profile.get("current_company"))
        if any(w in lt for w in ["current title", "current role", "current position",
                                  "job title", "current job title", "your title",
                                  "most recent title", "what is your title"]):
            return bool(self.profile.get("current_title"))
        if any(w in lt for w in ["years of experience", "years experience", "how many years",
                                  "total experience", "years of relevant", "professional experience"]):
            return bool(self.profile.get("years_experience"))
        if any(w in lt for w in ["management experience", "people management", "direct reports",
                                  "managed a team", "leadership experience"]):
            return bool(self.profile.get("management_experience"))

        # ── Work authorization / Visa (many phrasings) ──
        if any(w in lt for w in ["authorized to work", "work authorization", "eligible to work",
                                  "legally authorized", "right to work", "permitted to work",
                                  "employment eligibility", "lawfully"]):
            return bool(self.profile.get("work_authorization"))
        if any(w in lt for w in ["visa", "sponsorship", "immigration", "sponsor",
                                  "require sponsorship", "need sponsorship"]):
            return bool(self.profile.get("visa_sponsorship"))

        # ── Salary ──
        if any(w in lt for w in ["salary", "compensation", "pay expectation", "desired salary",
                                  "expected salary", "total comp", "salary expectation"]):
            return bool(self.profile.get("salary_expectation"))

        # ── Start date / Availability ──
        if any(w in lt for w in ["start date", "when can you start", "available to start",
                                  "earliest start", "availability", "when are you available",
                                  "date available"]):
            return bool(self.profile.get("start_date"))
        if any(w in lt for w in ["notice period", "current notice"]):
            return bool(self.profile.get("notice_period"))

        # ── Relocation / Remote ──
        if any(w in lt for w in ["relocat", "willing to move", "open to moving"]):
            return bool(self.profile.get("willing_to_relocate"))
        if any(w in lt for w in ["remote", "work from home", "hybrid"]):
            return bool(self.profile.get("comfortable_remote"))

        # ── Referral / How did you hear ──
        if any(w in lt for w in ["how did you hear", "where did you hear", "how did you find",
                                  "how did you learn", "referral", "source"]):
            return bool(self.profile.get("how_did_you_hear"))

        # ── EEO / Demographics ──
        if "gender" in lt or "sex" in lt:
            return bool(self.profile.get("gender"))
        if any(w in lt for w in ["race", "ethnic", "hispanic", "latino"]):
            return bool(self.profile.get("race"))
        if "veteran" in lt or "military" in lt:
            return bool(self.profile.get("veteran_status"))
        if "disability" in lt or "disabilit" in lt:
            return bool(self.profile.get("disability_status"))

        # ── Acknowledgements / Agreements (always answerable) ──
        if any(w in lt for w in ["acknowledge", "agree", "consent", "confirm", "accept",
                                  "i have read", "nda", "privacy", "terms"]):
            return True

        # ── Age verification ──
        if any(w in lt for w in ["18 years", "legal age", "age or older"]):
            return True

        # ── Previously employed ──
        if any(w in lt for w in ["previously employed", "ever been employed", "currently or have you ever",
                                  "former employee"]):
            return True  # Can answer "No"

        # ── Check custom_answers ──
        custom = self.profile.get("custom_answers") or {}
        for key in custom:
            if key.lower() in lt or lt in key.lower():
                return True
            # Fuzzy: check if 3+ words overlap
            key_words = set(key.lower().split())
            lt_words = set(lt.split())
            if len(key_words & lt_words) >= 3:
                return True

        return False

    def print_report(self):
        """Print a comprehensive report of all fields found."""
        console.print(f"\n{'━' * 70}")
        console.print(f"[bold]📋 FORM SCAN REPORT[/bold]")
        console.print(f"{'━' * 70}\n")

        console.print(f"  Jobs scanned: {len(self.by_job)}")
        console.print(f"  Errors: {len(self.errors)}")
        console.print(f"  Total unique fields: {len(self.field_counter)}")
        console.print(f"  Total required fields: {len(self.required_counter)}")
        total_req = len(self.required_counter)
        can_answer = len(self.answered)
        cannot_answer = len(self.unanswered)
        coverage = (can_answer / total_req * 100) if total_req else 100
        console.print(f"  Profile coverage: [{'green' if coverage >= 80 else 'yellow' if coverage >= 50 else 'red'}]{coverage:.0f}%[/] ({can_answer}/{total_req} required fields)\n")

        # ── Required fields you CAN'T answer ──
        if self.unanswered:
            console.print(f"[bold red]⚠ REQUIRED FIELDS YOUR PROFILE CAN'T ANSWER ({len(self.unanswered)}):[/bold red]")
            table = Table(show_header=True, header_style="bold")
            table.add_column("Field", style="white", min_width=40)
            table.add_column("Appears in", style="cyan", justify="right")
            table.add_column("Action needed", style="yellow")

            for label, count in self.unanswered.most_common():
                # Suggest what to add to profile
                suggestion = self._suggest_fix(label)
                table.add_row(label[:60], f"{count} jobs", suggestion)

            console.print(table)
            console.print()

        # ── Required fields you CAN answer ──
        if self.answered:
            console.print(f"[bold green]✓ REQUIRED FIELDS YOUR PROFILE COVERS ({len(self.answered)}):[/bold green]")
            for label, count in self.answered.most_common():
                console.print(f"  [green]✓[/green] {label[:60]} [dim]({count} jobs)[/dim]")
            console.print()

        # ── All fields by frequency ──
        console.print(f"[bold]ALL FIELDS BY FREQUENCY (top 30):[/bold]")
        table2 = Table(show_header=True, header_style="bold")
        table2.add_column("Field", style="white", min_width=40)
        table2.add_column("Count", style="cyan", justify="right")
        table2.add_column("Required", style="yellow", justify="center")
        table2.add_column("Covered", style="green", justify="center")

        for label, count in self.field_counter.most_common(30):
            req = "✱" if label in self.required_counter else ""
            covered = "✓" if self._can_answer(label) else "✗"
            covered_style = f"[green]{covered}[/green]" if covered == "✓" else f"[red]{covered}[/red]"
            table2.add_row(label[:60], str(count), req, covered_style)

        console.print(table2)

        # ── Generate custom_answers snippet ──
        if self.unanswered:
            console.print(f"\n{'━' * 70}")
            console.print(f"[bold]📝 ADD THIS TO YOUR profile.yaml (under custom_answers):[/bold]")
            console.print(f"{'━' * 70}\n")
            console.print("custom_answers:")
            for label, count in self.unanswered.most_common():
                safe_key = label.lower()[:60]
                # Check if it has options
                field = next((f for f in self.all_fields if f["label"] == label and f["options"]), None)
                if field and field["options"]:
                    opts = ", ".join(field["options"][:5])
                    console.print(f'  # Options: {opts}')
                console.print(f'  "{safe_key}": ""  # appears in {count} jobs')
            console.print()

        # ── Save full report ──
        report_path = Path("form_scan_report.json")
        report = {
            "jobs_scanned": len(self.by_job),
            "errors": len(self.errors),
            "coverage_pct": round(coverage, 1),
            "required_covered": can_answer,
            "required_missing": cannot_answer,
            "unanswered_fields": [
                {"label": l, "count": c, "suggestion": self._suggest_fix(l)}
                for l, c in self.unanswered.most_common()
            ],
            "answered_fields": [
                {"label": l, "count": c}
                for l, c in self.answered.most_common()
            ],
            "all_fields_by_freq": [
                {"label": l, "count": c, "required": l in self.required_counter}
                for l, c in self.field_counter.most_common()
            ],
            "errors_detail": self.errors,
        }
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        console.print(f"  [dim]Full report saved: {report_path}[/dim]\n")

    def _suggest_fix(self, label: str) -> str:
        """Suggest what to add to profile.yaml for a missing field."""
        lt = label.lower()
        if any(w in lt for w in ["education", "degree", "school", "university"]):
            return "Add education_level, school, degree"
        if any(w in lt for w in ["salary", "compensation", "pay"]):
            return "Add salary_expectation"
        if any(w in lt for w in ["experience", "years"]):
            return "Add years_experience"
        if any(w in lt for w in ["company", "employer"]):
            return "Add current_company"
        if any(w in lt for w in ["title", "role", "position"]):
            return "Add current_title"
        if any(w in lt for w in ["address", "street"]):
            return "Add to custom_answers"
        return "Add to custom_answers"


async def main():
    parser = argparse.ArgumentParser(description="Scan Greenhouse forms for required fields")
    parser.add_argument("--profile", "-p", required=True, help="Path to profile.yaml")
    parser.add_argument("--limit", type=int, default=None, help="Max jobs to scan")
    parser.add_argument("--status", default="saved", help="Scan jobs with this status (default: saved)")
    args = parser.parse_args()

    console.print("\n[bold]🌸 Pollen Form Scanner — Greenhouse[/bold]\n")

    scanner = FormScanner(args.profile)
    await scanner.start()

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            params = {"source": "greenhouse", "status": args.status, "limit": min(args.limit or 200, 200)}
            async with session.get("http://localhost:8000/api/jobs", params=params) as r:
                if r.status != 200:
                    console.print(f"[red]API error {r.status}. Is the server running?[/red]")
                    return
                data = await r.json()
                jobs = data.get("jobs", [])

                if args.limit:
                    jobs = jobs[:args.limit]

                console.print(f"  Scanning {len(jobs)} greenhouse jobs...\n")

                for i, job in enumerate(jobs):
                    console.print(f"  [{i+1}/{len(jobs)}] ", end="")
                    await scanner.scan_job(job["url"], job.get("company_name", ""), job.get("title", ""))
                    await asyncio.sleep(random.uniform(0.3, 0.8))

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        console.print("[dim]Make sure the API is running: uvicorn api.server:app --reload --port 8000[/dim]")

    await scanner.close()
    scanner.print_report()


if __name__ == "__main__":
    asyncio.run(main())
