# Unified Ask + Requirement Extract Design

**Date:** 2026-11-10  
**Status:** Approved  
**Context:** Replace brittle host regex NLU and duplicate `clarify`/`ask_user` tools with one wait-user tool and chat-history continuation; structured requirements updated via typed extract/apply.

## Problem

1. `clarify` and `ask_user` both only set `control="wait_user"`; the split is product ceremony, not a runtime need.
2. Clarify answers travel on a special `answers` map and are merged by host regex (`apply_clarify`, `parse_budget`, category/RAM cues) before the agent plans — incomplete coverage and hard to extend.
3. Free-text requirement parsing in `update_requirements` uses the same regex surface.

## Decisions

### 1. One wait-user tool

Merge into a single shopping tool, e.g. `ask_user`:

```text
ask_user(questions: [{id, question, options?, field?}...], reason?)
→ ToolResult(control="wait_user", output.pending_user with questions in metadata)
```

- One question with empty `options` covers today’s free-text `ask_user`.
- Multiple questions with options cover today’s `clarify`.
- Side Panel keeps card UI as presentation of the same payload; submit becomes a **user chat message**, not a separate `answers` API field.

### 2. Answers are normal messages

- Drop hot-path use of `MessageRequest.answers` and host `apply_clarify`.
- On submit, extension builds a readable user message (natural language and/or `id: value` lines) and POSTs `content` only.
- Agent resumes with that message in chat history and decides the next step.

### 3. Typed requirement extract + host apply

- Introduce `RequirementPatch` (Pydantic): budget fields, category, use_case, must_have, preferences, per-field confidence, ambiguous_fields, optional source spans.
- Extract runs as a **shopping-domain** step (cheap structured LLM call and/or agent tool), not inside `app.agent`.
- Host **apply** only merges a validated patch; never invents constraints the user did not state.
- Low confidence / ungrounded numbers → leave unset and let the agent call `ask_user` again.

### 4. What stays deterministic

- `constraints.py` product eligibility stays rule/regex-based (auditable inventory/recommend).
- Optional regex may remain only as a **grounding veto** (“this digit looks like a spec, not money”), not as primary NLU.

## Non-goals

- LLM-based product title matching.
- SSE push for mid-turn events (already has poll cursor).
- Multi-user auth changes.

## Acceptance

- Single wait-user tool registered for shopping; no `clarify` tool.
- Resume after questions works with `content` only; agent sees the reply in messages.
- Budget/category/must_have updates after a card submit no longer depend on `apply_clarify` regex.
- Existing hard-constraint recommend/inventory tests still pass.
- FakeModel / MockShopper flows updated; golden turns stay under step budgets.
