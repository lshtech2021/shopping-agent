# Site Adapters + Session Currency Design

**Date:** 2026-11-11  
**Status:** Approved  
**Context:** One active site per session; one session currency (no `_cny` suffixes); paired extension/backend site adapters; host-owned search filters; no FX in LLM prompts.

## Problem

1. Sessions allow `sites: []` and tools pass `site` per call, so JD and eBay can interleave despite different page structure, workflows, and query methods.
2. Money is stored as `budget_cny` / `price_cny` while eBay pages often display another currency; the prompt asks the model to convert, which produces wrong `_udhi` filters.
3. Extension and API both branch on `if site === "jd"` / `"ebay"` instead of loading a coherent site pack (prompts, parsers, search, constraints).

## Goals

- Exactly one **active site** and one **currency** per session, in session `settings`.
- Choosing a site loads matching **backend + extension adapters** (prompts, parsers, search, prefilters).
- Currency comes from extension detection when the site opens; user may override manually.
- Agent never converts currency in prompts; host/adapter builds filters in `settings.currency`.
- Mid-session site switch is allowed; products may be kept when currency rules allow.

## Non-goals

- Shopping JD and eBay in parallel in one turn.
- Silent FX conversion of inventory or budget.
- A generic capability-flag plugin framework beyond two concrete packs.
- Changing fit-judge / critic core algorithms (they stay site-agnostic callers).

## Decisions

### 1. Session settings (global, not inside requirements)

```ts
settings: {
  site: "jd" | "ebay"
  currency: string              // ISO-like code, e.g. CNY, USD
  currency_source: "detected" | "manual"
}
```

- Create session takes singular `site`, not `sites[]`.
- `PATCH /sessions/{id}/settings` is the only API to change `site` or set currency manually.
- Snapshot/detail exposes `settings`; multi-site list fields leave the happy path.

### 2. Money contract (no `_cny` suffix)

| Field | Meaning |
|---|---|
| `requirements.budget` | Number in `settings.currency` |
| `product.price` / `shipping_cost` / `landed_cost` | Amounts in the **listing** `product.currency` |
| Search max-price (e.g. eBay `_udhi`) | Built by host/adapter in `settings.currency` |

- Drop `budget_cny` and `price_cny`.
- Budget eligibility: compare only when `product.currency === settings.currency`; otherwise product is ineligible.
- Prompts state “budget is in {settings.currency}”; never instruct the model to convert.

### 3. Currency lifecycle

1. User chooses site → extension opens/binds tab → `detectCurrency` → if session currency unset or still `detected`, PATCH `currency` + `source: detected`.
2. User may set currency in the panel → `source: manual`.
3. **Browse gate:** before search / scrape / next_page / detail, `page_currency` must equal `settings.currency`. On mismatch return `currency_mismatch` (no silent continue).
4. UI / agent options on mismatch:
   - User changes the **site page** display currency to match session; extension re-detects; gate passes.
   - **Clear products** (and recommendations), set `settings.currency` to page currency (`source: detected`), then continue.

### 4. Mid-session site switch

1. `PATCH settings.site` → backend and extension load the new pack; bind/open that site’s tab; detect currency.
2. If detected currency **equals** session currency → **keep** products; continue with new adapter.
3. If **different** → apply the currency gate (align page currency **or** clear products + adopt page currency). No auto FX on existing prices.
4. Non-money requirements stay. Leftover products whose listing currency ≠ session currency remain ineligible until cleared or currency aligned.

### 5. Paired site packs (approach)

One pack id (`jd` | `ebay`) with matching halves. Shared runtime does not branch on site strings.

**Backend `SiteAdapter` (Protocol):**

- `id`
- `system_prompt_extra(settings, requirements) -> str`
- `tool_specs() -> list[ToolSpec]`
- `build_search(args, settings, requirements) -> SearchPlan`
- `normalize_product(raw, settings) -> Product` (preserve listing currency; no silent FX)
- `eligible_for_budget(product, settings, budget) -> bool`
- `prefilter(products, requirements) -> list[Product]`

**Extension pack:**

- `matchUrl`, `detectCurrency`, `buildSearchUrl`, `parseListing`, `parseDetail`, `nextPage`, `detectBlock`

**Load rules:**

- Backend: `get_adapter(settings.site)` on create / site change; cached for the run.
- Extension: content scripts register into a pack registry; `browser-jobs` resolves `adapters[settings.site]` only.
- Tab host ≠ session site → `site_mismatch`.

**Shared (not in packs):** WS broker, quotas, session store, `ask_user`, requirement patches, fit-judge LLM, recommendation schema, critic/planner orchestration.

### 6. Tool / agent surface

- Browse tools do not take `site` from the LLM (or host ignores it); always `settings.site`.
- Active adapter supplies site-specific tool args and builds search URLs/filters from `budget` + `settings.currency`.

### 7. Errors

| code | meaning |
|---|---|
| `site_mismatch` | tab host ≠ `settings.site` |
| `currency_mismatch` | page currency ≠ `settings.currency` |
| `adapter_missing` | unknown site id |

### 8. Migration (one-version cutover)

On read or via DB migrate:

- `sites[0]` → `settings.site` (require non-empty)
- `budget_cny` → `budget`
- drop `price_cny`
- initialize `settings.currency` / `currency_source` when missing (e.g. `CNY` / `manual` for legacy JD-only, or leave unset until first detection)

No long-lived dual field names in code or tests.

## Layout (target)

```text
apps/api/app/domains/shopping/adapters/
  __init__.py          # get_adapter(site)
  base.py              # Protocol + shared types
  jd/
  ebay/

packages/extension/adapters/
  registry.js
  jd/
  ebay/
```

Existing `content/jd.js` / `content/ebay.js` become pack implementations (or thin re-exports) behind the registry.

## Rollout order

1. Session `settings` + singular `site` + rename money fields + migration.
2. Adapter interfaces; move JD/eBay into packs with behavior-preserving wiring.
3. Currency detect / manual override + browse gate + panel UX for mismatch / clear.
4. Strip FX-from-prompt; host-owned search filters only.
5. Delete leftover multi-site and `_cny` paths.

## Acceptance

- Create session accepts `site` (singular); snapshot shows `settings.{site,currency,currency_source}`.
- No `budget_cny` / `price_cny` in schemas, store, prompts, or tests.
- LLM prompts never instruct currency conversion; search max-price matches `settings.currency` via adapter.
- Extension and API resolve site behavior only through pack registry / `get_adapter`.
- Browse jobs fail closed on `site_mismatch` / `currency_mismatch`.
- Same-currency site switch keeps products; different-currency switch requires align-or-clear.
- Golden / unit tests cover migration, budget eligibility, and both packs’ search/parse/currency detect.
