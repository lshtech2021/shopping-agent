# Unified Ask + Requirement Extract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `ask_user` wait-user tool; user replies as normal chat messages; structured requirements updated via typed extract/apply instead of host regex NLU and `apply_clarify`.

**Architecture:** Shopping domain owns extract + apply. Generic runtime only sees `control=wait_user`. Extension cards format answers into `content`. `constraints.py` stays deterministic for product eligibility.

**Tech Stack:** FastAPI, Pydantic v2, AgentRuntime ReAct, pytest, FakeModel/MockShopper, Chrome Side Panel

**Spec:** `docs/superpowers/specs/2026-11-10-unified-ask-and-requirement-extract-design.md`

## Global Constraints

- `app.agent` must not import `app.domains` or shopping business names (architecture test already enforces this).
- Host must never invent budget/category/must_have the user did not state.
- Commit after each task; keep FakeModel tests green without a live LLM key.
- Do not rewrite `constraints.py` product matching with an LLM in this plan.

## File map

| Path | Role |
|------|------|
| `apps/api/app/domains/shopping/requirements_patch.py` | `RequirementPatch` + `apply_requirement_patch` |
| `apps/api/app/domains/shopping/extract.py` | Structured extract (FakeModel-friendly interface) |
| `apps/api/app/domains/shopping/tools.py` | Unify ask tool; patch-based `update_requirements` |
| `apps/api/app/domains/shopping/prompt.py` | Prompt: one ask tool; answers are chat |
| `apps/api/app/domains/shopping/requirements.py` | Shrink to grounding helpers or delete unused parsers |
| `apps/api/app/tools.py` | Remove regex NLU / `apply_clarify` hot path |
| `apps/api/app/agent_runner.py` | Drop `answers` → `apply_clarify` |
| `apps/api/app/schemas.py` / `main.py` | Deprecate `answers` field |
| `packages/extension/panel/sidepanel.js` | Submit cards as `content` message |
| `packages/extension/shared/api.js` | Stop sending `answers` |

---

### Task 1: `RequirementPatch` + host apply

**Files:**
- Create: `apps/api/app/domains/shopping/requirements_patch.py`
- Test: `apps/api/tests/test_requirement_patch.py`

**Interfaces:**
- Produces: `RequirementPatch`, `apply_requirement_patch(req: dict, patch: RequirementPatch) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
from app.domains.shopping.requirements_patch import (
    RequirementPatch,
    apply_requirement_patch,
)

def test_apply_sets_budget_and_category():
    req = {"budget_cny": None, "category": None, "must_have": [], "preferences": []}
    patch = RequirementPatch(
        budget_cny=5000,
        category="笔记本",
        confidence={"budget_cny": "high", "category": "high"},
    )
    out = apply_requirement_patch(req, patch)
    assert out["budget_cny"] == 5000
    assert out["category"] == "笔记本"
    assert out.get("budget_unlimited") is False

def test_apply_skips_low_confidence_budget():
    req = {"budget_cny": None}
    patch = RequirementPatch(
        budget_cny=5000,
        confidence={"budget_cny": "low"},
        ambiguous_fields=["budget_cny"],
    )
    out = apply_requirement_patch(req, patch)
    assert out.get("budget_cny") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_requirement_patch.py -q`

Expected: FAIL import / missing module

- [ ] **Step 3: Implement**

```python
# requirements_patch.py
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field

Confidence = Literal["high", "low"]

class RequirementPatch(BaseModel):
    budget_cny: float | None = None
    budget_min_cny: float | None = None
    budget_unlimited: bool | None = None
    category: str | None = None
    use_case: str | None = None
    must_have: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    confidence: dict[str, Confidence] = Field(default_factory=dict)
    ambiguous_fields: list[str] = Field(default_factory=list)
    source_spans: dict[str, str] = Field(default_factory=dict)

def apply_requirement_patch(
    requirements: dict[str, Any], patch: RequirementPatch
) -> dict[str, Any]:
    req = dict(requirements or {})
    conf = patch.confidence or {}

    def high(field: str) -> bool:
        return conf.get(field, "high") == "high" and field not in patch.ambiguous_fields

    if patch.budget_unlimited is True and high("budget_unlimited"):
        req["budget_cny"] = None
        req["budget_min_cny"] = None
        req["budget_unlimited"] = True
    else:
        if patch.budget_cny is not None and high("budget_cny"):
            req["budget_cny"] = float(patch.budget_cny)
            req["budget_unlimited"] = False
        if patch.budget_min_cny is not None and high("budget_min_cny"):
            req["budget_min_cny"] = float(patch.budget_min_cny)
            req["budget_unlimited"] = False
    if patch.category and high("category"):
        req["category"] = patch.category
    if patch.use_case and high("use_case"):
        req["use_case"] = patch.use_case
    if patch.must_have:
        must = list(req.get("must_have") or [])
        for item in patch.must_have:
            if item and item not in must:
                must.append(item)
        req["must_have"] = must
    if patch.preferences:
        prefs = list(req.get("preferences") or [])
        for item in patch.preferences:
            if item and item not in prefs:
                prefs.append(item)
        req["preferences"] = prefs
    return req
```

