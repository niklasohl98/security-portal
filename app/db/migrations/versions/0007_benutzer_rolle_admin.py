"""Benutzer: Rolle Admin

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_admin", sa.Boolean(), server_default=sa.text("0"), nullable=False)
        )
    # Bisher hatten alle Benutzer volle Rechte: sie bleiben Admins (sonst käme niemand mehr an die
    # Benutzerverwaltung).
    op.execute("UPDATE app_user SET is_admin = 1")


def downgrade() -> None:
    with op.batch_alter_table("app_user", schema=None) as batch_op:
        batch_op.drop_column("is_admin")
