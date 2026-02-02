"""Drop unused steps table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-01-31

The steps table was originally designed for per-iteration phase tracking
(SE/TR/PO) but was never actually used. All relevant data is already
captured in the events table, making steps redundant.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the steps table."""
    # Drop index first
    op.drop_index("ix_steps_run_id_iteration", table_name="steps")
    
    # Drop the table
    op.drop_table("steps")


def downgrade() -> None:
    """Recreate the steps table (for rollback)."""
    import sqlalchemy as sa
    from sqlalchemy.dialects.postgresql import UUID, JSONB
    
    op.create_table(
        "steps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="STARTED"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    
    op.create_index("ix_steps_run_id_iteration", "steps", ["run_id", "iteration"])
