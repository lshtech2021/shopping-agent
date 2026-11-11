# Site Adapters + Session Currency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One active site and one session currency; paired backend/extension site adapters; host-owned search filters; no `_cny` suffixes and no FX in LLM prompts.

**Architecture:** Session `settings.{site,currency,currency_source}` is the single source of truth. Backend `get_adapter(site)` and extension `adapters[site]` own prompts, parsers, search URL/filters, and budget eligibility. Shared runtime (agent loop, inventory store, fit-judge, WS broker) stays site-agnostic. Browse jobs fail closed on `site_mismatch` / `currency_mismatch`.

**Tech Stack:** FastAPI, Pydantic v2, pytest, Chrome MV3 extension (JS), node:test

**Spec:** `docs/superpowers/specs/2026-11-11-site-adapters-and-session-currency-design.md`

## Global Constraints

- Exactly one active `settings.site` per session; never search JD and eBay in the same turn.
- Exactly one `settings.currency` per session; never silently FX-convert budget or inventory.
- No new or retained `*_cny` field names in schemas, store, prompts, or tests after cutover.
- LLM prompts must not instruct currency conversion; adapters/`build_search` own max-price filters.
- `app.agent` must not import `app.domains` (existing architecture test).
- Commit after each task; keep FakeModel / MockShopper tests green without a live LLM key.
- One-version migration: normalize legacy `sites[]` / `budget_cny` / `price_cny` on read; do not keep dual writes.

## File map

| Path | Role |
|------|------|
| `apps/api/app/store.py` | `Session.settings`; migrate on `from_dict` / `create` |
| `apps/api/app/schemas.py` / `main.py` | Singular `site`; `PATCH .../settings`; snapshot fields |
| `apps/api/app/domains/shopping/requirements_patch.py` | `budget` / `budget_min` (not `_cny`) |
| `apps/api/app/constraints.py` | Budget vs listing `price`+`currency` vs `settings.currency` |
| `apps/api/app/inventory.py` | Drop `price_cny` / `to_cny` from product upsert path |
| `apps/api/app/domains/shopping/adapters/` | `SiteAdapter` protocol + `jd` / `ebay` packs |
| `apps/api/app/domains/shopping/prompt.py` | Inject session currency; remove FX / multi-site wording |
| `apps/api/app/domains/shopping/tools.py` | Host uses adapter; ignore LLM `site` |
| `apps/api/app/tools.py` | Dispatch browse with session site; settings helpers |
| `packages/extension/adapters/` | Registry + jd/ebay packs (parsers, search, currency) |
| `packages/extension/background/browser-jobs.js` | Resolve pack; currency/site gates |
| `packages/extension/shared/api.js` / `panel/*` | Singular site UI; settings PATCH; mismatch UX |

---

### Task 1: Session `settings` + singular `site` API

**Files:**
- Modify: `apps/api/app/store.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_session_settings.py` (create)

**Interfaces:**
- Produces:
  - `Session.settings: dict` with keys `site: str`, `currency: str | None`, `currency_source: "detected" | "manual" | None`
  - `normalize_session_dict(data: dict) -> dict` applied in `Session.from_dict`
  - `store.create(requirement: str, site: str) -> Session`
  - `CreateSessionRequest.site: Literal["jd","ebay"]`
  - `PATCH /api/v1/sessions/{id}/settings` body `{ site?: Literal["jd","ebay"], currency?: str }`
- Consumes: existing `Session` / `SessionStore` persistence

- [ ] **Step 1: Write the failing tests**

```python
# apps/api/tests/test_session_settings.py
from app.store import Session, SessionStore


def test_create_sets_singular_site_settings(tmp_path, monkeypatch):
    store = SessionStore("memory")
    session = store.create("买笔记本", "jd")
    assert session.settings["site"] == "jd"
    assert session.settings.get("currency") is None
    assert session.sites == ["jd"]  # derived mirror for one release of callers


def test_from_dict_migrates_legacy_sites_list():
    raw = {
        "id": "abc",
        "requirement": "x",
        "sites": ["ebay", "jd"],
        "requirements": {"budget_cny": 5000, "must_have": []},
        "products": {},
    }
    session = Session.from_dict(raw)
    assert session.settings["site"] == "ebay"  # first entry
    assert session.requirements.get("budget") == 5000
    assert "budget_cny" not in session.requirements


def test_patch_settings_changes_site(client):
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本", "site": "jd"},
    ).json()
    sid = created["session_id"]
    resp = client.patch(
        f"/api/v1/sessions/{sid}/settings",
        json={"site": "ebay", "currency": "USD"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["site"] == "ebay"
    assert body["settings"]["currency"] == "USD"
    assert body["settings"]["currency_source"] == "manual"
```

