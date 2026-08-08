"""
Database session management and initialization.
"""

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from db.models import Base
from config.settings import settings


# Create async engine
engine = create_async_engine(
    settings.db.url,
    echo=settings.db.echo,
    # SQLite needs this for async
    connect_args={"check_same_thread": False} if "sqlite" in settings.db.url else {},
)

# Session factory
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✓ Database initialized")


async def get_session() -> AsyncSession:
    """Dependency for FastAPI."""
    async with async_session() as session:
        yield session


# CLI entrypoint
if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
