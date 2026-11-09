"""Stabile Entra-Benutzerreferenz und Herkunft der Sitzung; keine Tokenpersistenz.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_user") as batch:
        batch.add_column(sa.Column("oidc_tenant_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("oidc_object_id", sa.String(36), nullable=True))
        batch.create_unique_constraint(
            "uq_app_user_oidc_identity", ["oidc_tenant_id", "oidc_object_id"]
        )
        batch.create_check_constraint(
            "oidc_identity_pair", "(oidc_tenant_id IS NULL) = (oidc_object_id IS NULL)"
        )
    with op.batch_alter_table("app_session") as batch:
        batch.add_column(
            sa.Column("is_oidc", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("app_session") as batch:
        batch.drop_column("is_oidc")
    with op.batch_alter_table("app_user") as batch:
        batch.drop_constraint("oidc_identity_pair", type_="check")
        batch.drop_constraint("uq_app_user_oidc_identity", type_="unique")
        batch.drop_column("oidc_object_id")
        batch.drop_column("oidc_tenant_id")
