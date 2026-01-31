"""
Database session management.

Provides:
- get_engine(): Create SQLAlchemy engine from DATABASE_URL
- get_session(): Context manager for database sessions
- init_db(): Create all tables (for quick testing, prefer Alembic for production)
"""

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


# Default for local development (matches docker-compose.yml)
DEFAULT_DATABASE_URL = "postgresql://aos:aos_dev@localhost:5432/aos"


def get_database_url() -> str:
    """Get database URL from environment or use default."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_engine() -> Engine:
    """Create and return a SQLAlchemy engine."""
    return create_engine(get_database_url())


# Session factory - created once, reused
_SessionLocal: sessionmaker | None = None


def _get_session_factory() -> sessionmaker:
    """Get or create the session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine()
        _SessionLocal = sessionmaker(bind=engine)
    return _SessionLocal


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.
    
    Usage:
        with get_session() as session:
            run = session.query(Run).get(run_id)
            # ... work with run ...
        # Session automatically committed on success, rolled back on exception
    """
    factory = _get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """
    Create all tables. 
    
    Use this for quick testing. For production, use Alembic migrations.
    """
    engine = get_engine()
    Base.metadata.create_all(engine)
