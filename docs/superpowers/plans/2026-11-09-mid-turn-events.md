# Mid-Turn Event Fan-out Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development.

**Goal:** Make turn progress visible without waiting for end-of-turn durable flush, by appending to the unused `run_events` table and exposing a cursor API the Side Panel can poll (SSE later).

**Architecture:** Soft-touch keeps `execution_logs` in the worker memory working set. Multi-worker and cold GETs only see durable JSONB. Append-only `run_events` rows are cheap and give a cross-process cursor. Side Panel keeps interval polling first; SSE/WS can subscribe to the same cursor later.

**Tech Stack:** FastAPI, RunRepository/`run_events`, extension panel poller, pytest

---

### Task 1: Repository append + list

**Files:** `store_pg.py`, `store.py`, tests

- [ ] `append_run_event(run_id, event)` inserts into `run_events`
- [ ] `list_run_events(run_id, *, after_id=0, limit=100)` returns ordered rows with ids
- [ ] Memory mode mirrors with an in-process list
- [ ] Commit

### Task 2: Trace writes events

**Files:** `trace.py`, tests

- [ ] `log_event` also appends via store when session is known
- [ ] Failures to append must not break the tool path
- [ ] Commit

### Task 3: HTTP cursor endpoint

**Files:** `main.py`, schemas, tests

- [ ] `GET /api/v1/sessions/{id}/events?after=&limit=`
- [ ] Auth same as session GET
- [ ] Commit

### Task 4: Side Panel polls events

**Files:** `packages/extension/panel/*`, tests

- [ ] During busy turn, poll events cursor; update progress text from latest stage/message
- [ ] Fall back to session GET if events empty
- [ ] Commit

### Deferred

- SSE/WebSocket push of the same cursor
- Dropping 2s full-session poll entirely
