"""Store large tool-result artifacts by reference.

Revision ID: 0004_run_artifacts
Revises: 0003_runs_settings
Create Date: 2026-11-12
"""
from __future__ import annotations

from alembic import op

revision = "0004_run_artifacts"
down_revision = "0003_runs_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS run_artifacts (
            artifact_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS run_artifacts_run_idx
        ON run_artifacts(run_id, created_at)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS run_artifacts")
