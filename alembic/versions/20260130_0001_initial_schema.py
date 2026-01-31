"""Initial schema - runs, events, steps, artifacts tables

Revision ID: 0001
Revises: 
Create Date: 2026-01-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # runs table - main job record
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), unique=True, nullable=True),
        sa.Column("repo_url", sa.Text(), nullable=False),
        sa.Column("repo_ref", sa.Text(), nullable=False, server_default="main"),
        sa.Column("git_sha", sa.String(40), nullable=True),
        sa.Column("work_order", postgresql.JSONB(), nullable=False),
        sa.Column("work_order_body", sa.Text(), nullable=False, server_default=""),
        sa.Column("params", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(), nullable=True),
        sa.Column("artifact_root", sa.Text(), nullable=True),
    )

    # events table - append-only audit log
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("level", sa.String(10), nullable=False, server_default="INFO"),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_events_run_id_id", "events", ["run_id", "id"])

    # steps table - per-iteration phase records
    op.create_table(
        "steps",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="STARTED"),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
    )
    op.create_index("ix_steps_run_id_iteration", "steps", ["run_id", "iteration"])

    # artifacts table - file metadata
    op.create_table(
        "artifacts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])


def downgrade() -> None:
    op.drop_table("artifacts")
    op.drop_table("steps")
    op.drop_table("events")
    op.drop_table("runs")
