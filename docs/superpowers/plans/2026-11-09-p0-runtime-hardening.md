# P0 Runtime Hardening Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Land the still-open P0 correctness/hot-path fixes from the improvement roadmap.

**Recheck (2026-11-09):**

| Item | Status | Action now |
|------|--------|------------|
| VersionConflict overwrite | Still broken in `store._persist` | Fix + test |
| Checkpoint resume turn mismatch | Still broken (`turn += 1` then match `==`) | Fix + test |
| Auth fail-open / unauth GETs | Still open | `SA_AUTH_REQUIRED` + protect GETs |
| Memory summarize every step | Still open | Cache by fingerprint |
| Full-blob touch amplification | Still open | Dirty/flush batching (slice) |
| Async PG / pool | Still open | Defer to later P0/P1 (larger) |
| Model-driven clarify | Done | Skip |
| P1 parallel tools / ToolBox split / planner | Open | After P0 |
| P2 alembic-only / file splits | Open | After P0 |

**Tech Stack:** FastAPI, SessionStore / RunRepository, AgentRuntime, pytest

---

### Task 1: VersionConflict must not clobber

**Files:** `apps/api/app/store.py`, `apps/api/tests/test_store_conflict.py`

- [x] Failing test: stale `touch` after concurrent repo update leaves newer products intact and raises
- [x] `_persist`: on `VersionConflict`, refresh cache from repo and re-raise (no force-write)
- [x] Commit

### Task 2: Checkpoint resume turn matching

**Files:** `apps/api/app/llm_agent.py`, `apps/api/tests/test_checkpoint_resume_turn.py`

- [x] Failing test: checkpoint saved at turn N loads after `run_agent` bumps to N+1
- [x] Accept `candidate.turn in {session.turn, session.turn - 1}`; set `state["turn"] = session.turn`
- [x] Commit

### Task 3: Optional fail-closed auth

**Files:** `apps/api/app/config.py`, `apps/api/app/main.py`, `apps/api/app/api/actions.py`, tests, `.env.example`

- [x] Add `SA_AUTH_REQUIRED` (default False)
- [x] When required or token configured: reject missing/wrong token (incl. session GETs); constant-time compare
- [x] Commit

### Task 4: Cache shopping memory summarize

**Files:** `apps/api/app/domains/shopping/memory.py`, tests

- [x] Cache `context_block` until requirements/products/quotas/sites/status fingerprint changes
- [x] Commit

### Task 5: Batch session persists

**Files:** `apps/api/app/store.py`, `apps/api/app/trace.py`, `apps/api/app/agent_runner.py`, tools touch sites, tests

- [x] `touch` updates memory + dirty; `flush` writes; force persist for interrupts/end-of-turn
- [x] Commit

### Deferred (not this plan)

- Async psycopg pool / `to_thread`
- P1–P2 structural splits