Adapt `client` fixture to whatever `conftest.py` already exposes (TestClient). If no client fixture, use FastAPI `TestClient(app)` inline.

- [ ] **Step 2: Run to verify fail**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_session_settings.py -q`

Expected: FAIL (missing module / `site` field / `settings`)

- [ ] **Step 3: Implement normalize + store + API**

```python
# store.py helpers (sketch)
ALLOWED_SITES = ("jd", "ebay")

def normalize_session_dict(data: dict) -> dict:
    data = dict(data)
    settings = dict(data.get("settings") or {})
    sites = list(data.get("sites") or [])
    site = settings.get("site") or (sites[0] if sites else None)
    if site not in ALLOWED_SITES:
        site = "jd"
    settings["site"] = site
    if "currency" not in settings:
        settings["currency"] = None
    if "currency_source" not in settings:
        settings["currency_source"] = None
    data["settings"] = settings
    data["sites"] = [site]

    req = dict(data.get("requirements") or {})
    if "budget" not in req and "budget_cny" in req:
        req["budget"] = req.pop("budget_cny")
    elif "budget_cny" in req:
        req.pop("budget_cny", None)
    if "budget_min" not in req and "budget_min_cny" in req:
        req["budget_min"] = req.pop("budget_min_cny")
    elif "budget_min_cny" in req:
        req.pop("budget_min_cny", None)
    data["requirements"] = req

    products = dict(data.get("products") or {})
    for pid, product in list(products.items()):
        p = dict(product)
        p.pop("price_cny", None)
        p.pop("price_max_cny", None)
        products[pid] = p
    data["products"] = products
    return data
```

- Add `settings: dict` to `Session` dataclass; `from_dict` calls `normalize_session_dict` first.
- `create(requirement, site)` sets `settings={site, currency: None, currency_source: None}`, `sites=[site]`, requirements with `budget` (not `budget_cny`).
- Schemas: `CreateSessionRequest.site`; keep reading legacy `sites` only inside normalize, not on create request.
- `SessionDetail` / `CreateSessionResponse` / `SessionSummary`: add `settings`; `sites` may remain as `[settings.site]` for one cutover if needed by panel, then Task 7 drops multi select.
- `PATCH /api/v1/sessions/{session_id}/settings`: validate site; if `currency` provided set `currency_source="manual"`; if `site` changes and currencies will be handled in Task 6 (for now just update site + reload later).

- [ ] **Step 4: Run tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_session_settings.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/store.py apps/api/app/schemas.py apps/api/app/main.py apps/api/tests/test_session_settings.py
git commit -m "$(cat <<'EOF'
feat(api): add session settings and singular site

EOF
)"
```

---

### Task 2: Rename money fields (`budget`, drop `price_cny`)

**Files:**
- Modify: `apps/api/app/domains/shopping/requirements_patch.py`
- Modify: `apps/api/app/constraints.py`
- Modify: `apps/api/app/inventory.py`
- Modify: `apps/api/app/tools.py`
- Modify: `apps/api/app/domains/shopping/tools.py`
- Modify: `apps/api/app/domains/shopping/planner.py`
- Modify: `apps/api/tests/conftest.py` and all tests still using `budget_cny` / `price_cny`
- Test: `apps/api/tests/test_requirement_patch.py`, `apps/api/tests/test_agent_constraints.py`

**Interfaces:**
- Consumes: `Session.settings` from Task 1
- Produces:
  - `RequirementPatch.budget` / `budget_min` (rename from `*_cny`)
  - `check_eligibility(..., settings: dict)` uses `product.price` when `product.currency == settings.currency`
  - `upsert_listing` no longer writes `price_cny` / `price_max_cny`
  - Remove hot-path use of `to_cny` (delete function or leave unused until Task 8 deletes `fx_rates`)

- [ ] **Step 1: Write / update failing tests**

