"""Phase 4 durable run/action schema.

Revision ID: 0001_phase4
Revises:
Create Date: 2026-11-09
"""
from __future__ import annotations

from alembic import op

revision = "0001_phase4"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            requirement TEXT NOT NULL,
            sites JSONB NOT NULL,
            status TEXT NOT NULL,
            requirements JSONB NOT NULL,
            plan JSONB,
            messages JSONB NOT NULL,
            products JSONB NOT NULL,
            recommendations JSONB NOT NULL,
            quotas JSONB NOT NULL,
            searched_sites JSONB NOT NULL,
            interrupt_reason TEXT,
            pending_questions JSONB NOT NULL,
            blocked_sites JSONB NOT NULL,
            execution_logs JSONB NOT NULL,
            turn INT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version BIGINT NOT NULL DEFAULT 1,
            lease_owner TEXT,
            lease_expires_at TEXT,
            heartbeat_at TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS actions (
            action_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            tool TEXT NOT NULL,
            args JSONB NOT NULL,
            status TEXT NOT NULL,
            result JSONB,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            action_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS run_events (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL,
            event JSONB NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
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
        CREATE TABLE IF NOT EXISTS request_receipts (
            idempotency_key TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            response JSONB NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            run_id TEXT PRIMARY KEY,
            state JSONB NOT NULL,
            updated_at TEXT NOT NULL
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
    op.execute(
        "CREATE INDEX IF NOT EXISTS runs_status_idx ON runs(status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS actions_run_idx ON actions(run_id, status)"
    )
    # One-time migration from the pre-Phase-4 JSONB session table.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.sessions') IS NOT NULL THEN
                INSERT INTO runs (
                    id, requirement, sites, status, requirements, plan,
                    messages, products, recommendations, quotas,
                    searched_sites, interrupt_reason, pending_questions,
                    blocked_sites, execution_logs, turn, version,
                    created_at, updated_at
                )
                SELECT
                    payload->>'id',
                    COALESCE(payload->>'requirement', ''),
                    COALESCE(payload->'sites', '[]'::jsonb),
                    COALESCE(payload->>'status', 'idle'),
                    COALESCE(payload->'requirements', '{}'::jsonb),
                    payload->'plan',
                    COALESCE(payload->'messages', '[]'::jsonb),
                    COALESCE(payload->'products', '{}'::jsonb),
                    COALESCE(payload->'recommendations', '[]'::jsonb),
                    COALESCE(payload->'quotas', '{}'::jsonb),
                    COALESCE(payload->'searched_sites', '[]'::jsonb),
                    payload->>'interrupt_reason',
                    COALESCE(payload->'pending_questions', '[]'::jsonb),
                    COALESCE(payload->'blocked_sites', '{}'::jsonb),
                    COALESCE(payload->'execution_logs', '[]'::jsonb),
                    COALESCE((payload->>'turn')::int, 0),
                    1,
                    COALESCE(payload->>'created_at', now()::text),
                    COALESCE(payload->>'updated_at', now()::text)
                FROM sessions
                WHERE payload->>'id' IS NOT NULL
                ON CONFLICT (id) DO NOTHING;
                DROP TABLE sessions;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in (
        "messages",
        "request_receipts",
        "checkpoints",
        "candidates",
        "products",
        "run_events",
        "observations",
        "actions",
        "runs",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
