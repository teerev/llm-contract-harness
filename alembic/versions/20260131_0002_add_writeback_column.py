"""Add writeback column to runs table

Revision ID: 0002
Revises: 0001
Create Date: 2026-01-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("writeback", postgresql.JSONB(), nullable=True, server_default="{}")
    )


def downgrade() -> None:
    op.drop_column("runs", "writeback")
