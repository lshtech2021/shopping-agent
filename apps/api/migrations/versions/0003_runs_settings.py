"""Persist session settings on the runs table.

Revision ID: 0003_runs_settings
Revises: 0002_drop_unused
Create Date: 2026-11-12

The runs table was created without a settings column, so a message refresh
loaded the session from Postgres without its site/currency and silently reset
the active currency. Add the column and backfill what can be recovered.
"""
from __future__ import annotations

from alembic import op

revision = "0003_runs_settings"
down_revision = "0002_drop_unused"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runs
        ADD COLUMN IF NOT EXISTS settings JSONB
        NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute(
        """
        UPDATE runs
        SET settings = jsonb_build_object('site', sites->>0)
        WHERE (settings = '{}'::jsonb)
          AND jsonb_typeof(sites) = 'array'
          AND jsonb_array_length(sites) > 0
        """
    )
    op.execute(
        """
        UPDATE runs
        SET settings = settings || jsonb_build_object(
            'currency', 'CNY',
            'currency_source', 'detected'
        )
        WHERE settings->>'site' = 'jd'
          AND settings->>'currency' IS NULL
        """
    )
    op.execute(
        """
        UPDATE runs
        SET settings = settings || jsonb_build_object(
            'currency', requirements->>'budget_currency',
            'currency_source', 'manual'
        )
        WHERE settings->>'currency' IS NULL
          AND requirements->>'budget_currency' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS settings")
