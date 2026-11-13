# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

This is a browser-assisted shopping agent with three parts:

- `apps/api` — FastAPI + LangGraph/LangChain agent, shopping tools, PostgreSQL persistence, WebSocket hub.
- `packages/extension` — Chrome Manifest V3 side panel and browser job host. It owns tabs and executes browse jobs; it does not decide shopping criteria.
- `apps/web` — small read-only console served at `/console/`.

The server is the brain, the extension is the hands, and the database/session store is the source of truth. The design rationale lives in `docs/superpowers/specs/`; implementation history is in `docs/superpowers/plans/`.

## Commands

### Backend

```powershell
docker compose up -d postgres

cd apps/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8100
```

`alembic` reads the database URL from `app.config.settings.database_url` (via `migrations/env.py`), so `SA_DATABASE_URL` / `apps/api/.env` is the source of truth. `SA_AUTO_CREATE_SCHEMA` defaults to `false`; run `alembic upgrade head` instead. `SA_DATABASE_URL=memory` runs without Postgres (data is lost on restart).

Tests:

```powershell
cd apps/api
$env:PYTHONPATH = (Get-Location).Path
python -m pytest -q

# One test file or one test
python -m pytest tests/test_agent_runtime.py -q
python -m pytest tests/test_agent_runtime.py::test_wait_user_control_returns_pending_user -q
```

The suite forces an in-memory store and mock browser in `tests/conftest.py`; persistence tests are skipped unless `SA_TEST_DATABASE_URL` points at a PostgreSQL test database. CI is in `.github/workflows/ci.yml` and runs on Python 3.12.

Smoke evals:

```powershell
cd apps/api
python evals/run_smoke.py          # scripted LLM + mock browser, in-process
python evals/live_server_smoke.py  # boots uvicorn on :8199 with mock browser
```

### Extension

```powershell
cd packages/extension
npm ci
npm test

# One test file
node --test tests/ebay-parser.test.js
```

Load `packages/extension` as an unpacked extension in Chrome. Configure the backend URL and optional token in the extension options; the default backend is `http://127.0.0.1:8100`. After changing adapter/background/content files, reload the extension and confirm the `extension_version` in job logs.

There is no configured lint or typecheck step; CI runs the API pytest suite and the extension `npm test` suite.

## Architecture

### A turn

1. The side panel calls `POST /api/v1/sessions` and then `POST /api/v1/sessions/{id}/messages` (or `/resume`).
2. `apps/api/app/main.py` handles auth, idempotency receipts, and the per-session turn slot. A session allows one active turn; concurrent requests return `409`.
3. `app.agent_runner.run_agent()` calls `app.llm_agent.run_llm_policy()`. That function builds a plan when category + budget are known; otherwise it uses a generic step plan and lets the main LLM extract requirements first.
4. The agent runs through `app.agent.runtime.AgentRuntime` with `PlanExecutePolicy(ReActPolicy(model))`. `ToolBox` is the host-side safety layer; the LLM chooses tools, but `ToolBox` enforces quotas, risk-site lockout, currency/shipping/budget eligibility, and recommendation validity.
5. Browse tools go through `BrowserGateway` → `app.ws_hub.ExtensionHub` → WebSocket job → extension. The extension returns parsed data and `extension_logs`; the backend upserts products into the session.
6. `recommend` and `ask_user` are terminal controls. `update_requirements` can return `wait_user` with a structured clarify question. Clarify cards must carry `pending.metadata.questions` and be mirrored into `Session.pending_questions`.
7. Session state is persisted through `app.store` / `app.store_pg.RunRepository`. Use `durable_touch()` when a turn ends or a durable write is required.

### Generic runtime vs shopping domain

`app/agent/` is deliberately domain-agnostic: runtime, policies, state, checkpointing, critic protocol, and typed tool contracts. Do not put shopping concepts, category names, or business conditions into the generic runtime.

`app/domains/shopping/` is the domain pack: planner/replanner, prompt, requirement review, tools, critic, memory, and API-state translation. Shopping-specific behavior belongs here.

The nine shopping tools are: `get_session`, `update_requirements`, `query_inventory`, `search`, `scrape_listing`, `next_page`, `fetch_detail`, `recommend`, `ask_user`. `ToolBox` lives in `apps/api/app/tools.py`; the LangChain tool wrappers are in `app/domains/shopping/tools.py`.

### Agent/plan invariants

- `ShoppingPlanner` is LLM-driven when requirements are sufficiently complete. The fallback plan contains only generic steps and no generated query.
- `ShoppingReplanner` does not synthesize category/site query suffixes. It clears stale queries and lets the LLM derive new ones from current requirements and critique.
- Once the plan's `recommend` step is `in_progress`, `search` and `next_page` return `skipped` unless the LLM sets `force=true` for an explicitly requested new search/pagination.
- `fit_judge` first filters candidates through deterministic, category-agnostic constraints (`check_product(include_fit=False)`), then asks the LLM only for the remaining products. Verdicts are cached by requirements + detail state.
- Live mode requires `detail_fetched` before a product can be recommended. `recommend` validates product IDs against the session and rejects ineligible items.

### Non-negotiable design constraints

