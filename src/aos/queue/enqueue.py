"""
Job enqueueing for AOS.

This module handles putting jobs onto the Redis queue.
"""

import os
from uuid import UUID

from redis import Redis
from rq import Queue


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
    
    return job.id