```python
def test_apply_sets_budget():
    from app.domains.shopping.requirements_patch import (
        RequirementPatch,
        apply_requirement_patch,
    )
    out = apply_requirement_patch(
        {"budget": None, "must_have": [], "preferences": []},
        RequirementPatch(budget=5000, confidence={"budget": "high"}),
    )
    assert out["budget"] == 5000
    assert "budget_cny" not in out


def test_budget_requires_matching_currency():
    from app.constraints import check_eligibility
    product = {
        "source": "ebay",
        "price": 500,
        "currency": "USD",
        "availability": "available",
    }
    req = {"budget": 600, "must_have": []}
    settings = {"site": "ebay", "currency": "USD"}
    ok = check_eligibility(product, req, settings=settings, sites=["ebay"])
    assert not ok.violations or "budget" not in ok.violations

    settings_cny = {"site": "ebay", "currency": "CNY"}
    bad = check_eligibility(product, req, settings=settings_cny, sites=["ebay"])
    assert "budget" in bad.violations  # currency mismatch → ineligible
```

Update `seed_session` / `seed_product` in `conftest.py` to use `budget` and omit `price_cny`.

- [ ] **Step 2: Run subset to verify fail**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_requirement_patch.py tests/test_agent_constraints.py -q`

Expected: FAIL on renamed fields / currency rule

- [ ] **Step 3: Implement**

- Rename patch + apply + tool args (`update_requirements`) to `budget` / `budget_min`.
- `constraints.check_eligibility`: if `settings.currency` set and `product.currency` differs → `budget` violation (or dedicated reason — use `budget` for simplicity unless excluded reasons already enumerate codes; keep `budget`).
- Compare `float(price)` / range max to `requirements.budget` in session currency only.
- `inventory.upsert_listing` / detail merge: stop calling `to_cny`; stop storing `price_cny`.
- `inventory_summary`: show min/max of `price` only for products matching session currency (pass currency in) or show `{currency} min-max`.
- Grep and fix all tests: `budget_cny`→`budget`, drop `price_cny` assertions (assert `price` + `currency` instead).

- [ ] **Step 4: Run focused + broader API tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_requirement_patch.py tests/test_agent_constraints.py tests/test_agent_inventory.py tests/test_ebay_inventory.py tests/test_plan_requirements_sync.py -q`

Expected: PASS (update `to_cny` unit tests: delete or replace with “no conversion” assertions)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app apps/api/tests
git commit -m "$(cat <<'EOF'
refactor(api): use session currency budget without _cny fields

EOF
)"
```

---

### Task 3: Backend `SiteAdapter` packs (JD + eBay)

**Files:**
- Create: `apps/api/app/domains/shopping/adapters/__init__.py`
- Create: `apps/api/app/domains/shopping/adapters/base.py`
- Create: `apps/api/app/domains/shopping/adapters/jd/__init__.py`
- Create: `apps/api/app/domains/shopping/adapters/ebay/__init__.py`
- Modify: `apps/api/app/domains/shopping/prompt.py` (split shared vs pack extras)
- Modify: `apps/api/app/domains/shopping/tools.py` (search filter building via adapter)
- Test: `apps/api/tests/test_site_adapters.py`

**Interfaces:**
- Produces:

```python
# adapters/base.py
from typing import Any, Literal, Protocol

class SearchPlan(dict):  # or TypedDict
    # keys: tool args / filters dict passed to browser job
    ...

class SiteAdapter(Protocol):
    id: Literal["jd", "ebay"]

    def system_prompt_extra(self, settings: dict[str, Any], requirements: dict[str, Any]) -> str: ...
    def build_search_filters(
        self,
        *,
        query: str,
        requirements: dict[str, Any],
        settings: dict[str, Any],
        max_price: float | None,
    ) -> dict[str, Any]: ...
    def eligible_for_budget(
        self,
        product: dict[str, Any],
        settings: dict[str, Any],
        budget: float | None,
    ) -> bool: ...
    def prefilter(
        self,
        products: list[dict[str, Any]],
        requirements: dict[str, Any],
    ) -> list[dict[str, Any]]: ...

def get_adapter(site: str) -> SiteAdapter: ...
```

- Consumes: session `settings`, requirements `budget`

- [ ] **Step 1: Failing tests**

```python
from app.domains.shopping.adapters import get_adapter


