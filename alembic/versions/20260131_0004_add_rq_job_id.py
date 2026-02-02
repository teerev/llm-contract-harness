"""Add rq_job_id column to runs table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-01-31

Stores the RQ job ID for observability and debugging.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add rq_job_id column."""
    op.add_column(
        "runs",
        sa.Column("rq_job_id", sa.String(100), nullable=True)
    )


def downgrade() -> None:
    """Remove rq_job_id column."""
    op.drop_column("runs", "rq_job_id")
