"""
Greenhouse Auto-Apply.

Uses Playwright to fill out and submit Greenhouse job application forms.
Greenhouse forms follow a standard structure:
  - First name, Last name, Email, Phone
  - Resume upload
  - LinkedIn, Website (optional)
  - Custom questions (dropdown, text, checkbox)
  - Submit button

Usage:
    python auto_apply.py --profile profile.yaml                    # Apply to all saved greenhouse jobs
    python auto_apply.py --profile profile.yaml --job-id 42        # Apply to a specific job
    python auto_apply.py --profile profile.yaml --limit 5 --dry-run  # Test without submitting
"""

import argparse
import asyncio
import json
import os
import time
import random
from pathlib import Path
from typing import Optional

import yaml
from rich.console import Console
from rich.table import Table

console = Console()


class ApplicantProfile:
    """Your personal info for auto-filling applications."""

    def __init__(self, profile_path: str):
        with open(profile_path) as f:
            data = yaml.safe_load(f)

        self.first_name = data.get("first_name", "")
        self.last_name = data.get("last_name", "")
        self.email = data.get("email", "")
        self.phone = data.get("phone", "")
        self.linkedin = data.get("linkedin", "")
        self.website = data.get("website", "")
        self.resume_path = data.get("resume_path", "")
        self.cover_letter_path = data.get("cover_letter_path", "")
        self.location = data.get("location", "")
        self.city = data.get("city", "")
        self.state = data.get("state", "")
        self.country = data.get("country", "United States")
        self.zip_code = data.get("zip_code", "")

        # Education
        self.education_level = data.get("education_level", "")
        self.school = data.get("school", "")
        self.degree = data.get("degree", "")
        self.graduation_year = data.get("graduation_year", "")
        self.major = data.get("major", "")

        # Work
        self.current_company = data.get("current_company", "")
        self.current_title = data.get("current_title", "")
        self.years_experience = data.get("years_experience", "")
        self.management_experience = data.get("management_experience", "")

        # Common questions
        self.work_authorization = data.get("work_authorization", "")
        self.visa_sponsorship = data.get("visa_sponsorship", "")
        self.salary_expectation = data.get("salary_expectation", "")
        self.start_date = data.get("start_date", "")
        self.notice_period = data.get("notice_period", "")
        self.willing_to_relocate = data.get("willing_to_relocate", "Yes")
        self.comfortable_remote = data.get("comfortable_remote", "Yes")
        self.how_did_you_hear = data.get("how_did_you_hear", "Company website")

        # EEO
        self.gender = data.get("gender", "")
        self.race = data.get("race", "")
        self.veteran_status = data.get("veteran_status", "")
        self.disability_status = data.get("disability_status", "")
        self.custom_answers = data.get("custom_answers", {})

    def validate(self):
        missing = []
        if not self.first_name:
            missing.append("first_name")
        if not self.last_name:
            missing.append("last_name")
        if not self.email:
            missing.append("email")
        if not self.resume_path or not Path(self.resume_path).exists():
            missing.append(f"resume_path ({self.resume_path})")
        return missing


