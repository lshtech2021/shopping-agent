# P2 Follow-ups Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development.

**Goal:** Land remaining roadmap leftovers: drop unused normalized tables, split ToolBox browser/inventory seams, optional PG pool. Defer full UI streaming to a later plan (needs mid-turn event transport).

---

### Task 1: Drop unused normalized tables

**Files:** new Alembic `0002_…`, `store_pg.py`

- [ ] Migration drops `products`, `candidates`, `messages` tables (JSONB on `runs` remains source of truth)
- [ ] Remove from `_init_schema` + `TRUNCATE`
- [ ] Commit

### Task 2: Extract BrowserGateway

**Files:** `app/browser/gateway.py` (new), `tools.py`, tests

- [ ] Move throttle + `_browse` live/mock dispatch behind `BrowserGateway`
- [ ] ToolBox delegates; keep public search/scrape APIs
- [ ] Commit

### Task 3: Deepen inventory module

**Files:** `inventory.py`, `tools.py`, tests

- [x] Move `_inventory_summary`, upsert index helpers into inventory module
- [x] Commit

### Task 4: Optional psycopg connection pool

**Files:** `config.py`, `store_pg.py`, tests

- [ ] `SA_PG_POOL_SIZE` (0 = single conn); use `psycopg_pool.ConnectionPool` when > 0
- [ ] Commit

### Deferred

- Side Panel streaming (SSE/WS progress) — needs durable mid-turn event fan-out design
