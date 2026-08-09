"""
Pollen Onboarding — Resume-driven setup.

Upload a resume → Claude extracts your profile → generates profile.yaml
→ detects target roles → configures job search → ready to crawl & apply.

Usage:
    python onboard.py --resume path/to/resume.pdf
    python onboard.py --resume resume.txt --apply  # Also start applying
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

console = Console()


class ResumeParser:
    """Uses Claude to extract structured profile data from a resume."""

    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            try:
                from config.settings import settings
                self.api_key = settings.llm.api_key
            except Exception:
                pass

        if not self.api_key:
            # Try .env file
            env_path = Path(".env")
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("ANTHROPIC_API_KEY="):
                        self.api_key = line.split("=", 1)[1].strip().strip('"').strip("'")

    def read_resume(self, path: str) -> str:
        """Read resume content from file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Resume not found: {path}")

        if p.suffix.lower() == ".pdf":
            return self._read_pdf(path)
        elif p.suffix.lower() in (".docx", ".doc"):
            return self._read_docx(path)
        else:
            return p.read_text(errors="ignore")

    def _read_pdf(self, path: str) -> str:
        """Extract text from PDF."""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(path)
            text = ""
            for page in doc:
                text += page.get_text()
            return text
        except ImportError:
            pass

        try:
            from pdfminer.high_level import extract_text
            return extract_text(path)
        except ImportError:
            pass

        # Fallback: basic extraction
        with open(path, "rb") as f:
            content = f.read()
        text = content.decode("latin-1", errors="ignore")
        matches = re.findall(r'\(([^)]+)\)', text)
        return " ".join(matches)

    def _read_docx(self, path: str) -> str:
        """Extract text from DOCX."""
        try:
            import docx
            doc = docx.Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            console.print("[yellow]Install python-docx for DOCX support: pip install python-docx[/yellow]")
            return ""

    async def parse(self, resume_text: str) -> dict:
        """Use Claude to extract structured profile data from resume text."""
        if not self.api_key:
            console.print("[red]No Anthropic API key found. Set ANTHROPIC_API_KEY in .env[/red]")
            return {}

        import anthropic
        client = anthropic.Anthropic(api_key=self.api_key)

        prompt = f"""Analyze this resume and extract structured information. Return ONLY a JSON object with these fields:

{{
  "first_name": "",
  "last_name": "",
  "email": "",
  "phone": "",
  "linkedin": "",
  "website": "",
  "location": "",
  "city": "",
  "state": "",
  "country": "",
  "zip_code": "",

  "education_level": "",
  "school": "",
  "degree": "",
  "graduation_year": "",
  "major": "",

  "current_company": "",
  "current_title": "",
  "years_experience": "",
  "management_experience": "",

  "target_roles": ["list of job titles this person is best suited for"],
  "target_industries": ["list of industries they have experience in"],
  "key_skills": ["list of top 20 technical and domain skills"],
  "seniority_level": "one of: junior, mid, senior, staff, principal, director, vp",

  "role_category": "one of: product_manager, engineering_manager, data_scientist, software_engineer, designer, project_manager, business_analyst, other",
  "search_keywords": ["list of 10-15 job search keywords to find relevant roles"],
  "negative_keywords": ["list of keywords for roles they should NOT be matched with"]
}}

Important:
- For target_roles, suggest 5-8 specific job titles they'd be competitive for
- For search_keywords, include variations (e.g., "product manager", "PM", "technical product manager")
- For seniority_level, judge from years of experience and title progression
- For negative_keywords, exclude roles clearly below their level or outside their domain
- If a field can't be determined, use empty string or empty array

RESUME:
{resume_text[:6000]}

Return ONLY the JSON object, no other text."""

        try:
            message = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )

            text = message.content[0].text.strip()
            # Clean up markdown fences
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]

            return json.loads(text)

        except Exception as e:
            console.print(f"[red]Resume parsing error: {e}[/red]")
            return {}