class GreenhouseAutoApply:
    """Automates Greenhouse job applications using Playwright."""

    def __init__(self, profile: ApplicantProfile, dry_run: bool = False, use_llm: bool = True, pause_on_missing: bool = False, auto_only: bool = False):
        self.profile = profile
        self.dry_run = dry_run
        self.use_llm = use_llm
        self.pause_on_missing = pause_on_missing
        self.auto_only = auto_only
        self.browser = None
        self.playwright = None
        self.results = []
        self._all_jobs = []
        self.skipped_jobs = []

    async def start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,  # Set True for production, False to watch it work
            slow_mo=100,
        )

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def apply_to_job(self, job_url: str, company_name: str = "", job_title: str = "") -> dict:
        """
        Apply to a single Greenhouse job posting.

        Returns dict with status, message, and any errors.
        """
        result = {
            "url": job_url,
            "company": company_name,
            "title": job_title,
            "status": "pending",
            "message": "",
        }

        try:
            page = await self.browser.new_page()
            page.set_default_timeout(45000)

            console.print(f"\n  [bold]Applying: {job_title}[/bold] at {company_name}")
            console.print(f"  [dim]{job_url}[/dim]")

            # Try to convert company career URLs to direct Greenhouse application URLs
            direct_url = self._get_greenhouse_apply_url(job_url)
            if direct_url != job_url:
                console.print(f"  [dim]Using direct Greenhouse URL[/dim]")

            # Navigate — use domcontentloaded instead of networkidle (much faster)
            try:
                await page.goto(direct_url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                # Fallback to original URL
                if direct_url != job_url:
                    console.print(f"  [dim]Falling back to original URL[/dim]")
                    await page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # Check if there's an "Apply" button to click first
            apply_btn = await page.query_selector(
                'a[href*="application"], button:has-text("Apply"), a:has-text("Apply for this job"), '
                'a:has-text("Apply Now"), a:has-text("Apply now"), a:has-text("Submit Application")'
            )
            if apply_btn:
                console.print("  [dim]Clicking Apply button...[/dim]")
                await apply_btn.click()
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)

            # Check if we're on an application form
            form = await page.query_selector('form, #application, [class*="application"]')
            if not form:
                # Try finding the application iframe
                iframe = await page.query_selector('iframe[src*="greenhouse"], iframe[src*="boards"], iframe[id*="grnhse"]')
                if iframe:
                    frame = await iframe.content_frame()
                    if frame:
                        console.print("  [dim]Switched to Greenhouse iframe[/dim]")
                        page = frame
                        await asyncio.sleep(1)
                        form = await page.query_selector('form')

            # Also check for iframes even if we found a form — the real form might be inside
            if form:
                inner_iframe = await page.query_selector('iframe[src*="greenhouse"], iframe[src*="boards"], iframe[id*="grnhse"]')
                if inner_iframe:
                    inner_frame = await inner_iframe.content_frame()
                    if inner_frame:
                        inner_form = await inner_frame.query_selector('form')
                        if inner_form:
                            console.print("  [dim]Switched to inner Greenhouse iframe[/dim]")
                            page = inner_frame
                            form = inner_form

            if not form:
                result["status"] = "skipped"
                result["message"] = "Could not find application form"
                console.print("  [yellow]⚠ No application form found[/yellow]")
                await page.close()
                return result

            # Scroll to the application form to make sure all fields are visible
            try:
                await form.scroll_into_view_if_needed()
                await asyncio.sleep(1)
                # Also try scrolling to the bottom where forms typically are
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)
                # Scroll back up to the form
                await form.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
            except Exception:
                pass

            # ── Fill standard fields ──
            await self._fill_field(page, "first_name", self.profile.first_name)
            await self._fill_field(page, "last_name", self.profile.last_name)
            await self._fill_field(page, "email", self.profile.email)
            await self._fill_field(page, "phone", self.profile.phone)
            await self._fill_field(page, "location", self.profile.location)

            # Also try filling by label text for fields the name-based approach misses
            await self._fill_by_label(page, "Legal Name", f"{self.profile.first_name} {self.profile.last_name}")
            await self._fill_by_label(page, "Preferred Name", self.profile.first_name)
            await self._fill_by_label(page, "Current Job Title", self.profile.current_title)
            await self._fill_by_label(page, "Most Recent Job Title", self.profile.current_title)
            await self._fill_by_label(page, "Most Recent Employer", self.profile.current_company)
            await self._fill_by_label(page, "Current Employer", self.profile.current_company)
            await self._fill_by_label(page, "name of your current employer", self.profile.current_company)

            # LinkedIn
            if self.profile.linkedin:
                await self._fill_field(page, "linkedin", self.profile.linkedin)
                await self._fill_field(page, "linkedin_profile", self.profile.linkedin)

            # Website
            if self.profile.website:
                await self._fill_field(page, "website", self.profile.website)
                await self._fill_field(page, "portfolio", self.profile.website)

            # ── Upload resume ──
            if self.profile.resume_path and Path(self.profile.resume_path).exists():
                await self._upload_file(page, "resume", self.profile.resume_path)

            # ── Upload cover letter ──
            if self.profile.cover_letter_path and Path(self.profile.cover_letter_path).exists():
                await self._upload_file(page, "cover_letter", self.profile.cover_letter_path)

            # ── Handle custom questions ──
            await self._handle_custom_questions(page)

            # ── Handle EEO fields (optional demographic questions) ──
            await self._handle_eeo_fields(page)

            # ── Audit: Log what fields exist and their state ──
            await self._audit_form(page)

            # Small random delay to seem human
            await asyncio.sleep(random.uniform(0.5, 1.5))

            # ── Check for unfilled required fields ──
            unfilled = await self._check_required_fields(page)
            if unfilled and self.auto_only:
                console.print(f"  [yellow]⏭ Skipping — {len(unfilled)} required field(s) can't be filled: {', '.join(f[:30] for f in unfilled[:3])}[/yellow]")
                result["status"] = "skipped"
                result["message"] = f"Missing required: {', '.join(unfilled[:3])}"
                self.skipped_jobs.append({"company": company_name, "title": job_title, "url": job_url, "missing": unfilled})
                await page.close()
                self.results.append(result)
                return result
            elif unfilled and self.pause_on_missing:
                console.print(f"\n  [yellow]⚠ {len(unfilled)} required field(s) still empty:[/yellow]")
                for field_label in unfilled:
                    console.print(f"    • {field_label}")
                console.print(f"\n  [bold yellow]Browser is open — fill them manually, then press Enter here to continue...[/bold yellow]")
                await asyncio.get_event_loop().run_in_executor(None, input)
                console.print("  [dim]Continuing...[/dim]")
            elif unfilled and not self.pause_on_missing:
                console.print(f"  [yellow]⚠ {len(unfilled)} required field(s) unfilled: {', '.join(f[:30] for f in unfilled[:5])}[/yellow]")
                console.print(f"  [dim]Tip: use --auto-only to skip these, or --pause to fill manually[/dim]")

            # ── Submit or dry-run ──
            if self.dry_run:
                console.print("  [yellow]🔸 DRY RUN — form filled but NOT submitted[/yellow]")

                # Take a screenshot for review
                screenshot_dir = Path("screenshots")
                screenshot_dir.mkdir(exist_ok=True)
                safe_name = "".join(c if c.isalnum() else "_" for c in f"{company_name}_{job_title}")[:60]
                screenshot_path = screenshot_dir / f"{safe_name}.png"
                await page.screenshot(path=str(screenshot_path), full_page=True)
                console.print(f"  [dim]Screenshot saved: {screenshot_path}[/dim]")

                result["status"] = "dry_run"
                result["message"] = f"Form filled, screenshot at {screenshot_path}"
            else:
                # Take PRE-submit screenshot
                screenshot_dir = Path("screenshots")
                screenshot_dir.mkdir(exist_ok=True)
                safe_name = "".join(c if c.isalnum() else "_" for c in f"{company_name}_{job_title}")[:60]
                pre_path = screenshot_dir / f"{safe_name}_PRE.png"
                await page.screenshot(path=str(pre_path), full_page=True)
                console.print(f"  [dim]Pre-submit screenshot: {pre_path}[/dim]")

                # Find and click submit button
                submitted = await self._submit_form(page)
                if submitted:
                    await asyncio.sleep(3)

                    # Take POST-submit screenshot
                    post_path = screenshot_dir / f"{safe_name}_POST.png"
                    await page.screenshot(path=str(post_path), full_page=True)
                    console.print(f"  [dim]Post-submit screenshot: {post_path}[/dim]")

                    # Check for success or error indicators
                    page_text = await page.inner_text("body")
                    page_lower = page_text.lower() if page_text else ""

                    if any(w in page_lower for w in ["thank you", "thanks for applying",
                            "application received", "application submitted", "successfully submitted",
                            "we have received", "application has been", "you have applied"]):
                        result["status"] = "applied"
                        result["message"] = "Application confirmed"
                        console.print("  [green]✓ Application confirmed![/green]")
                        if job_url:
                            await self._update_job_status(job_url, "applied")

                    elif any(w in page_lower for w in ["error", "required field", "please fill",
                              "is required", "can't be blank", "cannot be blank",
                              "please complete", "missing required"]):
                        result["status"] = "error"
                        result["message"] = "Form validation error — check screenshot"
                        console.print(f"  [red]✗ Form has errors — check {post_path}[/red]")

                    else:
                        # Unclear — might have worked, might not
                        result["status"] = "submitted"
                        result["message"] = f"Submitted but unconfirmed — check {post_path}"
                        console.print(f"  [yellow]⚠ Submitted but no confirmation detected — check {post_path}[/yellow]")
                        if job_url:
                            await self._update_job_status(job_url, "applied")
                else:
                    result["status"] = "error"
                    result["message"] = "Could not find submit button"
                    console.print("  [red]✗ Could not find submit button[/red]")

            await asyncio.sleep(1)
            await page.close()

        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
            console.print(f"  [red]✗ Error: {e}[/red]")

        self.results.append(result)
        return result

    async def _fill_field(self, page, field_name: str, value: str):
        """Try multiple selectors to fill a form field."""
        if not value:
            return

        selectors = [
            f'input[name*="{field_name}" i]',
            f'input[id*="{field_name}" i]',
            f'input[placeholder*="{field_name}" i]',
            f'input[aria-label*="{field_name}" i]',
            f'textarea[name*="{field_name}" i]',
            f'textarea[id*="{field_name}" i]',
        ]

        # Greenhouse-specific selectors
        gh_map = {
            "first_name": ['input[name="job_application[first_name]"]', '#first_name'],
            "last_name": ['input[name="job_application[last_name]"]', '#last_name'],
            "email": ['input[name="job_application[email]"]', '#email', 'input[type="email"]'],
            "phone": ['input[name="job_application[phone]"]', '#phone', 'input[type="tel"]'],
            "location": ['input[name="job_application[location]"]', '#job_application_location'],
            "linkedin": ['input[name*="linkedin" i]', 'input[placeholder*="LinkedIn" i]'],
            "linkedin_profile": ['input[name*="linkedin" i]'],
            "website": ['input[name*="website" i]', 'input[placeholder*="Website" i]'],
            "portfolio": ['input[name*="portfolio" i]'],
        }

        all_selectors = gh_map.get(field_name, []) + selectors

        for selector in all_selectors:
            try:
                el = await page.query_selector(selector)
                if el:
                    visible = await el.is_visible()
                    if visible:
                        await el.click()
                        await el.fill("")
                        await el.type(value, delay=random.randint(20, 50))
                        console.print(f"  [dim]✓ Filled {field_name}[/dim]")
                        return
            except Exception:
                continue

    async def _fill_by_label(self, page, label_text: str, value: str):
        """Find an input by its label text and fill it."""
        if not value:
            return
        try:
            # Find all labels containing the text
            labels = await page.query_selector_all('label')
            for lbl in labels:
                text = await lbl.inner_text()
                if label_text.lower() in (text or '').lower():
                    # Found the label — now find its input
                    for_attr = await lbl.get_attribute('for')
                    if for_attr:
                        inp = await page.query_selector(f'#{for_attr}')
                        if inp:
                            visible = await inp.is_visible()
                            current = await inp.evaluate("el => el.value")
                            if visible and not (current and current.strip()):
                                await inp.fill(str(value))
                                console.print(f"  [dim]✓ Filled by label: {label_text[:30]} → {str(value)[:20]}[/dim]")
                                return

                    # Try sibling/child input
                    parent = await lbl.evaluate_handle("el => el.closest('.field, .form-group, fieldset, div')")
                    if parent:
                        inp = await parent.query_selector('input[type="text"], input:not([type]), textarea')
                        if inp:
                            visible = await inp.is_visible()
                            current = await inp.evaluate("el => el.value")
                            if visible and not (current and current.strip()):
                                await inp.fill(str(value))
                                console.print(f"  [dim]✓ Filled by label: {label_text[:30]} → {str(value)[:20]}[/dim]")
                                return
        except Exception:
            pass

    async def _upload_file(self, page, field_type: str, file_path: str):
        """Upload resume or cover letter."""
        selectors = [
            f'input[type="file"][name*="{field_type}" i]',
            f'input[type="file"][id*="{field_type}" i]',
            f'input[type="file"][data-field*="{field_type}" i]',
            'input[type="file"]',  # Fallback: first file input
        ]

        if field_type == "resume":
            selectors = [
                'input[type="file"][name*="resume" i]',
                'input[type="file"][id*="resume" i]',
                'input[type="file"][name="job_application[resume]"]',
                '#resume_file_input',
                'input[type="file"]',
            ]
        elif field_type == "cover_letter":
            selectors = [
                'input[type="file"][name*="cover" i]',
                'input[type="file"][id*="cover" i]',
                'input[type="file"][name="job_application[cover_letter]"]',
            ]

        for selector in selectors:
            try:
                el = await page.query_selector(selector)
                if el:
                    await el.set_input_files(file_path)
                    console.print(f"  [dim]✓ Uploaded {field_type}: {Path(file_path).name}[/dim]")
                    return
            except Exception:
                continue

        # Try clicking an upload button/area first
        upload_btn = await page.query_selector(
            f'button:has-text("{field_type}"), a:has-text("Attach"), '
            f'[class*="upload"], [class*="dropzone"]'
        )
        if upload_btn:
            try:
                async with page.expect_file_chooser() as fc_info:
                    await upload_btn.click()
                file_chooser = await fc_info.value
                await file_chooser.set_files(file_path)
                console.print(f"  [dim]✓ Uploaded {field_type} via chooser[/dim]")
            except Exception:
                console.print(f"  [yellow]⚠ Could not upload {field_type}[/yellow]")

    async def _handle_custom_questions(self, page):
        """Answer all custom questions on the form — more aggressive field finding."""

        # ── Pass 1: Fill all selects (dropdowns) ──
        selects = await page.query_selector_all('select')
        for sel in selects:
            try:
                value = await sel.evaluate("el => el.value")
                if value and value.strip():
                    continue  # Already has a value

                label = await sel.evaluate("""el => {
                    const id = el.id || '';
                    let label = '';
                    if (id) { const lbl = document.querySelector('label[for="' + id + '"]'); if (lbl) label = lbl.textContent; }
                    if (!label) { const p = el.closest('.field, .form-group, fieldset, div'); if (p) { const l = p.querySelector('label,legend'); if (l) label = l.textContent; }}
                    return (label || el.name || '').trim();
                }""")
                if not label:
                    continue

                answer = self._match_custom_answer(label) or self._default_answer(label)
                if answer:
                    await self._select_option(sel, answer)
                    console.print(f"  [dim]✓ Selected: {label[:40]} → {answer[:20]}[/dim]")
                else:
                    # Try first non-empty option as fallback for things like country
                    options = await sel.query_selector_all('option')
                    for opt in options[1:]:  # skip first (usually blank/placeholder)
                        opt_val = await opt.get_attribute("value")
                        if opt_val and opt_val.strip():
                            await sel.select_option(index=1)
                            console.print(f"  [dim]✓ Selected first option: {label[:40]}[/dim]")
                            break
            except Exception:
                continue

        # ── Pass 1b: Re-check required selects that are still empty ──
        selects2 = await page.query_selector_all('select[required], select[aria-required="true"]')
        for sel in selects2:
            try:
                value = await sel.evaluate("el => el.value")
                if value and value.strip():
                    continue  # Filled now

                label = await sel.evaluate("""el => {
                    const id = el.id || '';
                    let label = '';
                    if (id) { const lbl = document.querySelector('label[for="' + id + '"]'); if (lbl) label = lbl.textContent; }
                    if (!label) { const p = el.closest('.field, .form-group, fieldset, div'); if (p) { const l = p.querySelector('label,legend'); if (l) label = l.textContent; }}
                    return (label || el.name || '').trim();
                }""")

                ll = (label or '').lower()

                # For Yes/No style questions, try selecting by label text
                answer = self._match_custom_answer(label) or self._default_answer(label)

                # Get all option texts
                opt_texts = await sel.evaluate("""el => Array.from(el.options).map(o => ({ text: o.text.trim(), value: o.value, index: o.index })).filter(o => o.text !== '' && o.text !== '--')""")

                if answer:
                    al = answer.lower()
                    for opt in opt_texts:
                        ot = opt['text'].lower()
                        if al == ot or al in ot or ot.startswith(al):
                            await sel.select_option(index=opt['index'])
                            console.print(f"  [dim]✓ Re-selected: {label[:35]} → {opt['text'][:25]}[/dim]")
                            break
                    else:
                        # Still not matched — select first real option as last resort
                        if opt_texts:
                            await sel.select_option(index=opt_texts[0]['index'])
                            console.print(f"  [dim]✓ Fallback select: {label[:35]} → {opt_texts[0]['text'][:25]}[/dim]")

            except Exception:
                continue

        # ── Pass 2: Fill all text inputs and textareas ──
        inputs = await page.query_selector_all('input[type="text"], input[type="number"], input[type="url"], input:not([type]), textarea')
        for inp in inputs:
            try:
                visible = await inp.is_visible()
                if not visible:
                    continue
                value = await inp.evaluate("el => el.value")
                if value and value.strip():
                    continue

                label = await inp.evaluate("""el => {
                    const id = el.id || '';
                    let label = '';
                    if (id) { const lbl = document.querySelector('label[for="' + id + '"]'); if (lbl) label = lbl.textContent; }
                    if (!label) { const p = el.closest('.field, .form-group, fieldset, div, li'); if (p) { const l = p.querySelector('label,legend,.label'); if (l) label = l.textContent; }}
                    return (label || el.placeholder || el.name || '').trim();
                }""")
                if not label or len(label) < 3:
                    continue

                # Skip standard fields already handled
                ll = label.lower()
                if any(w in ll for w in ['first name', 'last name', 'email', 'phone']):
                    continue

                answer = self._match_custom_answer(label) or self._default_answer(label)
                if answer:
                    await inp.fill(str(answer))
                    console.print(f"  [dim]✓ Filled: {label[:40]} → {str(answer)[:20]}[/dim]")
            except Exception:
                continue

        # ── Pass 3: Handle checkboxes (agreements, NDAs, acknowledgements) ──
        checkboxes = await page.query_selector_all('input[type="checkbox"]')
        for cb in checkboxes:
            try:
                visible = await cb.is_visible()
                if not visible:
                    continue
                checked = await cb.is_checked()
                if checked:
                    continue

                label = await cb.evaluate("""el => {
                    const id = el.id || '';
                    let label = '';
                    if (id) { const lbl = document.querySelector('label[for="' + id + '"]'); if (lbl) label = lbl.textContent; }
                    if (!label) { const p = el.closest('.field, .form-group, fieldset, div, li, label'); if (p) label = p.textContent; }
                    return (label || '').trim();
                }""")
                ll = (label or '').lower()

                # Auto-check agreements, NDAs, privacy notices, acknowledgements
                if any(w in ll for w in ['acknowledge', 'agree', 'consent', 'confirm', 'accept',
                                          'i have read', 'nda', 'privacy', 'terms', 'review',
                                          'candidate privacy', 'non-disclosure']):
                    await cb.check()
                    console.print(f"  [dim]✓ Checked: {label[:50]}[/dim]")
            except Exception:
                continue

        # ── Pass 4: Handle radio buttons ──
        # Find all radio groups by name
        radio_groups = await page.evaluate("""() => {
            const radios = document.querySelectorAll('input[type="radio"]');
            const groups = {};
            radios.forEach(r => {
                if (!r.name) return;
                if (!groups[r.name]) groups[r.name] = [];
                const parent = r.closest('.field, .form-group, fieldset, div');
                const groupLabel = parent ? (parent.querySelector('label, legend, .label') || {}).textContent || '' : '';
                const optLabel = r.closest('label') ? r.closest('label').textContent.trim() : r.value;
                groups[r.name].push({ value: r.value, label: optLabel, groupLabel: groupLabel.trim(), checked: r.checked });
            });
            return groups;
        }""")

        for name, options in (radio_groups or {}).items():
            try:
                if any(o.get("checked") for o in options):
                    continue  # Already answered

                group_label = options[0].get("groupLabel", "") if options else ""
                gl = group_label.lower()

                answer = self._match_custom_answer(group_label) or self._default_answer(group_label)
                if not answer:
                    # Default logic for common radio patterns
                    if any(w in gl for w in ["currently or have you ever", "previously employed",
                                              "former employee", "ever worked at"]):
                        answer = "No"
                    elif any(w in gl for w in ["authorized", "eligible", "legally"]):
                        answer = "Yes"
                    elif any(w in gl for w in ["sponsor", "visa", "immigration"]):
                        answer = "Yes"
                    elif any(w in gl for w in ["relocat", "willing to", "comfortable"]):
                        answer = "Yes"
                    elif any(w in gl for w in ["in-person", "hybrid", "on-site", "onsite", "office"]):
                        answer = "Yes"

                if answer:
                    # Find the matching option
                    al = answer.lower()
                    matched = False
                    for opt in options:
                        ol = opt.get("label", "").lower()
                        if al in ol or ol in al or opt.get("value", "").lower() == al:
                            await page.click(f'input[type="radio"][name="{name}"][value="{opt["value"]}"]')
                            console.print(f"  [dim]✓ Radio: {group_label[:35]} → {opt["label"][:20]}[/dim]")
                            matched = True
                            break
                    if not matched and options:
                        # Fallback: click first option for "Yes/No" type questions
                        for opt in options:
                            if opt.get("label", "").lower() in ["yes", "no", "true", "false"]:
                                if ("yes" in al or al == "true"):
                                    if opt.get("label", "").lower() in ["yes", "true"]:
                                        await page.click(f'input[type="radio"][name="{name}"][value="{opt["value"]}"]')
                                        console.print(f"  [dim]✓ Radio: {group_label[:35]} → {opt["label"]}[/dim]")
                                        break
                                elif ("no" in al or al == "false"):
                                    if opt.get("label", "").lower() in ["no", "false"]:
                                        await page.click(f'input[type="radio"][name="{name}"][value="{opt["value"]}"]')
                                        console.print(f"  [dim]✓ Radio: {group_label[:35]} → {opt["label"]}[/dim]")
                                        break
            except Exception:
                continue

    def _match_custom_answer(self, label_text: str) -> Optional[str]:
        """Match a question label against profile custom_answers."""
        for key, value in self.profile.custom_answers.items():
            if key.lower() in label_text or label_text in key.lower():
                return str(value)
        return None

    def _default_answer(self, label_text: str) -> Optional[str]:
        """Provide default answers for common application questions."""
        lt = label_text.lower()

        # Work authorization
        if any(w in lt for w in ["authorized to work", "work authorization", "eligible to work", "legally authorized", "right to work"]):
            return self.profile.work_authorization or "Yes"

        # Visa sponsorship
        if any(w in lt for w in ["visa sponsorship", "sponsor", "immigration sponsorship", "require sponsorship"]):
            return self.profile.visa_sponsorship or "Yes"

        # Salary
        if any(w in lt for w in ["salary", "compensation", "pay expectation", "desired salary", "expected salary"]):
            return self.profile.salary_expectation or ""

        # Start date
        if any(w in lt for w in ["start date", "when can you start", "available to start", "earliest start", "availability"]):
            return self.profile.start_date or "Immediately"

        # Notice period
        if any(w in lt for w in ["notice period", "current notice"]):
            return self.profile.notice_period or "2 weeks"

        # Years of experience
        if any(w in lt for w in ["years of experience", "years experience", "how many years", "total experience"]):
            return self.profile.years_experience or ""

        # Management experience
        if any(w in lt for w in ["management experience", "people management", "direct reports", "managed a team"]):
            return self.profile.management_experience or ""

        # Education level
        if any(w in lt for w in ["education level", "highest degree", "highest level of education", "education"]):
            return self.profile.education_level or ""

        # School / University
        if any(w in lt for w in ["school", "university", "college", "institution"]):
            return self.profile.school or ""

        # Degree
        if any(w in lt for w in ["degree", "what degree", "type of degree"]):
            return self.profile.degree or ""

        # Major / Field of study
        if any(w in lt for w in ["major", "field of study", "area of study", "discipline"]):
            return self.profile.major or ""

        # Graduation year
        if any(w in lt for w in ["graduation year", "year of graduation", "grad year", "when did you graduate"]):
            return self.profile.graduation_year or ""

        # Current company
        if any(w in lt for w in ["current company", "current employer", "most recent company", "present employer"]):
            return self.profile.current_company or ""

        # Current title
        if any(w in lt for w in ["current title", "current role", "current position", "job title", "most recent title"]):
            return self.profile.current_title or ""

        # How did you hear
        if any(w in lt for w in ["how did you hear", "where did you hear", "how did you find", "referral source", "how did you learn"]):
            return self.profile.how_did_you_hear or "Company website"

        # Relocation
        if any(w in lt for w in ["willing to relocate", "relocation", "open to relocating"]):
            return self.profile.willing_to_relocate or "Yes"

        # Remote
        if any(w in lt for w in ["comfortable working remotely", "remote work", "work remotely"]):
            return self.profile.comfortable_remote or "Yes"

        # Location / City / State / Country
        if any(w in lt for w in ["city", "what city"]):
            return self.profile.city or ""
        if any(w in lt for w in ["state", "province"]):
            return self.profile.state or ""
        if any(w in lt for w in ["country"]):
            return self.profile.country or "United States"
        if any(w in lt for w in ["zip", "postal code"]):
            return self.profile.zip_code or ""

        # Age verification
        if any(w in lt for w in ["18 years", "age or older", "legal age"]):
            return "Yes"

        # LinkedIn (sometimes asked as a text field)
        if "linkedin" in lt:
            return self.profile.linkedin or ""

        # Acknowledgements / Agreements — always say yes/agree
        if any(w in lt for w in ["acknowledge", "agree", "consent", "confirm", "accept",
                                  "i have read", "nda", "privacy notice", "terms"]):
            return "Yes"

        # Previously employed at company — use text that fuzzy-matches long dropdown options
        if any(w in lt for w in ["previously employed", "ever been employed",
                                  "currently or have you ever", "former employee"]):
            return "I have never been employed"

        # Hispanic/Latino
        if any(w in lt for w in ["hispanic", "latino"]):
            return "No"

        # Discipline / field of study
        if any(w in lt for w in ["discipline", "field of study", "area of study", "concentration"]):
            return self.profile.major or ""

        return None

    async def _handle_eeo_fields(self, page):
        """Handle Equal Employment Opportunity demographic fields using Greenhouse-specific selectors."""
        # Gender dropdown
        if self.profile.gender:
            try:
                sel = await page.query_selector('#job_application_gender, select[name*="gender"]')
                if sel:
                    await self._select_option(sel, self.profile.gender)
                    console.print(f"  [dim]✓ EEO: Gender → {self.profile.gender}[/dim]")
            except Exception:
                pass

        # Hispanic/Latino
        try:
            sel = await page.query_selector('#job_application_hispanic_ethnicity, select[name*="hispanic"]')
            if sel:
                await self._select_option(sel, "No")
                console.print(f"  [dim]✓ EEO: Hispanic → No[/dim]")
        except Exception:
            pass

        # Race
        if self.profile.race:
            try:
                sel = await page.query_selector('#job_application_race, select[name*="race"]')
                if sel:
                    await self._select_option(sel, self.profile.race)
                    console.print(f"  [dim]✓ EEO: Race → {self.profile.race}[/dim]")
            except Exception:
                pass

        # Veteran
        if self.profile.veteran_status:
            try:
                sel = await page.query_selector('#job_application_veteran_status, select[name*="veteran"]')
                if sel:
                    await self._select_option(sel, self.profile.veteran_status)
                    console.print(f"  [dim]✓ EEO: Veteran → {self.profile.veteran_status[:30]}[/dim]")
            except Exception:
                pass

        # Disability
        if self.profile.disability_status:
            try:
                sel = await page.query_selector('#job_application_disability_status, select[name*="disability"]')
                if sel:
                    await self._select_option(sel, self.profile.disability_status)
                    console.print(f"  [dim]✓ EEO: Disability → {self.profile.disability_status[:30]}[/dim]")
            except Exception:
                pass

    async def _audit_form(self, page):
        """Log all form fields and whether they're filled — helps debug submission errors."""
        try:
            audit = await page.evaluate("""() => {
                const results = [];
                const els = document.querySelectorAll('input, select, textarea');
                els.forEach(el => {
                    if (el.type === 'hidden' || el.offsetParent === null) return;
                    const tag = el.tagName.toLowerCase();
                    const type = el.type || tag;
                    const value = el.value || '';
                    const checked = el.checked || false;
                    const required = el.required || el.getAttribute('aria-required') === 'true';
                    let label = '';
                    const id = el.id;
                    if (id) { const lbl = document.querySelector('label[for="' + id + '"]'); if (lbl) label = lbl.textContent.trim(); }
                    if (!label) { const p = el.closest('.field,.form-group,fieldset,div'); if (p) { const l = p.querySelector('label,legend'); if (l) label = l.textContent.trim(); }}
                    if (!label) label = el.placeholder || el.name || '';
                    label = label.replace(/\\*/g, '').trim().substring(0, 50);
                    const filled = (type === 'checkbox' || type === 'radio') ? checked : (value.trim() !== '');
                    results.push({ label, type, required, filled, value: value.substring(0, 30) });
                });
                return results;
            }""")

            empty_required = [f for f in audit if f.get("required") and not f.get("filled")]
            if empty_required:
                console.print(f"  [yellow]⚠ AUDIT: {len(empty_required)} empty required field(s):[/yellow]")
                for f in empty_required:
                    console.print(f"    [red]✗ {f['label'][:45]}[/red] ({f['type']})")
            else:
                filled_count = sum(1 for f in audit if f.get("filled"))
                console.print(f"  [green]✓ AUDIT: All required fields filled ({filled_count} total fields)[/green]")

        except Exception as e:
            console.print(f"  [dim]Audit error: {e}[/dim]")

    async def _check_required_fields(self, page) -> list[str]:
        """Find required fields that are still empty AND we can't answer. Returns list of label texts."""
        unfilled = []
        try:
            required_elements = await page.query_selector_all(
                '[required], [aria-required="true"]'
            )

            for el in required_elements:
                try:
                    info = await el.evaluate("""el => {
                        const tag = el.tagName.toLowerCase();
                        const type = el.type || tag;
                        if (type === 'hidden') return null;
                        if (el.offsetParent === null) return null;

                        const value = el.value || '';
                        if (value.trim() !== '') return null;  // Already filled

                        // Get label
                        let label = '';
                        const id = el.id;
                        if (id) { const lbl = document.querySelector('label[for="' + id + '"]'); if (lbl) label = lbl.textContent; }
                        if (!label) {
                            const parent = el.closest('.field, .form-group, fieldset, div');
                            if (parent) { const lbl = parent.querySelector('label, legend'); if (lbl) label = lbl.textContent; }
                        }
                        if (!label) label = el.placeholder || el.name || '';

                        return { label: label.trim().replace(/\\*/g, '').trim(), type, tag };
                    }""")

                    if not info:
                        continue

                    label = info.get("label", "")
                    if not label or len(label) < 3:
                        continue

                    # Check if our profile CAN answer this — if yes, don't count it as unfilled
                    if self._profile_can_answer(label):
                        continue

                    unfilled.append(label[:60])

                except Exception:
                    continue
        except Exception:
            pass

        # Deduplicate
        seen = set()
        result = []
        for label in unfilled:
            if label not in seen:
                seen.add(label)
                result.append(label)
        return result

    def _profile_can_answer(self, label: str) -> bool:
        """Check if our profile/default_answer logic can handle this field."""
        lt = label.lower()

        # Standard fields we always fill
        if any(w in lt for w in ["first name", "last name", "email", "phone", "resume",
                                  "linkedin", "website", "portfolio", "location", "file"]):
            return True

        # Education
        if any(w in lt for w in ["school", "university", "college", "degree", "education",
                                  "major", "discipline", "field of study", "graduation"]):
            return bool(self.profile.school or self.profile.education_level or self.profile.degree or self.profile.major)

        # Work info
        if any(w in lt for w in ["current company", "current employer", "most recent employer",
                                  "name of your current", "current organization"]):
            return bool(self.profile.current_company)
        if any(w in lt for w in ["current title", "current role", "job title", "current position",
                                  "current job title", "most recent job title", "most recent title"]):
            return bool(self.profile.current_title)
        if any(w in lt for w in ["years of experience", "years experience", "how many years"]):
            return bool(self.profile.years_experience)

        # Work authorization / visa
        if any(w in lt for w in ["authorized to work", "work authorization", "legally authorized",
                                  "eligible to work", "right to work", "currently authorized",
                                  "will you now", "require sponsorship", "visa", "sponsor",
                                  "immigration"]):
            return True  # We always answer these

        # Location
        if any(w in lt for w in ["country", "city", "state", "zip", "postal", "address"]):
            return bool(self.profile.location or self.profile.country)

        # Salary
        if any(w in lt for w in ["salary", "compensation", "pay"]):
            return bool(self.profile.salary_expectation)

        # Start date
        if any(w in lt for w in ["start date", "when can you start", "available", "notice period"]):
            return True

        # How did you hear
        if any(w in lt for w in ["how did you hear", "how did you find", "how did you first learn",
                                  "how did you learn", "referral", "source"]):
            return True

        # Relocation / Remote
        if any(w in lt for w in ["relocat", "remote", "in-person", "hybrid", "on-site", "onsite",
                                  "office", "believes in-person"]):
            return True

        # Acknowledgements / Agreements / NDAs / Privacy
        if any(w in lt for w in ["acknowledge", "agree", "consent", "confirm", "accept",
                                  "i have read", "nda", "privacy", "terms", "review and",
                                  "please review"]):
            return True

        # Previously employed / former employee
        if any(w in lt for w in ["previously employed", "ever been employed", "currently or have you ever",
                                  "former employee", "current or former", "ever worked"]):
            return True

        # Age
        if any(w in lt for w in ["18 years", "legal age"]):
            return True

        # EEO
        if any(w in lt for w in ["gender", "race", "ethnic", "hispanic", "veteran", "disability",
                                  "sexual orientation"]):
            return True  # Optional, we handle these

        # Preferred name
        if any(w in lt for w in ["preferred name", "nickname"]):
            return bool(self.profile.first_name)

        # Legal name
        if any(w in lt for w in ["legal name"]):
            return bool(self.profile.first_name)

        # Security code / captcha — we can't do these
        if any(w in lt for w in ["security code", "captcha", "verification code"]):
            return False

        # Check custom_answers
        custom = self.profile.custom_answers or {}
        for key in custom:
            if key.lower() in lt or lt in key.lower():
                return True

        return False

    async def _select_by_label(self, page, field_hint: str, value: str):
        """Select a dropdown/radio option by label text."""
        try:
            selects = await page.query_selector_all('select')
            for sel in selects:
                name = (await sel.get_attribute("name") or "").lower()
                id_attr = (await sel.get_attribute("id") or "").lower()
                if field_hint in name or field_hint in id_attr:
                    await self._select_option(sel, value)
                    return
        except Exception:
            pass

    async def _select_option(self, select_el, value: str):
        """Select an option from a dropdown, trying exact then fuzzy match."""
        try:
            options = await select_el.query_selector_all('option')
            best_match = None
            best_score = 0

            for opt in options:
                opt_text = (await opt.inner_text()).strip()
                opt_value = await opt.get_attribute("value")

                # Skip empty/placeholder options
                if not opt_text or opt_text in ('', '--', 'Select', 'Select...', 'Please select', 'Choose...'):
                    continue

                # Exact match on text or value
                if opt_text.lower() == value.lower() or (opt_value and opt_value.lower() == value.lower()):
                    try:
                        await select_el.select_option(label=opt_text)
                    except Exception:
                        try:
                            await select_el.select_option(value=opt_value)
                        except Exception:
                            await select_el.select_option(index=await opt.evaluate("el => el.index"))
                    return

                # Fuzzy: value contained in option text or vice versa
                vl = value.lower()
                ol = opt_text.lower()
                if vl in ol or ol in vl:
                    score = len(vl) / max(len(ol), 1)
                    if score > best_score:
                        best_score = score
                        best_match = (opt_text, opt_value, opt)

            if best_match:
                opt_text, opt_value, opt = best_match
                try:
                    await select_el.select_option(label=opt_text)
                except Exception:
                    try:
                        await select_el.select_option(value=opt_value)
                    except Exception:
                        try:
                            idx = await opt.evaluate("el => el.index")
                            await select_el.select_option(index=idx)
                        except Exception:
                            pass
        except Exception:
            pass

    async def _submit_form(self, page) -> bool:
        """Find and click the submit button."""
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Submit Application")',
            'button:has-text("Apply")',
            'input[value="Submit Application"]',
            '#submit_app',
        ]
        for sel in submit_selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    return True
            except Exception:
                continue
        return False

    def _get_greenhouse_apply_url(self, url: str) -> str:
        """Convert a company career page URL to a direct Greenhouse application URL."""
        import re

        # Already a Greenhouse URL
        if "greenhouse.io" in url or "boards.greenhouse" in url:
            # Make sure it points to the application page
            if "/jobs/" in url and "#app" not in url:
                return url + "#app"
            return url

        # Extract gh_jid parameter from URL
        gh_match = re.search(r'gh_jid=(\d+)', url)
        if gh_match:
            job_id = gh_match.group(1)
            return f"https://boards.greenhouse.io/embed/job_app?token={job_id}"

        # Extract job ID from common URL patterns
        job_id_match = re.search(r'/jobs?/(\d+)', url)
        if job_id_match:
            job_id = job_id_match.group(1)
            return f"https://boards.greenhouse.io/embed/job_app?token={job_id}"

        return url

    async def _update_job_status(self, job_url: str, status: str):
        """Update job status in the database via API."""
        try:
            import aiohttp
            # Find job ID by URL
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://localhost:8000/api/jobs?limit=200") as r:
                    if r.status == 200:
                        data = await r.json()
                        for job in data.get("jobs", []):
                            if job.get("url") == job_url:
                                async with session.post(
                                    f"http://localhost:8000/api/jobs/{job['id']}/status",
                                    json={"status": status}
                                ) as sr:
                                    if sr.status == 200:
                                        console.print(f"  [dim]✓ Status updated to '{status}'[/dim]")
                                return
        except Exception as e:
            # Fallback: update directly via SQLite
            try:
                import sqlite3
                conn = sqlite3.connect("jobs.db")
                c = conn.cursor()
                c.execute("UPDATE jobs SET status = ? WHERE url = ?", (status, job_url))
                conn.commit()
                conn.close()
                if c.rowcount:
                    console.print(f"  [dim]✓ Status updated to '{status}'[/dim]")
            except Exception:
                pass

    def print_results(self):
        """Print a summary table of all application results."""
        table = Table(title="Auto-Apply Results")
        table.add_column("Company", style="cyan")
        table.add_column("Title", style="white")
        table.add_column("Status", style="bold")
        table.add_column("Message", style="dim")

        for r in self.results:
            status_style = {
                "applied": "[green]✓ Applied[/green]",
                "dry_run": "[yellow]🔸 Dry run[/yellow]",
                "skipped": "[dim]⏭ Skipped[/dim]",
                "error": "[red]✗ Error[/red]",
            }.get(r["status"], r["status"])

            table.add_row(
                r.get("company", ""),
                r.get("title", "")[:50],
                status_style,
                r.get("message", "")[:60],
            )

        console.print(table)
        applied = sum(1 for r in self.results if r["status"] == "applied")
        skipped = sum(1 for r in self.results if r["status"] == "skipped")
        errors = sum(1 for r in self.results if r["status"] == "error")
        console.print(f"\n  Total: {len(self.results)} | [green]Applied: {applied}[/green] | [yellow]Skipped: {skipped}[/yellow] | [red]Errors: {errors}[/red]")

        # Show skipped jobs detail
        if self.skipped_jobs:
            console.print(f"\n[bold yellow]⏭ SKIPPED JOBS — need manual apply or profile updates:[/bold yellow]")
            skip_table = Table(show_header=True, header_style="bold")
            skip_table.add_column("Company", style="cyan")
            skip_table.add_column("Title", style="white")
            skip_table.add_column("Missing fields", style="yellow")
            for sj in self.skipped_jobs:
                skip_table.add_row(
                    sj["company"],
                    sj["title"][:45],
                    ", ".join(sj["missing"][:3])
                )
            console.print(skip_table)
            console.print(f"\n  [dim]Tip: add these fields to profile.yaml custom_answers, then re-run[/dim]")


