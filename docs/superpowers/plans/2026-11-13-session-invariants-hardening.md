# Session Invariants Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten session invariants so one site, one currency, and fail-closed browse are true in runtime—not only in docs.

**Architecture:** Hard-reset on site switch (including checkpoint); delete `budget_currency` and public `sites[]`; deepen site packs with `eligibility_extra`; make listing browse fail closed on unknown/mismatched currency; keep host-owned search filters with an explicit buffer shown in context.

**Tech Stack:** FastAPI, Pydantic v2, pytest, Chrome MV3 extension (JS), node:test

**Spec:** `docs/superpowers/specs/2026-11-13-session-invariants-hardening-design.md`

## Global Constraints

- Exactly one `settings.site`; never search JD and eBay in the same turn.
- Exactly one money unit: `settings.currency`; no `budget_currency` / `*_cny` / silent FX.
- Site change clears products, plan, searched_sites, recommendations, and agent checkpoint.
- Listing browse fails closed on `site_mismatch` / `currency_mismatch` / `currency_unknown`.
- Site-specific eligibility extras live on the adapter; shared constraints stay site-agnostic + pack hook.
- Commit after each task; FakeModel / MockShopper tests stay green without a live LLM key.
- Do not restore multi-site shopping or prompt-side FX.

## File map

| Path | Role |
|------|------|
| `apps/api/app/store.py` | Drop public `sites` writes; migrate read; site identity helpers |
| `apps/api/app/schemas.py` / `main.py` | API without `sites[]`; site PATCH hard-reset + checkpoint delete |
| `apps/api/app/tools.py` / `constraints.py` / `inventory.py` | Eligibility via `settings.site` + currency only |
| `apps/api/app/domains/shopping/adapters/` | `eligibility_extra`; move eBay shipping/auction rules |
| `apps/api/app/domains/shopping/prompt.py` | Context: currency + effective search cap |
| `apps/api/app/llm_agent.py` / checkpoint store | Invalidate checkpoint on site/currency hard reset |
| `packages/extension/background/browse-guards.js` / `browser-jobs.js` | Fail-closed listing gate; remove escapes |
| `packages/extension/panel/*` | Site switch UX assumes hard reset; no “keep products” |
| Eval smoke scripts | `site` not `sites` |

---

### Task 1: Drop `budget_currency`; budget ∈ `settings.currency` only

**Files:**
- Modify: `apps/api/app/store.py` (normalize: strip/migrate away `budget_currency`)
- Modify: `apps/api/app/tools.py` (update_requirements / patch apply — no budget_currency writes)
- Modify: `apps/api/app/constraints.py` (compare product.currency to settings.currency only)
- Modify: `apps/api/app/domains/shopping/requirements_patch.py` if it mentions budget_currency
- Modify: tests that set/assert `budget_currency`
- Test: `apps/api/tests/test_agent_constraints.py`, `apps/api/tests/test_requirement_patch.py`

**Interfaces:**
- Consumes: `settings.currency`
- Produces: eligibility without `budget_currency`; requirements no longer store it

- [ ] **Step 1: Write failing tests**

```python
def test_budget_compares_in_settings_currency_only():
    product = {"source": "ebay", "price": 100, "currency": "USD", "availability": "available"}
    req = {"budget": 200, "must_have": []}
    settings = {"site": "ebay", "currency": "USD"}
    result = check_eligibility(product, req, settings=settings, site="ebay")
    assert "budget" not in result.violations
    assert "currency" not in result.violations

def test_no_budget_currency_field_in_requirements_after_patch():
    # apply patch with budget; assert "budget_currency" not in out
    ...
```

- [ ] **Step 2: Run to verify fail** (if still dual-field)

Run: `cd apps/api && PYTHONPATH=. .venv/bin/pytest tests/test_agent_constraints.py tests/test_requirement_patch.py -q`

- [ ] **Step 3: Implement** — remove `budget_currency` from apply/update/normalize/eligibility; currency change still clears budget.

- [ ] **Step 4: Full focused + grep**

```bash
rg -n 'budget_currency' apps/api/app apps/api/tests packages/extension
cd apps/api && PYTHONPATH=. .venv/bin/pytest -q
```

