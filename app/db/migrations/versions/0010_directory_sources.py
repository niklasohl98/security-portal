"""Additive Verzeichnis- und Quellkontoreferenzen; keine Änderung vorhandener Konten.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "directory_source",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "entra", "ad", name="source", native_enum=False, create_constraint=True, length=16
            ),
            nullable=False,
        ),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_directory_source")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_directory_source_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_directory_source_tenant_id")),
        sa.UniqueConstraint(
            "tenant_id", "source", "external_id", name="uq_directory_source_identity"
        ),
        sa.CheckConstraint(
            "length(external_id) > 0 AND external_id = lower(trim(external_id))",
            name=op.f("ck_directory_source_canonical_external_id"),
        ),
    )
    op.create_table(
        "source_account",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("directory_source_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_account")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_source_account_tenant_id_tenant"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "directory_source_id"],
            ["directory_source.tenant_id", "directory_source.id"],
            name=op.f("fk_source_account_tenant_id_directory_source"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "directory_source_id", "external_id", name="uq_source_account_identity"
        ),
        sa.CheckConstraint(
            "length(external_id) > 0 AND external_id = lower(trim(external_id))",
            name=op.f("ck_source_account_canonical_external_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("source_account")
    op.drop_table("directory_source")