async def main():
    parser = argparse.ArgumentParser(description="Greenhouse Auto-Apply")
    parser.add_argument("--profile", "-p", required=True, help="Path to profile.yaml with your info")
    parser.add_argument("--job-id", type=int, help="Apply to a specific job ID from the database")
    parser.add_argument("--limit", type=int, default=5, help="Max jobs to apply to (default: 5)")
    parser.add_argument("--status", default="saved", help="Apply to jobs with this status (default: saved)")
    parser.add_argument("--dry-run", action="store_true", help="Fill forms but don't submit")
    parser.add_argument("--pause", action="store_true", help="Pause on unfilled required fields so you can fill them manually")
    parser.add_argument("--auto-only", action="store_true", help="Skip jobs that have required fields the profile can't fill")
    parser.add_argument("--url", type=str, help="Apply to a specific job URL directly")
    args = parser.parse_args()

    # Load profile
    profile = ApplicantProfile(args.profile)
    missing = profile.validate()
    if missing:
        console.print(f"[red]Missing required fields: {', '.join(missing)}[/red]")
        console.print("Edit your profile.yaml to add the missing info.")
        return

    console.print("\n[bold]🌸 Pollen Auto-Apply — Greenhouse[/bold]")
    console.print(f"  Profile: {profile.first_name} {profile.last_name} ({profile.email})")
    console.print(f"  Resume: {profile.resume_path}")
    if args.dry_run:
        console.print("  [yellow]Mode: DRY RUN (forms filled but not submitted)[/yellow]")

    applier = GreenhouseAutoApply(profile, dry_run=args.dry_run, pause_on_missing=args.pause, auto_only=args.auto_only)
    await applier.start()

    try:
        if args.url:
            # Apply to a specific URL
            await applier.apply_to_job(args.url, "Direct", "Manual Application")
        else:
            # Get jobs from database
            import aiohttp
            async with aiohttp.ClientSession() as session:
                params = {"source": "greenhouse", "status": args.status, "limit": args.limit}
                if args.job_id:
                    # Fetch specific job
                    async with session.get(f"http://localhost:8000/api/jobs/{args.job_id}") as r:
                        if r.status == 200:
                            job = await r.json()
                            await applier.apply_to_job(job["url"], job["company_name"], job["title"])
                        else:
                            console.print(f"[red]Job ID {args.job_id} not found[/red]")
                else:
                    # Fetch jobs by status
                    async with session.get(
                        f"http://localhost:8000/api/jobs",
                        params={"source": "greenhouse", "status": args.status, "limit": args.limit}
                    ) as r:
                        if r.status == 200:
                            data = await r.json()
                            jobs = data.get("jobs", [])
                            console.print(f"\n  Found {len(jobs)} '{args.status}' greenhouse jobs\n")

                            for job in jobs:
                                await applier.apply_to_job(
                                    job["url"],
                                    job["company_name"],
                                    job["title"],
                                )
                                # Delay between applications
                                if not args.dry_run:
                                    delay = random.uniform(3, 8)
                                    console.print(f"  [dim]Waiting {delay:.0f}s...[/dim]")
                                    await asyncio.sleep(delay)
                        else:
                            console.print(f"[red]API error: {r.status}[/red]")

    finally:
        await applier.close()

    applier.print_results()


if __name__ == "__main__":
    asyncio.run(main())
