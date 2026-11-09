# Model-Driven Clarify Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clarify (and other shopping policy actions) are chosen by the LLM as tool calls; the host only applies answers and enforces hard guarantees.

**Architecture:** Keep `apply_clarify` + requirement field parsing as host helpers. Remove pre-loop `ask_clarify` interrupts in `llm_agent` / `agent_runner`. Ambiguous budget / failed answers continue into the ReAct loop so the model calls `clarify`.

**Tech Stack:** FastAPI, AgentRuntime ReAct loop, pytest, FakeModel / MockShopper

---

### Task 1: Drop pre-loop clarify interrupt in `llm_agent`

**Files:**
- Modify: `apps/api/app/llm_agent.py`
- Modify: `apps/api/tests/test_agent_budget.py`

- [x] Write failing test: low-confidence budget reaches the model and clarify comes from a tool call (`model.calls >= 1`)
- [x] Remove `needs_clarify → ask_clarify → return` short-circuit; keep host `update_requirements` parse; optionally hint the model when budget was ambiguous
- [x] Run `pytest tests/test_agent_budget.py tests/test_phase6_clarify.py -q` — pass
- [x] Commit

### Task 2: Drop host re-clarify in `agent_runner`

**Files:**
- Modify: `apps/api/app/agent_runner.py`
- Modify: `apps/api/tests/test_agent_clarify_runtime.py`

- [x] Write failing test: unparseable clarify answer continues into the model (not host interrupt)
- [x] On `apply_clarify` `needs_clarify`, do not hardcode questions; enter LLM with `skip_parse=False` so the model can call `clarify` again
- [x] Run clarify + phase6 tests — pass
- [x] Commit

### Task 3: Surface parse gaps to the model cleanly

**Files:**
- Modify: `apps/api/app/llm_agent.py` (turn hint only)
- Modify: `apps/api/tests/conftest.py` if MockShopper needs `field` on questions
- Modify: tests as needed

- [x] Ensure ambiguous-budget / failed-answer hints are clear and do not reintroduce host interrupts
- [x] Run `pytest tests/ -q` (or targeted suite) — pass
- [x] Commit

### Task 4: Remove dead clarify question duplicates

**Files:**
- Modify: `apps/api/app/agent_runner.py`, `apps/api/app/llm_agent.py` (delete leftover hardcoded lists if any)
- Modify: docs/prompt only if wording drifts

- [x] Confirm no host-owned clarify question lists remain outside tools/tests
- [x] Full API test pass
- [x] Commit
