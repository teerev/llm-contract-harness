"""
Job enqueueing for AOS.

This module handles putting jobs onto the Redis queue.
"""

import logging
import os
from uuid import UUID

from redis import Redis
from rq import Queue

from ..db import get_session, Run


logger = logging.getLogger(__name__)


def get_redis_connection() -> Redis:
    """Get Redis connection from environment or default."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    return Redis.from_url(redis_url)


def get_queue() -> Queue:
    """Get the RQ queue for AOS jobs."""
    # Use "default" queue so workers can be started with just `rq worker`
    return Queue("default", connection=get_redis_connection())


def enqueue_run(run_id: UUID) -> str:
    """
    Enqueue a run for processing by a worker.
    
    Args:
        run_id: The UUID of the run to process
    
    Returns:
        The RQ job ID
    """
    queue = get_queue()
    
    # Import here to avoid circular imports
    from .worker import run_job
    
    job = queue.enqueue(
        run_job,
        str(run_id),
        job_timeout="1h",  # Allow long-running jobs
    )
    
    # Store the RQ job ID in the database for observability
    try:
        with get_session() as session:
            run = session.query(Run).filter(Run.id == run_id).first()
            if run:
                run.rq_job_id = job.id
                logger.info(f"Enqueued run {run_id} as RQ job {job.id}")
    except Exception as e:
        # Don't fail the enqueue if we can't store the job ID
        logger.warning(f"Failed to store RQ job ID for run {run_id}: {e}")
    
    return job.id
