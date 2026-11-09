"""Normalized products/messages/candidates tables stay unused and dropped."""
from __future__ import annotations

from pathlib import Path

STORE_PG = Path(__file__).resolve().parents[1] / "app" / "store_pg.py"
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0002_drop_unused_normalized_tables.py"
)


def test_runtime_schema_no_longer_creates_unused_tables():
    source = STORE_PG.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS products" not in source
    assert "CREATE TABLE IF NOT EXISTS candidates" not in source
    assert "CREATE TABLE IF NOT EXISTS messages" not in source


def test_drop_migration_targets_unused_tables():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'for table in ("messages", "candidates", "products")' in source
    assert "DROP TABLE IF EXISTS" in source
