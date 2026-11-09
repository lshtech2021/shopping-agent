# P0 leftover + P1 Runtime Perf Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development.

**Goal:** Finish deferred P0 async offload, then land high-leverage P1 latency/structure slices.

**Tech Stack:** asyncio, AgentRuntime, ToolBox, ShoppingPlanner, RunRepository, pytest

---

### Task 1: Offload sync checkpoint PG I/O

**Files:** `apps/api/app/agent/checkpoint.py`, tests

- [x] `PgCheckpointStore.save/load/delete` run repo calls via `asyncio.to_thread`
- [x] Commit

### Task 2: Parallelize safe tool batches

**Files:** `apps/api/app/agent/runtime.py`, tests

- [x] When a step’s tool_calls are all parallel-safe (`fetch_detail`, `query_inventory`, `get_session`), execute with `asyncio.gather` preserving order
- [x] Keep sequential for browse / finish / wait_user tools
- [x] Commit

### Task 3: O(1) product upsert index

**Files:** `apps/api/app/tools.py`, tests

- [x] Maintain `(source, source_product_id) -> product_id` map; use in `_upsert_listing`
- [x] Commit

### Task 4: Template planner when requirements complete

**Files:** `apps/api/app/domains/shopping/planner.py`, `llm_agent.py`, tests

- [x] If category + budget resolved + sites present, use template/fallback plan (no LLM)
- [x] Commit

### Task 5: Cap / lazy run list load

**Files:** `apps/api/app/store_pg.py`, `store.py`, tests

- [x] `list_runs(limit=...)`; SessionStore warmup capped; `get` still loads missing runs
- [x] Commit