- **Semantic decisions belong to the LLM.** Do not use regex or hardcoded keyword lists to classify requirements, categories, search intent, or product fit. The user explicitly rejected this approach.
- **This is a generic product-selection system.** Category-specific filters, search queries, exclusion terms, and `must_have` values must be inferred by the LLM from the user request and active site. Do not add category→query maps, category branches, or category-specific suffixes in backend code. Deterministic host checks may cover generic concerns only: budget/currency, shipping caps, availability, listing type, duplicate identity, quotas, and workflow state.
- **No silent currency conversion.** Session currency is explicit or page-detected (`settings.currency`). Budgets are always in that currency — never a separate `budget_currency`. eBay detail pages may expose primary and approximate prices; use the page-rendered amount in the session currency when available, otherwise keep the listing price and flag the mismatch. Site switch hard-resets products and the agent checkpoint.
- **Shipping caps are independent.** `max_shipping_ratio` (relative to item price) and `max_shipping_cost` (session currency) both apply; exceeding the absolute cap is a hard veto. They currently apply to eBay because JD has no shipping amount.
- **`must_have` updates are complete-list updates.** When the LLM supplies `must_have` to `update_requirements`, it is authoritative and replaces the previous list. If it is omitted, existing values are preserved.
- **One turn per session.** Do not bypass `_turn_slot`, the supervisor lease, or the turn lock when adding endpoints or background work.
- **Keep browser actions behind the host.** New browse behavior belongs in `ToolBox`/`BrowserGateway`/extension job contracts, not in the agent prompt alone.

### Browser execution and extension

The extension hosts a narrow job protocol over `/ws/extension`:

- `background/service-worker.js` owns the WebSocket and job dispatch.
- `background/browser-jobs.js` owns tab reuse/creation, navigation waits, currency gating, retries, and job result envelopes (`job_id`, `document_url`, `extension_logs`, `extension_version`).
- `content/bridge.js` dispatches DOM messages to the active adapter.
- `adapters/registry.js` selects an adapter by site and URL; adapters are UMD modules.
- eBay/JD adapters live under `adapters/` and use `content/parse-utils.js` for prices, currencies, ratings, and counts.

Important browser rules:

- Site and currency checks fail closed. A page with a different currency blocks browsing; an unknown page currency is not silently treated as the session currency.
- Search jobs wait for listing readiness before currency detection; listing pages prefer result-card price selectors over detail-page selectors.
- Blocked jobs (`rate_limited`, `captcha`, `login_required`, `blocked`) are surfaced to the user; the blocked site is cleared only on resume.
- `sponsored` must come from an explicit badge/label/footer marker; do not scan arbitrary leaf text.
- Parse fixtures live in `packages/extension/tests/fixtures/`. Keep them synthetic and anonymized. Update fixtures/tests when eBay/JD DOM changes; see `packages/extension/README.md` for the detailed parser contract.

### Persistence and observability

- `Session` in `apps/api/app/store.py` is the in-process working object. `apps/api/app/store_pg.py` provides the durable `RunRepository` (runs, actions, observations, run events, artifacts, checkpoints, receipts) and a memory implementation used by tests.
- `runs.settings` stores site, currency/source, and shipping caps; migrations live in `apps/api/migrations/versions/`.
- Execution logs are stored on the session and exposed through `GET /api/v1/sessions/{id}` and `/events`. `turn.summary` aggregates status, LLM/token usage, tool status, exclusions, recommendations, settings, and extension version.
- Large tool payloads can be stored separately (`SA_ARTIFACT_STORAGE_ENABLED`); `/api/v1/artifacts/{id}` returns them. Prometheus metrics are available at `/metrics` only when `SA_METRICS_ENABLED=true`. OpenTelemetry and log redaction/retention are optional `SA_*` settings.

## Conventions

- Keep new code comments and commit messages in English. Existing product/docs copy and user-facing assistant/panel text may remain Chinese.
- Keep LLM prompts generic. Examples may illustrate a rule but must not become a category-specific filter or a backend branch.

## Configuration

All backend settings use the `SA_` prefix and load from `apps/api/.env` (see `.env.example`). The most important ones:

- `SA_DATABASE_URL` — PostgreSQL URL or `memory`.
- `SA_LLM_API_KEY`, `SA_LLM_BASE_URL`, `SA_LLM_MODEL` — OpenAI-compatible LLM; empty key interrupts turns with `llm_unavailable`.
- `SA_MOCK_BROWSER` — `true` for offline mock browsing, `false` for live extension jobs.
- `SA_EXTENSION_TOKEN`, `SA_EXTENSION_ID`, `SA_AUTH_REQUIRED` — extension auth and WebSocket origin restrictions.
- `SA_MAX_SHIPPING_RATIO`, `SA_MAX_SHIPPING_COST` — default caps; sessions can override them.
- `SA_ARTIFACT_STORAGE_ENABLED`, `SA_METRICS_ENABLED`, `SA_OTEL_ENABLED`, `SA_LOG_REDACTION_ENABLED`, `SA_LOG_RETENTION_DAYS` — optional audit/observability features.

## Testing notes

- API tests use `tests/conftest.py`: memory store, mock browser, scripted `MockShopper`, and deterministic fit-judge/review doubles. Do not make tests depend on a live LLM or live sites.
- Extension tests use Node's test runner with `jsdom`; no browser needed.
- `apps/api/tests/test_agent_plan_guard.py`, `test_fit_judge.py`, and the extension `browser-jobs.test.js`/`currency-gate.test.js` cover the recent P0/P1 guards; keep those invariants when changing tool or browser flow.
- The design documents and recent plans are the best source for intent when a change spans backend, extension, and persistence.
