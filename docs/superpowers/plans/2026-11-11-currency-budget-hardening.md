# Currency and Budget Hardening Plan

Status: implemented in four phases on `fix/ebay-scraping-quality`
(2026-11-11). Verification: extension 63 tests passed; API 282 passed,
2 skipped.

## Findings

1. A budget can be written before the session currency is known. The value is
   then silently reinterpreted when the first page detects a currency.
2. Changing or adopting a currency leaves the old budget amount and plan
   state in place.
3. eBay can expose different currencies on search and detail pages. The
   fail-closed browse gate can then block detail verification, and a bare `$`
   price can make currency detection return `null` with no recovery path.
4. Budget eligibility still uses the item price only. Shipping, landed cost,
   and auction final-price uncertainty are not part of the check.
5. Currency mismatch is reported as `budget`, which sends the agent down the
   wrong diagnostic path.
6. The fit judge still re-tries unsupported structured output for every
   batch and judges the entire inventory.
7. The read-only web console still renders the removed `price_cny` field.

## Phase 1: Currency is required before a budget is stored (implemented)

- Add `budget_currency` to requirement patches and `update_requirements`.
- JD defaults to CNY; eBay budget writes require an explicit currency.
- If session currency is unset, a stated budget currency sets session
  currency; if it conflicts with the session currency, do not write the
  budget and return a clarify question.
- Changing session currency clears budget/budget_min, recommendations,
  plan, and searched-site state; the agent must ask for the budget again in
  the new currency.
- Reject empty or unsupported currency codes.

## Phase 2: Reliable eBay detection and non-blocking detail evidence (implemented)

- Detect currency from explicit selectors and several price samples, with a
  USD fallback for bare `$` on ebay.com.
- Return a recoverable `currency_unknown` signal instead of a permanent
  mismatch when no currency can be inferred.
- Do not let a detail-page currency mismatch block specification extraction;
  keep the listing price/currency authoritative and flag the detail currency.
- When eBay shows a primary price plus an approximate price in the session
  currency, use the approximate price without reporting a mismatch.
- Report currency mismatches as `currency`, not `budget`.
- Fix the panel mismatch scan so it does not stop at the first unrelated log.
- Add tests for CNY listing + USD detail, bare dollar, and multiple logs.

## Phase 3: Landed cost and purchase realism (implemented)

- Add `landed_cost` and `cost_complete`; budget checks use the landed cost.
- Add a search cap buffer for shipping/tax instead of using the full budget.
- Exclude or flag auctions whose final price is unknown; prefer Buy It Now
  for hard budgets.
- Fix condition extraction so brand names are not treated as condition;
  exclude parts/repair listings from normal recommendations.
- Remove the stale `price_cny` render from `apps/web`.

## Phase 4: Performance and migration cleanup (implemented)

- Remember when a provider does not support structured output and skip the
  failing attempt for subsequent fit-judge batches.
- Judge a bounded top candidate set instead of the whole inventory.
- Normalize legacy plan requirements and recommendation price fields.
- Add migration/regression tests for currency transitions.

## Acceptance

- A 5,000 CNY budget is never silently treated as 5,000 USD.
- Currency changes clear or explicitly re-request the budget.
- eBay search and detail currency differences do not deadlock browsing.
- Bare `$` pages detect USD or offer a manual recovery path.
- Budget checks include shipping/landed cost and exclude uncertain auction
  final prices.
- Currency failures appear as `currency`, not `budget`.
- API and extension suites pass with new transition tests.
