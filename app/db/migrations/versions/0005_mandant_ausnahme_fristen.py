"""Mandant: Standardlaufzeit und Vorwarnzeit für Ausnahmen

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tenant", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("exception_days", sa.Integer(), server_default="365", nullable=False)
        )
        batch_op.add_column(
            sa.Column("exception_warn_days", sa.Integer(), server_default="30", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("tenant", schema=None) as batch_op:
        batch_op.drop_column("exception_warn_days")
        batch_op.drop_column("exception_days")
