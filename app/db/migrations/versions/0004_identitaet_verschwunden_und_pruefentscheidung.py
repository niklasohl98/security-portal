"""Identität: verschwunden seit, Prüfentscheidung

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("identity", schema=None) as batch_op:
        batch_op.add_column(sa.Column("missing_since", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("review_decision", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("identity", schema=None) as batch_op:
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("review_decision")
        batch_op.drop_column("missing_since")
