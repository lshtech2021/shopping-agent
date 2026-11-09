# Shopping Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从零实现「Side Panel 对话 + LangGraph Agent + Extension Tool Host」选品助手，按阶段可提交、可演示。

**Architecture:** FastAPI 托管会话与 WebSocket；LangGraph 执行工具循环；Chrome Extension 执行 browse jobs 并回传；PostgreSQL 持久化。

**Tech Stack:** Python 3.11+、FastAPI、LangGraph、SQLAlchemy、Pydantic v2、Chrome MV3、Node test（扩展侧可选）

**Spec:** `docs/superpowers/specs/2026-11-08-shopping-agent-design.md`

## Global Constraints

- Tool 面仅 9 个命名工具，browse 必须经 Extension/Mock Host，服务端不直连电商页。
- 风控状态必须可 interrupt，禁止自动连刷。
- 每完成一个 Phase 单独 git commit。
- 不依赖旧项目 `ai-shopping-assistant` 代码。

## Review Focus

- Extension 离线时 browse 工具应失败并提示，而不是挂死。
- `rate_limited` 后同站再次 search 必须被配额/状态拒绝直到 resume。
- `recommend` 只能引用会话内已有 product id。
- 空需求 / 空站点应 422，不启动 graph。
- WS 重连后应能领取未完成 job，不丢任务。

---

### Task 1: Docs + repo skeleton

**Files:**
- Create: `README.md`, `docs/superpowers/specs/...`, `docs/superpowers/plans/...`, `.gitignore`

- [x] Write design + plan
- [x] Commit: `docs: shopping-agent design and plan`

---

### Task 2: Phase 0 — API echo + Extension WS skeleton

**Files:**
- Create: `apps/api/` FastAPI app, session create/message echo
- Create: `packages/extension/` MV3 side panel + background WS heartbeat
- Create: `apps/api/requirements.txt`, `docker-compose.yml` (optional postgres)

**Interfaces:**
- `POST /api/v1/sessions` → `{session_id, status}`
- `POST /api/v1/sessions/{id}/messages` → `{reply}` (echo in Phase 0)
- `WS /ws/extension` → `{"type":"ping"}` / `{"type":"pong"}`

- [x] Implement API echo + WS
- [x] Implement extension panel + background
- [x] Smoke test: create session, send message, WS ping
- [x] Commit: `feat: phase0 api echo and extension websocket skeleton`

---

### Task 3: Phase 1 — Memory tools + mock browse loop

**Files:**
- Create: `packages/domain/`, `packages/agent/` LangGraph with 9 tools
- Mock browser host inside API when no extension (dev) OR extension mock mode
- Persist products + recommendations

**Interfaces:**
- Tools: `get_session`, `update_requirements`, `query_inventory`, `search`, `scrape_listing`, `next_page`, `fetch_detail`, `recommend`, `ask_user`
- Message endpoint runs graph until recommend / ask_user / interrupt

- [x] Domain models + persistence (now PostgreSQL)
- [x] Nine tools + mock browse (rule policy; LLM loop added in Phase 5)
- [x] Wire message API to agent
- [x] Tests for mock closed loop
- [x] Commit: `feat: phase1 agent tools and mock browse closed loop`

---

### Task 4: Phase 2 — JD real browse

- [x] Real content parsers, quotas, captcha/rate_limited interrupt
- [x] Commit: `feat: phase2 jd browse adapters and rate-limit interrupts`

---

### Task 5: Phase 3 — eBay + multi-turn

- [x] eBay parsers + live browse jobs
- [x] Multi-turn reanalyze / research (site add/remove, tighter constraints)
- [x] Commit: `feat: phase3 ebay browse and multi-turn reanalyze`

### Task 6: Phase 4 — console + persistence + evals

- [x] Session persistence (PostgreSQL JSONB; `memory` for unit tests)
- [x] Read-only web console at `/console`
- [x] Smoke eval runner
- [x] Commit: `feat: phase4 console persistence and smoke evals`

---

### Task 7: Phase 5 - hardening

- [x] LangGraph tool-loop policy (`llm_agent`) + rule policy fallback
- [x] Risk-site lockout until resume; per-turn quotas; server-side throttle
- [x] Budget/RAM parsing no longer reads spec numbers as price
- [x] Per-session turn lock (409), WS Origin check, CORS tightened
- [x] Disconnect fails in-flight jobs; MV3 keepalive
- [x] Turn-scoped execution logs (no duplicates in Side Panel)
### Task 8: Phase 6 - clarify interview gate

- [x] Structured clarify questions (budget / category / budget-RAM conflict) with options
- [x] Side Panel question card: click options or free text, submit batch / skip
- [x] `answers` merged into requirements without text re-parse clobbering
- [x] `clarify` tool for the LLM policy
- [x] Tests + smoke eval case
- [ ] Commit: `feat: clarify interview gate before browsing`

- [ ] Evaluate LLM policy against rule baseline on real model (needs API key)
- [ ] Split session JSONB into tables; connection pool
