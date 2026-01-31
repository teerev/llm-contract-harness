"""
Event logging helpers for AOS.

Events are the append-only audit trail of everything that happens
during a run. This module provides helpers for recording events.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from .db import Event


def record_event(
    session: Session,
    run_id: UUID,
    kind: str,
    payload: dict[str, Any] | None = None,
    level: str = "INFO",
    iteration: int | None = None,
) -> Event:
    """
    Record an event to the database.
    
    Args:
        session: SQLAlchemy session
        run_id: The run this event belongs to
        kind: Event type (e.g., RUN_START, SE_OUTPUT, ERROR_EXCEPTION)
        payload: Event data (stored as JSONB)
        level: INFO, WARN, or ERROR
        iteration: Which iteration this event occurred in (if applicable)
    
    Returns:
        The created Event object
    """
    event = Event(
        run_id=run_id,
        ts=datetime.utcnow(),
        kind=kind,
        level=level,
        iteration=iteration,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return event


# Standard event kinds (from AWS_SPEC.md section 6)
class EventKind:
    """Constants for standard event kinds."""
    
    # Run lifecycle
    RUN_CREATED = "RUN_CREATED"
    RUN_START = "RUN_START"
    RUN_END = "RUN_END"
    RUN_CANCELED = "RUN_CANCELED"
    
    # Iteration lifecycle
    STEP_START = "STEP_START"
    SE_OUTPUT = "SE_OUTPUT"
    TR_APPLY = "TR_APPLY"
    PO_RESULT = "PO_RESULT"
    STEP_END = "STEP_END"
    
    # Errors
    ERROR_EXCEPTION = "ERROR_EXCEPTION"
