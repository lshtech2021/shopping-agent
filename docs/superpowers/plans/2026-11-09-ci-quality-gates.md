# CI Quality Gates Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development.

**Goal:** Add a GitHub Actions workflow that runs API unit tests and extension tests on every push/PR so clean checkouts stay green.

**Tech Stack:** GitHub Actions, pytest, Node 20 `node --test`, optional Postgres service later

---

### Task 1: API pytest job

**Files:** `.github/workflows/ci.yml`

- [x] Job `api`: Python 3.12, `pip install -r apps/api/requirements.txt`, `pytest` with `PYTHONPATH=.`
- [x] Do not require `SA_TEST_DATABASE_URL` (memory-mode suite is enough for default CI)
- [x] Commit

### Task 2: Extension tests job

**Files:** `.github/workflows/ci.yml`

- [x] Job `extension`: Node 20, `npm ci` in `packages/extension`, `npm test`
- [x] Commit

### Task 3: Postgres service for durable repo tests

**Files:** `.github/workflows/ci.yml`

- [x] Add Postgres 16 service; set `SA_TEST_DATABASE_URL` / `SA_DATABASE_URL`
- [x] `alembic upgrade head` before pytest
- [x] Commit

### Deferred

- Lint/typecheck once tooling is pinned
- OTel / observability
