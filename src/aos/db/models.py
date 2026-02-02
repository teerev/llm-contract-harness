"""
Database models for AOS.

Three tables:
- runs: The main job record (one per API request)
- events: Append-only audit log of everything that happens
- artifacts: Metadata for files created during a run
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class Run(Base):
    """
    A single AOS job.
    
    Lifecycle: PENDING -> RUNNING -> SUCCEEDED/FAILED/CANCELED
    """
    __tablename__ = "runs"

    # Primary key - UUID generated on creation
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Status tracking
    status = Column(String(20), nullable=False, default="PENDING")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    
    # Idempotency - allows clients to retry safely
    idempotency_key = Column(String(255), unique=True, nullable=True)
    
    # Git source
    repo_url = Column(Text, nullable=False)
    repo_ref = Column(Text, nullable=False, default="main")
    git_sha = Column(String(40), nullable=True)  # Recorded after clone
    
    # Work order (the task specification)
    work_order = Column(JSONB, nullable=False)
    work_order_body = Column(Text, nullable=False, default="")
    
    # Execution parameters
    params = Column(JSONB, nullable=False, default=dict)
    
    # Writeback configuration
    writeback = Column(JSONB, nullable=True, default=dict)
    
    # Progress tracking
    iteration = Column(Integer, nullable=False, default=0)
    
    # RQ job tracking
    rq_job_id = Column(String(100), nullable=True)  # RQ job ID for debugging
    
    # Results
    result_summary = Column(Text, nullable=True)
    error = Column(JSONB, nullable=True)
    artifact_root = Column(Text, nullable=True)
    
    # Relationships
    events = relationship("Event", back_populates="run", cascade="all, delete-orphan")
    artifacts = relationship("Artifact", back_populates="run", cascade="all, delete-orphan")


class Event(Base):
    """
    Append-only audit log entry.
    
    Events are never updated or deleted - they form the complete
    history of what happened during a run.
    """
    __tablename__ = "events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False)
    
    ts = Column(DateTime, nullable=False, default=datetime.utcnow)
    level = Column(String(10), nullable=False, default="INFO")  # INFO/WARN/ERROR
    kind = Column(String(50), nullable=False)  # e.g., RUN_START, SE_OUTPUT, etc.
    iteration = Column(Integer, nullable=True)
    payload = Column(JSONB, nullable=True)
    
    run = relationship("Run", back_populates="events")
    
    __table_args__ = (
        Index("ix_events_run_id_id", "run_id", "id"),  # For tailing queries
    )


class Artifact(Base):
    """
    Metadata for a file created during a run.
    
    The actual file lives on disk at `path`. This table stores
    metadata for querying and integrity checking.
    """
    __tablename__ = "artifacts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    run_id = Column(UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False)
    
    name = Column(Text, nullable=False)  # Logical name (e.g., "se_packet_iter_1.json")
    path = Column(Text, nullable=False)  # Filesystem path
    content_type = Column(String(100), nullable=True)
    bytes = Column(BigInteger, nullable=True)
    sha256 = Column(String(64), nullable=True)
    
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    run = relationship("Run", back_populates="artifacts")
    
    __table_args__ = (
        Index("ix_artifacts_run_id", "run_id"),
    )