def test_get_adapter_jd_and_ebay():
    assert get_adapter("jd").id == "jd"
    assert get_adapter("ebay").id == "ebay"


def test_ebay_build_search_filters_uses_session_currency_budget():
    adapter = get_adapter("ebay")
    filters = adapter.build_search_filters(
        query="laptop",
        requirements={"budget": 5000},
        settings={"site": "ebay", "currency": "CNY"},
        max_price=None,
    )
    # Host-owned: maxPrice comes from budget in session currency (no FX)
    assert filters.get("maxPrice") == 5000


def test_ebay_prompt_extra_has_no_fx_instruction():
    text = get_adapter("ebay").system_prompt_extra(
        {"site": "ebay", "currency": "USD"},
        {"budget": 500},
    )
    assert "convert" not in text.lower()
    assert "CNY" not in text or "budget is in USD" in text.lower() or "USD" in text
```

- [ ] **Step 2: Run fail**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_site_adapters.py -q`

Expected: FAIL import

- [ ] **Step 3: Implement packs**

- `jd`: filters for JD search as today; prompt extras for JD listing quirks; `eligible_for_budget` = currency match + price ≤ budget.
- `ebay`: map `maxPrice` ← `max_price or requirements.budget` in **session currency**; move eBay keyword/`category_id` guidance from shared `SYSTEM_PROMPT` into `system_prompt_extra`.
- Shared `SYSTEM_PROMPT`: remove “Convert a CNY budget…”, remove “Sites come from the session (Side Panel selection); do not invent or drop sites” multi-site tone; replace with “Active site and currency come from session settings; do not change them via tools.”
- `context_block`: show `settings.site` and `settings.currency`, not `sites` list.

- [ ] **Step 4: Wire tools to adapter**

In `domains/shopping/tools.py` search handler: resolve `get_adapter(session.settings["site"])`, call `build_search_filters`, pass filters to browse dispatch. Ignore/drop LLM-provided `site` arg (or remove from tool schema).

- [ ] **Step 5: Run tests**

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_site_adapters.py tests/test_agent_prompt_boundary.py tests/test_shopping_domain.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/domains/shopping/adapters apps/api/app/domains/shopping/prompt.py apps/api/app/domains/shopping/tools.py apps/api/tests/test_site_adapters.py
git commit -m "$(cat <<'EOF'
feat(api): add jd/ebay site adapter packs