class OnboardingEngine:
    """Generates all config files from parsed resume data."""

    def generate_profile(self, data: dict, resume_path: str) -> str:
        """Generate profile.yaml from parsed data."""
        profile = {
            "first_name": data.get("first_name", ""),
            "last_name": data.get("last_name", ""),
            "email": data.get("email", ""),
            "phone": data.get("phone", ""),
            "resume_path": resume_path,
            "linkedin": data.get("linkedin", ""),
            "website": data.get("website", ""),
            "cover_letter_path": "",
            "location": data.get("location", ""),
            "city": data.get("city", ""),
            "state": data.get("state", ""),
            "country": data.get("country", "United States"),
            "zip_code": data.get("zip_code", ""),
            "education_level": data.get("education_level", ""),
            "school": data.get("school", ""),
            "degree": data.get("degree", ""),
            "graduation_year": data.get("graduation_year", ""),
            "major": data.get("major", ""),
            "current_company": data.get("current_company", ""),
            "current_title": data.get("current_title", ""),
            "years_experience": data.get("years_experience", ""),
            "management_experience": data.get("management_experience", ""),
            "work_authorization": "Yes",
            "visa_sponsorship": "Yes",
            "salary_expectation": "",
            "start_date": "Immediately",
            "notice_period": "",
            "willing_to_relocate": "Yes",
            "comfortable_remote": "Yes",
            "how_did_you_hear": "Company website",
            "gender": "",
            "race": "",
            "veteran_status": "",
            "disability_status": "",
            "custom_answers": {
                "how did you hear about": "Company website",
                "how did you first learn": "Company website",
                "willing to relocate": "Yes",
                "comfortable with travel": "Yes",
                "currently or have you ever been employed": "I have never been employed",
                "acknowledge": "Yes",
                "nda": "Yes",
                "privacy": "Yes",
                "hispanic": "No",
                "in-person": "Yes",
                "hybrid": "Yes",
                "years of experience": data.get("years_experience", ""),
                "current job title": data.get("current_title", ""),
                "current employer": data.get("current_company", ""),
                "most recent employer": data.get("current_company", ""),
                "most recent job title": data.get("current_title", ""),
                "legal name": f"{data.get('first_name', '')} {data.get('last_name', '')}".strip(),
                "preferred name": data.get("first_name", ""),
            },
        }

        # Write profile.yaml
        with open("profile.yaml", "w") as f:
            yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

        return "profile.yaml"

    def generate_search_config(self, data: dict) -> dict:
        """Generate job search configuration from parsed data."""
        role_category = data.get("role_category", "other")
        seniority = data.get("seniority_level", "senior")
        search_keywords = data.get("search_keywords", [])
        target_roles = data.get("target_roles", [])
        negative_keywords = data.get("negative_keywords", [])
        key_skills = data.get("key_skills", [])

        # Build role filter patterns based on category
        role_filters = {
            "product_manager": [
                "product manager", "product management", "pm", "technical product",
                "product strategy", "product operations", "product lead", "product director",
                "product owner", "group product manager",
            ],
            "software_engineer": [
                "software engineer", "backend engineer", "frontend engineer",
                "full stack", "platform engineer", "staff engineer", "sre",
                "infrastructure engineer", "devops",
            ],
            "data_scientist": [
                "data scientist", "data analyst", "machine learning engineer",
                "ml engineer", "ai engineer", "research scientist",
                "analytics engineer", "data engineer",
            ],
            "engineering_manager": [
                "engineering manager", "em", "director of engineering",
                "vp engineering", "head of engineering", "tech lead manager",
            ],
            "designer": [
                "product designer", "ux designer", "ui designer",
                "design lead", "head of design", "ux researcher",
            ],
            "project_manager": [
                "project manager", "program manager", "technical program manager",
                "tpm", "delivery manager",
            ],
            "business_analyst": [
                "business analyst", "business intelligence", "strategy analyst",
                "operations analyst", "systems analyst",
            ],
        }

        # Get filter list for this role category
        filters = role_filters.get(role_category, search_keywords[:10])

        # Add seniority prefixes
        seniority_prefixes = {
            "junior": ["junior", "associate", "entry"],
            "mid": [""],
            "senior": ["senior", "sr"],
            "staff": ["staff", "senior", "sr"],
            "principal": ["principal", "staff", "senior"],
            "director": ["director", "head", "vp"],
            "vp": ["vp", "vice president", "svp", "head"],
        }

        config = {
            "role_category": role_category,
            "seniority_level": seniority,
            "target_roles": target_roles,
            "search_keywords": search_keywords,
            "negative_keywords": negative_keywords,
            "key_skills": key_skills,
            "role_filters": filters,
            "seniority_prefixes": seniority_prefixes.get(seniority, [""]),
            "target_industries": data.get("target_industries", []),
        }

        # Write search config
        with open("config/search_profile.yaml", "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return config

    def generate_matcher_config(self, data: dict) -> dict:
        """Generate custom resume matcher weights from parsed data."""
        key_skills = data.get("key_skills", [])
        target_roles = data.get("target_roles", [])

        # Build custom scoring weights
        title_weights = {}
        for role in target_roles:
            title_weights[role.lower()] = 35

        desc_weights = {}
        for skill in key_skills[:15]:
            desc_weights[skill.lower()] = 8

        config = {
            "title_positive": title_weights,
            "description_positive": desc_weights,
            "negative_companies": [],
            "negative_keywords": data.get("negative_keywords", []),
        }

        with open("config/matcher_profile.yaml", "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return config


async def onboard(resume_path: str, auto_apply: bool = False):
    """Full onboarding flow: resume → profile → search config → optional apply."""

    console.print("\n[bold]🌸 Pollen Onboarding[/bold]\n")

    # Step 1: Read resume
    console.print("[bold cyan]Step 1: Reading resume...[/bold cyan]")
    parser = ResumeParser()
    resume_text = parser.read_resume(resume_path)
    if not resume_text or len(resume_text) < 50:
        console.print("[red]Could not read resume. Supported: .pdf, .docx, .txt, .md[/red]")
        return

    word_count = len(resume_text.split())
    console.print(f"  ✓ Read {word_count} words from {resume_path}\n")

    # Step 2: Parse with Claude
    console.print("[bold cyan]Step 2: Analyzing resume with AI...[/bold cyan]")
    data = await parser.parse(resume_text)
    if not data:
        console.print("[red]Failed to parse resume. Check your API key.[/red]")
        return

    name = f"{data.get('first_name', '')} {data.get('last_name', '')}".strip()
    console.print(f"  ✓ Identified: {name}")
    console.print(f"  ✓ Role: {data.get('current_title', 'Unknown')} at {data.get('current_company', 'Unknown')}")
    console.print(f"  ✓ Category: {data.get('role_category', 'unknown')} ({data.get('seniority_level', 'unknown')} level)")
    console.print(f"  ✓ Experience: {data.get('years_experience', '?')} years")
    console.print(f"  ✓ Skills: {', '.join(data.get('key_skills', [])[:8])}")
    console.print()

    # Step 3: Show target roles
    console.print("[bold cyan]Step 3: Target roles identified[/bold cyan]")
    target_roles = data.get("target_roles", [])
    for i, role in enumerate(target_roles, 1):
        console.print(f"  {i}. {role}")
    console.print()

    # Step 4: Generate configs
    console.print("[bold cyan]Step 4: Generating configurations...[/bold cyan]")
    engine = OnboardingEngine()

    profile_path = engine.generate_profile(data, resume_path)
    console.print(f"  ✓ Created {profile_path}")

    os.makedirs("config", exist_ok=True)
    search_config = engine.generate_search_config(data)
    console.print(f"  ✓ Created config/search_profile.yaml")
    console.print(f"    Search keywords: {', '.join(search_config['search_keywords'][:6])}")

    matcher_config = engine.generate_matcher_config(data)
    console.print(f"  ✓ Created config/matcher_profile.yaml")
    console.print()

    # Step 5: Summary
    console.print("[bold cyan]Step 5: Summary[/bold cyan]")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Name", name)
    table.add_row("Email", data.get("email", ""))
    table.add_row("Location", data.get("location", ""))
    table.add_row("Role", data.get("role_category", ""))
    table.add_row("Level", data.get("seniority_level", ""))
    table.add_row("Target roles", str(len(target_roles)))
    table.add_row("Skills detected", str(len(data.get("key_skills", []))))
    table.add_row("Search keywords", str(len(search_config.get("search_keywords", []))))
    console.print(table)
    console.print()

    # Step 6: Next steps
    console.print("[bold green]✓ Onboarding complete![/bold green]\n")
    console.print("Next steps:")
    console.print("  1. Review and edit [bold]profile.yaml[/bold] (check email, phone, education)")
    console.print("  2. Run the pipeline:")
    console.print("     [dim]python run_pipeline.py --mode rank --min-score 30[/dim]")
    console.print("  3. Dry-run auto-apply:")
    console.print("     [dim]python auto_apply.py --profile profile.yaml --dry-run --auto-only --limit 5[/dim]")
    console.print("  4. Apply for real:")
    console.print("     [dim]python auto_apply.py --profile profile.yaml --auto-only --limit 50[/dim]")
    console.print()

    if auto_apply:
        console.print("[bold cyan]Starting auto-apply pipeline...[/bold cyan]\n")
        from run_pipeline import run_scoring, run_rank
        run_scoring(min_score=30)
        run_rank(min_score=30)
        console.print("\n  Jobs scored and ranked. Run auto_apply.py to start applying.")


def main():
    parser = argparse.ArgumentParser(description="Pollen Onboarding — Resume-driven setup")
    parser.add_argument("--resume", "-r", required=True, help="Path to your resume (PDF, DOCX, or TXT)")
    parser.add_argument("--apply", action="store_true", help="Also score and rank jobs after onboarding")
    args = parser.parse_args()

    asyncio.run(onboard(args.resume, auto_apply=args.apply))


if __name__ == "__main__":
    main()
