# Session Invariants Hardening Design

**Date:** 2026-11-13  
**Status:** Approved  
**Supersedes (in conflict):** parts of `2026-11-11-site-adapters-and-session-currency-design.md` §4 (keep products on site switch), dual `sites[]` mirror, and any fail-open browse-currency escapes.  
**Context:** Review of the site-adapter + session-currency implementation found the thesis sound but the session invariant soft. This design tightens that invariant; it does not reopen multi-site shopping or prompt-side FX.

## Problem

1. Mid-session site switch “keeps products” while eligibility requires `product.source == settings.site`, so kept items are storage ghosts.
2. PATCH site/currency clears `session.plan` but not the agent checkpoint; resume can reload the old plan.
3. `settings.currency` and `requirements.budget_currency` are two money authorities.
4. `settings.site` and `session.sites[]` are two site authorities; planner/quotas still speak multi-site.
5. Browse gate fail-opens when page currency is unknown or settings are missing on args.
6. Site-specific rules (eBay shipping/auction) still live in shared `constraints` while adapters stay shallow.
7. Design doc drifted from shipping settings, detail `skipCurrency`, and search buffer behavior.

## Goals

- One site identity, one currency identity, one budget unit.
- Site change = hard shopping-context reset (products + plan + searched + recommendations + checkpoint).
- Browse fails closed on site/currency/unknown-currency.
- Site packs own site-specific eligibility extras; shared constraints stay site-agnostic + pack hook.
- Written design matches runtime.

## Non-goals

- Parallel JD + eBay shopping.
- Silent FX / restoring `*_cny` / `fx_rates`.
- Capability-flag plugin frameworks.
- Reworking fit-judge / critic algorithms.

## Decisions

### 1. Single site identity

- Public and runtime source of truth: `settings.site` only.
- Remove `sites[]` from create/detail/summary happy path (migrate: `sites[0]` → `settings.site` on read, then stop writing `sites`).
- Planner / tools / eligibility take `site: str`, not `sites: list[str]`.
- Historical `product.source` may still be `"jd"` | `"ebay"`; maps keyed by source (quotas, blocked) are fine if they mean “per source ever touched,” not “active site list.”

### 2. Single currency + budget unit

- `settings.currency` is the only session money unit.
- Delete `requirements.budget_currency` (and any dual-unit checks).
- `requirements.budget` / `budget_min` are always in `settings.currency`.
- Changing `settings.currency` clears budget fields (already done) and clears recommendations; clear products if any stored `product.currency` differs from the new currency.

### 3. Site switch = hard reset

On `PATCH settings.site` when the site actually changes:

1. Clear `products`, `recommendations`, `plan`, `searched_sites`.
2. Invalidate agent checkpoint for the session (delete saved turn state).
3. Keep non-money requirements (category, must_have, preferences, use_case, raw).
4. Bind/open the new site tab; detect currency; apply currency gate (set if unset, else mismatch flow).
5. Do **not** call `resume` unless status is already `interrupted` / `blocked` **and** checkpoint was cleared or rewritten to match cleared plan.

**Forbidden:** “keep products across sites.”

### 4. Browse gate fail-closed

Before search / scrape / next_page (and any listing navigation):

| Condition | Result |
|---|---|
| Tab host ≠ `settings.site` | `site_mismatch` |
| `settings.currency` set and `page_currency` null / unknown | `currency_unknown` (block; retry/hydration allowed, no silent continue) |
| `page_currency` ≠ `settings.currency` | `currency_mismatch` |
| Missing session settings on a production job | treat as error / inject from gateway — do not skip guard |

Detail fetch may record a different display currency as metadata, but must not silently treat session currency as matched; prefer observing `detail_currency` without bypassing the session invariant for listing ops.

Remove `SITE_CHECK` sentinel and “allow when page currency null” escape for listing tools.

### 5. Adapter depth (pack hook)

Backend `SiteAdapter` keeps:

- `id`
- `system_prompt_extra`
- `build_search_filters` (host-owned; search buffer is explicit session/policy, see §6)

Adds:

- `eligibility_extra(product, settings, requirements) -> list[str]`  
  Site-only violations (e.g. eBay auction-when-budgeted, shipping-cap failures).

Shared `constraints.check_eligibility`:

- Site match vs `settings.site`
- Currency match vs `settings.currency`
- Budget numeric compare
- Must-have / fit / availability
- Then append `get_adapter(settings.site).eligibility_extra(...)`

No second budget implementation inside packs.

### 6. Search price buffer as explicit policy

- Keep host buffer for eBay item-price search (default `0.85`) as `settings.search_price_buffer` (or reuse a named constant exposed in context).
- Context block must show effective search cap when budget is set.
- Not silent FX; still in session currency.

### 7. Design / docs truth

- This document is authoritative for the invariants above.
- Update `2026-11-11-site-adapters-and-session-currency-design.md` with a short “Superseded” pointer for §4 keep-products and dual `sites[]`.

## Acceptance

- No public `sites[]` on create; detail/summary expose `settings.site` (migration-only read of legacy `sites`).
- No `budget_currency` in requirements or eligibility.
- Site PATCH clears products + plan + searched + recommendations + checkpoint; tests cover interrupt→switch→resume does not restore old plan.
- Listing browse blocks on `currency_unknown` / `currency_mismatch` / `site_mismatch`.
- eBay shipping/auction rules invoked only via adapter `eligibility_extra`.
- Context shows session currency and effective search max price.
- Eval/smoke create payloads use `site`, not `sites`.
