# P2 Structure & Hardening Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development.

**Goal:** Land remaining roadmap structure items: schema ownership, leaky seams, ToolBox deepening slice, cost budgets, async durable writes.

**Tech Stack:** FastAPI, Alembic, ToolBox, pytest

---

### Task 1: Alembic owns schema

**Files:** `config.py`, `store_pg.py`, `.env.example`, tests

- [ ] Add `SA_AUTO_CREATE_SCHEMA` (default false)
- [ ] Gate `_init_schema` on that flag; document alembic upgrade
- [ ] Commit

### Task 2: Close ShoppingBrowser private browse seam

**Files:** `tools.py`, `domains/shopping/adapter.py`, tests

- [ ] Public `ToolBox.browse_dispatch` / typed public browse entry that applies guards
- [ ] Adapter stops calling `box._browse`
- [ ] Commit

### Task 3: Extract requirements parsing from ToolBox

**Files:** `domains/shopping/requirements.py` (new), `tools.py`, tests

- [ ] Move `parse_budget` / category / RAM extract helpers into shopping domain module
- [ ] ToolBox imports them; keep public `parse_budget` re-export for tests
- [ ] Commit

### Task 4: Golden-turn cost budgets

**Files:** `tests/test_golden_turn_budgets.py`

- [ ] Assert MockShopper complete-req turn stays under max steps / max `get_session` calls
- [ ] Commit

### Task 5: Durable store writes off event loop

**Files:** `agent_runner.py`, optional `store.py`, tests

- [ ] End-of-turn / interrupt durable `touch` via `asyncio.to_thread`
- [ ] Commit
