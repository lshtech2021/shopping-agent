# P2 Acceptance Polish Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development.

**Goal:** Close remaining Phase-1/ops leftovers after P2 follow-ups: document pool config, lock agent↔domain boundary with a test, and fix the known low-risk console XSS/data-id issue.

**Tech Stack:** FastAPI, pytest, vanilla web console JS

---

### Task 1: Document `SA_PG_POOL_SIZE` + refresh `.env.example`

**Files:** `apps/api/.env.example`

- [ ] Add `SA_PG_POOL_SIZE=0` with one-line comment (0 = single conn)
- [ ] Fix stale “empty key → rule policy” wording if still present
- [ ] Commit

### Task 2: Architecture test — `app.agent` stays domain-free

**Files:** `apps/api/tests/test_agent_architecture.py`

- [ ] Assert no `app.domains` imports under `app/agent`
- [ ] Assert agent package source has no shopping business identifiers (`requirements`, `inventory`, `recommend`, `clarify` as symbols — allow comments only if needed; prefer zero matches in `.py` excluding tests)
- [ ] Commit

### Task 3: Escape web console `data-id`

**Files:** `apps/web/app.js`, test if present

- [ ] Escape `data-id` / HTML attribute injection for session ids
- [ ] Commit

### Deferred

- Side Panel streaming (mid-turn fan-out)
- Full AsyncConnection rewrite (sync pool is enough for now)
- Unused `sqlalchemy` / extension `scripting` permission cleanup (batch later with CI chore)