Expected: no runtime hits except migration strip; suite green.

- [ ] **Step 5: Commit**

```bash
git add apps/api
git commit -m "$(cat <<'EOF'
refactor(api): drop budget_currency; budget uses settings.currency

EOF
)"
```

---

### Task 2: Single site identity (`settings.site`; retire public `sites[]`)

**Files:**
- Modify: `apps/api/app/store.py`, `schemas.py`, `main.py`
- Modify: `tools.py`, `constraints.py`, `inventory.py`, `planner.py`, `llm_agent.py`, `prompt.py`
- Modify: extension panel/API consumers of `sites`
- Test: `apps/api/tests/test_session_settings.py` and callers

**Interfaces:**
- Produces: `check_eligibility(..., site: str)` (or read only `settings.site`); responses expose `settings.site`; optional deprecated `sites` omitted or derived only in migration tests
- Consumes: legacy `sites[0]` on read only

- [ ] **Step 1: Failing/updated tests** — create/detail/list assert `settings.site`; no requirement on `sites` for new sessions.

- [ ] **Step 2: Implement** — stop writing `Session.sites` (or keep private mirror only if a single internal call still needs it for one task, then delete in same task). Prefer delete field if feasible; else mark unused and stop serializing.

- [ ] **Step 3: Update planner context** to `"site": settings.site` not `"sites": [...]`.

- [ ] **Step 4: Run API suite + fix eval smoke**

```bash
# apps/api/evals/*.py create body: {"requirement": "...", "site": "jd"}
cd apps/api && PYTHONPATH=. .venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor(api): use settings.site as sole site identity

EOF
)"
```

---

### Task 3: Site switch hard reset + checkpoint invalidation

**Files:**
- Modify: `apps/api/app/main.py` (`patch_session_settings`)
- Modify: checkpoint delete API used by `llm_agent` / store (find `PgCheckpointStore` or equivalent delete-by-run)
- Modify: `packages/extension/panel/sidepanel.js` (assume products cleared; no keep-products copy)
- Test: `apps/api/tests/test_session_settings.py`

**Interfaces:**
- On site change: clear products, recommendations, plan, searched_sites; `checkpoint_store.delete(session_id)` (exact method name from codebase)
- Currency change that conflicts with stored product currencies: clear those products (or all products if any differ)

- [ ] **Step 1: Failing test**

```python
def test_site_change_clears_products_and_checkpoint(client, monkeypatch):
    # create session site=jd, seed product + fake checkpoint
    # PATCH site=ebay
    # assert products == [], plan is None, checkpoint gone
    ...

def test_resume_after_site_change_does_not_restore_old_plan(...):
    # interrupted session with checkpoint plan for jd
    # PATCH site=ebay (clears checkpoint)
    # resume → session.plan remains None / new adapter context
    ...
```

- [ ] **Step 2: Run fail**

- [ ] **Step 3: Implement hard reset + checkpoint delete**; panel: drop any “keep products” messaging.

- [ ] **Step 4: pytest focused + full API**

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
fix(api): hard-reset session on site switch including checkpoint

EOF
)"
```

---

### Task 4: Fail-closed listing browse gate

**Files:**
- Modify: `packages/extension/background/browse-guards.js`
- Modify: `packages/extension/background/browser-jobs.js` (`guardTab`)
- Modify: `apps/api/app/domains/shopping/prompt.py` (`blocked_text` for `currency_unknown`)
- Test: `packages/extension/tests/currency-gate.test.js`, `browser-jobs` tests if present

**Interfaces:**
- `assertBrowseAllowed` / `guardTab` for search, scrape_listing, next_page:
  - missing settings → blocked (`settings_missing`) when `run_id` present
  - page currency null + session currency set → `currency_unknown` (not continue)
  - remove `SITE_CHECK` sentinel
- Detail: may skip **listing** currency equality only if product listing currency already recorded; do not use silent “allow null page currency” for listing tools

- [ ] **Step 1: Failing tests**

```js
test('blocks when page currency unknown and session currency set', () => {
  const result = assertBrowseAllowed({
    sessionSite: 'ebay',
    sessionCurrency: 'USD',
    tabUrl: 'https://www.ebay.com/sch/i.html',
    pageCurrency: null,
  })
  assert.equal(result.error, 'currency_unknown')
})
```

- [ ] **Step 2: Implement gate + wire `guardTab`**

- [ ] **Step 3: `cd packages/extension && npm test`**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
fix(extension): fail closed when listing page currency is unknown

EOF
)"
```

