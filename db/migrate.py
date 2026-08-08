"""
Database migration — add new columns to existing tables.

This is needed because SQLAlchemy's create_all() only creates missing tables,
not missing columns on existing tables. Run this once after updating to
the new schema.

Usage:
    python -m db.migrate
"""

import asyncio
from sqlalchemy import text, inspect
from rich.console import Console

from db.session import engine, async_session
from db.models import Job

console = Console()

# Columns to add: (column_name, SQL type definition)
NEW_COLUMNS = [
    ("resume_match_score", "INTEGER"),
    ("resume_match_details", "TEXT"),
    ("visa_sponsorship", "VARCHAR(20) DEFAULT 'unknown'"),
    ("job_score", "INTEGER"),
    ("notes", "TEXT"),
]


async def get_existing_columns(table_name: str) -> list[str]:
    """Get list of existing column names for a table."""
    async with engine.connect() as conn:
        def get_columns(sync_conn):
            insp = inspect(sync_conn)
            return [col["name"] for col in insp.get_columns(table_name)]
        return await conn.run_sync(get_columns)


async def migrate():
    """Add missing columns to the jobs table."""
    console.print("[bold]🌸 Pollen DB Migration[/bold]\n")

    existing = await get_existing_columns("jobs")

    if existing is None:
        console.print("[red]Table 'jobs' does not exist. Run the app first to create tables.[/red]")
        return

    console.print(f"  Existing columns in 'jobs': {len(existing)}")

    added = 0
    async with engine.begin() as conn:
        for col_name, col_type in NEW_COLUMNS:
            if col_name not in existing:
                console.print(f"  [yellow]Adding column: {col_name} ({col_type})[/yellow]")
                await conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}"))
                added += 1
            else:
                console.print(f"  [green]✓ Column already exists: {col_name}[/green]")

    if added == 0:
        console.print("\n[green]✓ Database is up to date. No migration needed.[/green]")
    else:
        console.print(f"\n[green]✓ Migration complete. Added {added} column(s).[/green]")

    # Add indexes on new columns
    async with engine.begin() as conn:
        for col_name in ["job_score", "resume_match_score"]:
            try:
                index_name = f"ix_jobs_{col_name}"
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON jobs ({col_name})"))
                console.print(f"  [dim]Index ensured: {index_name}[/dim]")
            except Exception:
                pass  # Index may already exist

    console.print("\n[bold green]Done![/bold green]")


if __name__ == "__main__":
    asyncio.run(migrate())
