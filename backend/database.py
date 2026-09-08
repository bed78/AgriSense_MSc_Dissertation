"""
Database connection setup for the AgriSense API.

Builds a single shared SQLAlchemy async engine/session factory from the
credentials in conf.json, and exposes a `get_db()` dependency that
FastAPI routes use (via `Depends(get_db)`) to get a database session
for the duration of a single request.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import json
from pathlib import Path

# --- Load DB credentials -----------------------------------------------
# Automatically read credentials from conf.json so the API always matches your loading scripts!
conf_path = Path("conf.json")
with open(conf_path) as f:
    settings = json.load(f)

DBUSER = settings['DBUSER']
PASSWORD = settings['PASSWORD']
DBHOST = settings.get('DBHOST', 'localhost')
DBPORT = settings.get('DBPORT', 5433)  # Matches your Docker container
DBNAME = settings['DBNAME']

# Format the URL for SQLAlchemy asyncpg
DATABASE_URL = f"postgresql+asyncpg://{DBUSER}:{PASSWORD}@{DBHOST}:{DBPORT}/{DBNAME}"

# --- Engine & session factory ------------------------------------------
# `engine` is the single connection pool shared across the whole app.
# `SessionLocal` is a factory that creates new AsyncSession objects bound
# to that engine. `expire_on_commit=False` keeps loaded attributes usable
# after a commit, which is convenient for returning ORM objects from
# request handlers.
engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """
    FastAPI dependency that yields a database session for one request.

    Used as `db: AsyncSession = Depends(get_db)` in route handlers. The
    `async with` block guarantees the session (and its underlying
    connection) is closed automatically once the request finishes,
    even if an exception is raised.
    """
    async with SessionLocal() as session:
        yield session