- [ ] **Step 4: Run tests — pass**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_requirement_patch.py -q`

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/domains/shopping/requirements_patch.py apps/api/tests/test_requirement_patch.py
git commit -m "feat: add RequirementPatch host apply without regex NLU"
```

---

### Task 2: Extract interface (deterministic stub + LLM hook)

**Files:**
- Create: `apps/api/app/domains/shopping/extract.py`
- Test: `apps/api/tests/test_requirement_extract.py`

**Interfaces:**
- Consumes: `RequirementPatch`
- Produces: `async def extract_requirement_patch(text: str, *, current: dict, llm=None) -> RequirementPatch`

- [ ] **Step 1: Write failing tests**

```python
import asyncio
from app.domains.shopping.extract import extract_requirement_patch

def test_extract_with_injected_llm_returns_patch():
    async def fake_llm(prompt: str) -> dict:
        return {
            "budget_cny": 5000,
            "category": "笔记本",
            "confidence": {"budget_cny": "high", "category": "high"},
            "ambiguous_fields": [],
            "must_have": ["16G内存"],
            "preferences": [],
        }

    patch = asyncio.run(
        extract_requirement_patch(
            "预算5000买笔记本，至少16G内存",
            current={},
            llm=fake_llm,
        )
    )
    assert patch.budget_cny == 5000
    assert patch.category == "笔记本"
    assert "16G内存" in patch.must_have
```

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_requirement_extract.py -q`

- [ ] **Step 3: Implement extract module**

- Build a short system+user prompt asking for JSON matching `RequirementPatch`.
- If `llm` is provided, call it and `RequirementPatch.model_validate`.
- If `llm` is None, use a **temporary** fallback that calls existing `parse_budget` / `_extract_category` / `_extract_min_ram_gb` so the suite stays offline — mark this fallback for removal in Task 6 once agent-driven patch updates land.
- Never raise on bad LLM JSON: return empty patch with `ambiguous_fields=["_extract_failed"]`.

- [ ] **Step 4: Run tests — pass**

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/domains/shopping/extract.py apps/api/tests/test_requirement_extract.py
git commit -m "feat: add shopping requirement extract interface"
```

---

### Task 3: Unify `update_requirements` to accept / apply patches

**Files:**
- Modify: `apps/api/app/domains/shopping/tools.py`
- Modify: `apps/api/app/tools.py` (`update_requirements`)
- Test: `apps/api/tests/test_update_requirements_patch.py`

**Interfaces:**
- Consumes: `apply_requirement_patch`, `extract_requirement_patch`
- Produces: tool args may include structured fields; free text triggers extract then apply

- [ ] **Step 1: Failing test — explicit patch wins without regex change cues**

```python
def test_update_requirements_applies_explicit_patch(client_or_toolbox):
    # Via ToolBox or domain tool: pass patch fields for budget_cny=8000
    # Assert session.requirements["budget_cny"] == 8000
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement**

- Extend `_UpdateReq` / `ToolBox.update_requirements` to build `RequirementPatch` from explicit args when provided.
- For free-text-only updates: `await extract_requirement_patch(text, current=req)` then `apply_requirement_patch`.
- Keep `parse_notes` / `needs_clarify` derived from `ambiguous_fields`, not from bare-number regex heuristics alone.
- Preserve “never invent” rule in the tool description.

- [ ] **Step 4: Run targeted tests + `tests/test_agent_budget.py` — pass**

- [ ] **Step 5: Commit**

```bash
git commit -m "refactor: update_requirements applies RequirementPatch"
```

---

### Task 4: Unify `ask_user` / `clarify` into one tool

**Files:**
- Modify: `apps/api/app/domains/shopping/tools.py`
- Modify: `apps/api/app/tools.py` (single `ask_user` host helper storing `pending_questions`)
- Modify: `apps/api/app/domains/shopping/prompt.py`
- Modify: `apps/api/app/domains/shopping/adapter.py` (read questions from pending metadata)
- Test: `apps/api/tests/test_shopping_domain.py`, `tests/test_agent_clarify_runtime.py`

**Interfaces:**
- Produces: one ToolSpec `ask_user` with args:

```python
class _AskQuestion(BaseModel):
    id: str
    question: str
    options: list[str] = Field(default_factory=list)
    field: Literal["budget","category","use_case","must_have","preferences","other"] = "other"

class _AskUser(BaseModel):
    questions: list[_AskQuestion] = Field(min_length=1, max_length=5)
    reason: str = "need_user"