---

### Task 5: Adapter `eligibility_extra` + move eBay rules out of shared constraints

**Files:**
- Modify: `apps/api/app/domains/shopping/adapters/base.py`
- Modify: `apps/api/app/domains/shopping/adapters/ebay/__init__.py`
- Modify: `apps/api/app/domains/shopping/adapters/jd/__init__.py` (return `[]`)
- Modify: `apps/api/app/constraints.py` (call pack hook; remove `if site == "ebay"` shipping/auction branches)
- Test: `apps/api/tests/test_site_adapters.py`, `test_agent_constraints.py`

**Interfaces:**

```python
def eligibility_extra(
    self,
    product: dict[str, Any],
    settings: dict[str, Any],
    requirements: dict[str, Any],
) -> list[str]: ...
```

- [ ] **Step 1: Failing test** — auction + budget → violation via ebay adapter path; constraints has no `== "ebay"` for that rule.

- [ ] **Step 2: Implement move**; grep `ebay` in `constraints.py` for remaining policy branches.

- [ ] **Step 3: pytest**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor(api): move site eligibility extras into adapters

EOF
)"
```

---

### Task 6: Explicit search buffer in settings/context

**Files:**
- Modify: `apps/api/app/domains/shopping/adapters/base.py` (read buffer from settings)
- Modify: `apps/api/app/store.py` / create defaults (`search_price_buffer: 0.85`)
- Modify: `apps/api/app/domains/shopping/prompt.py` (`context_block` shows effective cap)
- Test: `apps/api/tests/test_site_adapters.py`, prompt/context test

- [ ] **Step 1: Test** — eBay filters use `settings.search_price_buffer`; context string includes search cap when budget set.

- [ ] **Step 2: Implement**

- [ ] **Step 3: pytest focused**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(api): expose search price buffer in settings and context

EOF
)"
```

---

### Task 7: Docs alignment + leftover cleanup

**Files:**
- Modify: `docs/superpowers/specs/2026-11-11-site-adapters-and-session-currency-design.md` (supersede pointer for §4 / `sites[]`)
- Grep: `sites`, `budget_currency`, `SITE_CHECK`, `keep products`
- Fix any remaining panel copy / eval scripts

- [ ] **Step 1: Amend 2026-11-11 design** with:

```markdown
## Superseded (2026-11-13)

- §4 “keep products” on site switch → see `2026-11-13-session-invariants-hardening-design.md` (hard reset).
- Dual `sites[]` mirror → `settings.site` only.
- Fail-open unknown page currency → fail-closed `currency_unknown`.
```

- [ ] **Step 2: Grep gate**

```bash
rg -n 'budget_currency|SITE_CHECK' apps/api/app packages/extension
rg -n '"sites"' apps/api/evals packages/extension/panel packages/extension/shared
```

- [ ] **Step 3: Full suites**

```bash
cd apps/api && PYTHONPATH=. .venv/bin/pytest -q
cd packages/extension && npm test
```

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
docs: align site-currency design with session invariant hardening

EOF
)"
```

---

## Spec coverage

| Spec decision | Task |
|---|---|
| Single currency / drop `budget_currency` | 1 |
| Single site / retire `sites[]` | 2 |
| Site switch hard reset + checkpoint | 3 |
| Fail-closed browse | 4 |
| `eligibility_extra` | 5 |
| Explicit search buffer | 6 |
| Docs truth | 7 |

## Execution notes

- Prefer deleting dual fields over shims.
- When clearing checkpoint, use the existing store API; do not invent a second checkpoint path.
- Panel site-switch: refresh snapshot after PATCH; resume only if still interrupted/blocked after reset.
