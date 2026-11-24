"""Revolut support: wider transaction side, transaction source, percentage fee model for paper accounts.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as b:
        b.alter_column("side", existing_type=sa.String(4), type_=sa.String(8))
        b.add_column(sa.Column("source", sa.String(40), nullable=False, server_default="manual"))
    with op.batch_alter_table("paper_accounts") as b:
        b.add_column(sa.Column("fee_model", sa.String(12), nullable=False, server_default="per_share"))
        b.add_column(sa.Column("commission_pct", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("free_trades_per_month", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("fx_fee_pct", sa.Float(), nullable=False, server_default="0"))
        b.add_column(sa.Column("fee_profile", sa.String(60), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("paper_accounts") as b:
        for c in ("fee_profile", "fx_fee_pct", "free_trades_per_month", "commission_pct", "fee_model"):
            b.drop_column(c)
    with op.batch_alter_table("transactions") as b:
        b.drop_column("source")
        b.alter_column("side", existing_type=sa.String(8), type_=sa.String(4))