```

- [ ] **Step 1: Failing tests**

- Assert shopping tool registry has `ask_user` and does **not** have `clarify`.
- Assert `ask_user` with one question and empty options still returns `control="wait_user"`.
- Assert multiple questions appear in `MessageResponse.clarify` (keep response field name for API compatibility, or rename to `questions` with alias — prefer keep `clarify` list in response schema for one release to avoid breaking the panel).

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement**

- Remove `clarify` ToolSpec and `_Clarify` handler.
- Host: `ToolBox.ask_user(questions, reason)` sets `status=interrupted`, `pending_questions=questions`, `interrupt_reason=reason`.
- Delete or stop registering separate free-text-only path.
- Update SYSTEM_PROMPT: one tool for all user questions; do not mention `answers`.

- [ ] **Step 4: Fix FakeModel/MockShopper call sites that used `clarify` → `ask_user`**

- [ ] **Step 5: Run shopping/clarify/runtime suites — pass**

- [ ] **Step 6: Commit**

```bash
git commit -m "refactor: merge clarify into a single ask_user wait tool"
```

---

### Task 5: Answers as chat messages (API + extension)

**Files:**
- Modify: `apps/api/app/agent_runner.py`
- Modify: `apps/api/app/schemas.py`, `apps/api/app/main.py`
- Modify: `packages/extension/panel/sidepanel.js`
- Modify: `packages/extension/shared/api.js`
- Test: `apps/api/tests/test_agent_clarify_runtime.py`, `packages/extension/tests/session-view.test.js` (or new panel test)

**Interfaces:**
- Consumes: unified `ask_user` pending questions
- Produces: user message text; no `apply_clarify` on resume

- [ ] **Step 1: Failing API test**

```python
def test_card_reply_is_plain_content_not_answers_map(client):
    # Create session, force interrupted with pending questions (via FakeModel ask_user)
    # POST /messages with content only, e.g. "budget: 5000\ncategory: 笔记本"
    # Assert apply_clarify was not required; agent turn runs; message stored as user role
```

- [ ] **Step 2: Run — fail / show answers path still used**

- [ ] **Step 3: Implement backend**

- `run_agent(session, content, answers=None)`: if `answers` provided (compat), format into a synthetic content string and treat as content; do **not** call `apply_clarify`.
- Clear `pending_questions` when a new user message arrives after interrupt.
- Append user content to `session.messages` as today; enter ReAct with that history.
- Mark `MessageRequest.answers` deprecated (still accepted, converted to content).

Helper to format answers map → content:

```python
def format_answers_as_message(answers: dict[str, str]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in answers.items() if str(value).strip())
```

- [ ] **Step 4: Implement extension**

In `appendClarify` submit handler:

```javascript
const lines = []
for (const [qid, value] of Object.entries(answers)) {
  if (value) lines.push(`${qid}: ${value}`)
}
const content = lines.join('\n')
await sendMessage(state.sessionId, content, undefined, clientMessageId)
```

- Remove `answers` argument from `sendMessage` once unused (or leave ignored for compat).

- [ ] **Step 5: Run API + extension tests — pass**

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: treat clarify card replies as ordinary chat messages"
```

---

### Task 6: Remove hot-path regex NLU / `apply_clarify`

**Files:**
- Modify: `apps/api/app/tools.py`
- Modify: `apps/api/app/domains/shopping/requirements.py`
- Modify: tests that imported `apply_clarify` / `_BUDGET_*` from tools
- Test: budget/clarify/inventory suites

- [ ] **Step 1: Delete or quarantine**

- Remove `ToolBox.apply_clarify` and call sites.
- Remove `update_requirements` reliance on `_BUDGET_CHANGE`, `_QUESTION_MENTION`, `_SPEC_NUMBER` merge logic.
- Keep optional grounding helpers used only inside `extract.py` fallback; or delete fallback and require injected LLM / agent patch in tests.

- [ ] **Step 2: Fix tests that depended on host regex auto-merge**

- Prefer FakeModel that calls `update_requirements` with explicit patch after reading the user message.

- [ ] **Step 3: Full API pytest — pass**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q`

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor: drop apply_clarify and host regex requirement NLU"
```

---

### Task 7: Golden / architecture regression

**Files:**
- Modify: `apps/api/tests/test_golden_turn_budgets.py`, `test_shopping_domain.py`, `test_agent_architecture.py` if needed
- Modify: `docs/superpowers/specs/2026-11-10-unified-ask-and-requirement-extract-design.md` status note if drift

- [ ] **Step 1: Assert tool list**

```python
names = {spec.name for spec in build_shopping_tools(...)}
assert "ask_user" in names
assert "clarify" not in names
```

- [ ] **Step 2: Golden complete-req turn still under step / get_session budgets**

- [ ] **Step 3: Extension `npm test` — pass**

- [ ] **Step 4: Commit**

```bash
git commit -m "test: lock unified ask_user and answers-as-messages behavior"
```

---

## Spec coverage check

| Spec decision | Task |
|---------------|------|
| One wait-user tool | Task 4 |
| Answers as normal messages | Task 5 |
| Typed extract + host apply | Tasks 1–3 |
| Keep constraints deterministic | Global constraint; untouched |
| Drop `apply_clarify` regex hot path | Task 6 |
| FakeModel / golden budgets | Tasks 4, 7 |

## Deferred

- Live-model extract eval corpus (slang budgets, open-ended, RTX false positives).
- Rename API response field `clarify` → `questions`.
- Remove `MessageRequest.answers` entirely after extension ships.
- SSE push for ask_user (panel already polls).