EOF
)"
```

---

### Task 4: Extension adapter registry + pack move

**Files:**
- Create: `packages/extension/adapters/registry.js`
- Create: `packages/extension/adapters/jd/index.js` (re-export/move from `content/jd.js`)
- Create: `packages/extension/adapters/ebay/index.js` (re-export/move from `content/ebay.js`)
- Modify: `packages/extension/background/browser-jobs.js`
- Modify: `packages/extension/content/jd.js` / `ebay.js` (thin register or re-export)
- Test: `packages/extension/tests/adapter-registry.test.js`
- Keep existing parser tests importing pack API

**Interfaces:**
- Produces:

```js
// registry.js
export function getAdapter(site) // throws if missing
// pack shape:
// { id, matchUrl(url), detectCurrency(doc, url), buildSearchUrl(query, filters),
//   parseListing, parseDetail, nextPageUrl, detectBlock }
```

- Consumes: existing parse/search helpers currently in `content/*.js` and `buildSearchUrl` in `browser-jobs.js`

- [ ] **Step 1: Failing test**

```js
const test = require('node:test')
const assert = require('node:assert/strict')
const { getAdapter } = require('../adapters/registry.js')

test('registry returns jd and ebay packs', () => {
  assert.equal(getAdapter('jd').id, 'jd')
  assert.equal(getAdapter('ebay').id, 'ebay')
})

test('ebay detectCurrency reads from sample listing currency default', () => {
  const ebay = getAdapter('ebay')
  assert.equal(typeof ebay.detectCurrency, 'function')
})
```

- [ ] **Step 2: Run fail**

Run: `cd packages/extension && node --test tests/adapter-registry.test.js`

Expected: FAIL missing module

- [ ] **Step 3: Implement registry; move `buildSearchUrl` site branches into packs**

- Extract eBay `_udhi` / JD URL builders into pack `buildSearchUrl(query, filters)`.
- `browser-jobs.js` `runBrowserJob`: `const adapter = getAdapter(args.site || sessionSite)`; call adapter methods; delete top-level `if (site === 'jd')` search URL branches.
- Content scripts: keep injecting on hosts; export parse functions from pack files; `content/jd.js` can `module.exports = require('../adapters/jd')` if test paths require it.

- [ ] **Step 4: Run extension tests**

Run: `cd packages/extension && node --test tests/adapter-registry.test.js tests/ebay-parser.test.js tests/jd-parser.test.js tests/search-filters.test.js tests/browser-jobs.test.js`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/extension/adapters packages/extension/background/browser-jobs.js packages/extension/content packages/extension/tests
git commit -m "$(cat <<'EOF'
feat(extension): resolve site behavior through adapter registry

EOF
)"
```

---

### Task 5: Currency detect, browse gates, settings sync

**Files:**
- Modify: `packages/extension/adapters/jd/index.js` (`detectCurrency` → `'CNY'`)
- Modify: `packages/extension/adapters/ebay/index.js` (`detectCurrency` from page / price text / `#gh-eb-Geo` or existing parse currency heuristics)
- Modify: `packages/extension/background/browser-jobs.js`
- Modify: `packages/extension/shared/api.js` (`patchSessionSettings`, pass session settings into jobs)
- Modify: `apps/api/app/main.py` (ensure PATCH clears products when requested)
- Modify: `apps/api/app/tools.py` / browse dispatch to surface `currency_mismatch` / `site_mismatch`
- Test: `packages/extension/tests/currency-gate.test.js`, `apps/api/tests/test_session_settings.py` (extend)

**Interfaces:**
- Produces:
  - Browse job precondition: tab URL matches adapter `matchUrl`; `detectCurrency(doc) === settings.currency` else `{ ok:false, error:'currency_mismatch', page_currency, session_currency }`
  - `PATCH settings` with `{ clear_products: true, currency }` for adopt-page-currency path
  - Site switch same currency: keep products; different currency without clear: reject or return mismatch for UI

- [ ] **Step 1: Failing tests**

```js
// currency-gate.test.js — unit-test the gate helper extracted from browser-jobs
const test = require('node:test')
const assert = require('node:assert/strict')
const { assertBrowseAllowed } = require('../background/browse-guards.js')

test('currency_mismatch when page differs', () => {
  const result = assertBrowseAllowed({
    sessionSite: 'ebay',
    sessionCurrency: 'CNY',
    tabUrl: 'https://www.ebay.com/sch/i.html',
    pageCurrency: 'USD',
  })
  assert.equal(result.error, 'currency_mismatch')
})

test('allows when currencies match', () => {
  const result = assertBrowseAllowed({
    sessionSite: 'ebay',
    sessionCurrency: 'USD',
    tabUrl: 'https://www.ebay.com/sch/i.html',
    pageCurrency: 'USD',
  })
  assert.equal(result.ok, true)
})
```

```python
def test_patch_clear_products_adopts_currency(client):
    # create session, seed product via store, patch with clear_products + currency
    ...
    assert detail["products"] == []
    assert detail["settings"]["currency"] == "USD"
```

- [ ] **Step 2: Run fail**

Run extension + API tests for new files.

Expected: FAIL missing `browse-guards` / clear behavior

- [ ] **Step 3: Implement**

- Add `packages/extension/background/browse-guards.js` with `assertBrowseAllowed`.
- On search/scrape/next/detail: load session settings (from action args or cached session); detect currency via adapter; gate.
- When opening site tab after create/switch: detect currency; if session currency is `null`, `patchSessionSettings({ currency, currency_source: 'detected' })`.
- API PATCH: accept `clear_products: bool`; if true, clear `products` + `recommendations`; set currency.
- If PATCH changes `site` only and existing products’ currencies conflict with new detection — Task 6 UI drives clear; API may accept `clear_products` flag from panel.

- [ ] **Step 4: Run tests**

Run: extension currency-gate + browser-jobs; API session settings tests.

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/extension apps/api
git commit -m "$(cat <<'EOF'
feat: gate browse on session currency and site match

EOF
)"
```

---

### Task 6: Side panel — one site, currency, mismatch UX

**Files:**
- Modify: `packages/extension/panel/sidepanel.html`
- Modify: `packages/extension/panel/sidepanel.js`
- Modify: `packages/extension/panel/sidepanel.css` (minimal)
- Modify: `packages/extension/shared/api.js`
- Test: `packages/extension/tests/session-view.test.js` (and small DOM/unit helpers if present)

**Interfaces:**
- Consumes: `createSession(requirement, site)`, `patchSessionSettings(id, {site,currency,clear_products})`
- Produces: radio site select; currency display + manual select; on mismatch show two actions (align page vs clear+adopt)

- [ ] **Step 1: Update create + settings client**

```js
export function createSession(requirement, site) {
  return request('/api/v1/sessions', {
    method: 'POST',
    body: JSON.stringify({ requirement, site }),
  })
}

export function patchSessionSettings(sessionId, patch) {
  return request(`/api/v1/sessions/${sessionId}/settings`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}
```

- [ ] **Step 2: UI changes**

- Replace checkboxes with radio: 京东 / eBay (one only).
- In chat header: show `站点: ebay · 货币: USD` from snapshot `settings`.
- Manual currency `<select>` → PATCH `{ currency, currency_source implied manual }`.
- On browse/tool error `currency_mismatch`: show banner with buttons:
  1. “我已改好网站货币” → re-detect / resume
  2. “清空商品并改用页面货币” → `patchSessionSettings({ currency: page_currency, clear_products: true })` then resume
- Site switch control: PATCH `{ site }`; if API/extension returns mismatch, same banner.

- [ ] **Step 3: Manual smoke / unit**

Run: `cd packages/extension && node --test tests/session-view.test.js`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add packages/extension/panel packages/extension/shared/api.js packages/extension/tests
git commit -m "$(cat <<'EOF'
feat(extension): single-site panel with currency controls

EOF
)"
```

---

### Task 7: Prompt/context cleanup + delete leftovers

**Files:**
- Modify: `apps/api/app/domains/shopping/prompt.py` (final wording)
- Modify: `apps/api/app/config.py` (remove `fx_rates` if unused)
- Modify: `apps/api/app/inventory.py` (delete `to_cny`)
- Grep-driven cleanup across `apps/api` + `packages/extension` + docs references in code comments
- Test: `apps/api/tests/test_agent_prompt_boundary.py`, full API + extension suites

**Interfaces:**
- Produces: zero matches for `budget_cny|price_cny|to_cny|fx_rates` in runtime code (tests may mention migration only in `normalize_session_dict` tests)

- [ ] **Step 1: Grep gate (fail if dirty)**

```bash
rg -n 'budget_cny|price_cny|price_max_cny|budget_min_cny|to_cny|fx_rates' apps/api/app packages/extension --glob '!**/node_modules/**'
```

Expected before cleanup: hits; after: only migration comments in `normalize_session_dict` if any, or zero.

- [ ] **Step 2: Fix prompt assertions**

```python
def test_system_prompt_has_no_fx():
    from app.domains.shopping.prompt import SYSTEM_PROMPT
    assert "Convert a CNY" not in SYSTEM_PROMPT
    assert "price_cny" not in SYSTEM_PROMPT
```

- [ ] **Step 3: Full verify**

Run:

```bash
cd apps/api && PYTHONPATH=. .venv/bin/pytest -q
cd packages/extension && node --test
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add apps/api packages/extension
git commit -m "$(cat <<'EOF'
chore: remove _cny fields and prompt-side FX

EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|---|---|
| `settings.{site,currency,currency_source}` | 1, 5, 6 |
| Singular `site` create / no parallel sites | 1, 6 |
| `budget` without `_cny`; drop `price_cny` | 2, 7 |
| No FX in prompts; host/adapter max-price | 3, 7 |
| Paired backend + extension adapters | 3, 4 |
| Detect + manual currency | 5, 6 |
| `currency_mismatch` / align-or-clear | 5, 6 |
| Mid-session switch keep products if same currency | 5, 6 |
| Migration legacy `sites` / `budget_cny` | 1, 2 |
| Browse tools ignore LLM site | 3 |
| Delete leftovers | 7 |

## Execution notes

- Prefer adapter moves that re-export existing parsers first (green tests), then delete duplicate branches.
- When updating FakeModel golden turns, expect tool args without `site` and requirements with `budget`.
- Do not implement silent FX “for convenience” if a test seems to need `price_cny` — fix the test to session-currency matching instead.
