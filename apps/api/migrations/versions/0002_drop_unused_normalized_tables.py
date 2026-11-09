"""Drop unused normalized product/message tables.

Revision ID: 0002_drop_unused
Revises: 0001_phase4
Create Date: 2026-11-09

Session products and messages remain on runs JSONB columns. The separate
normalized tables were never written by application code.
"""
from __future__ import annotations

from alembic import op

revision = "0002_drop_unused"
down_revision = "0001_phase4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("messages", "candidates", "products"):
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            run_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (run_id, product_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            run_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (run_id, product_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
