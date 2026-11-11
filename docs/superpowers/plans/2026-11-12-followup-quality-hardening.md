# Post-Validation Quality Hardening Plan

Status: implemented in four phases on `fix/ebay-scraping-quality`
(2026-11-12). Verification: extension 75 tests passed; API 305 passed,
2 skipped.

Implementation commits:

- `1824267` enforce requirement clarification and refresh plan queries
- `5465cd5` enforce configurable shipping caps
- `82a9a1b` link recommendations to the original listing
- `6c523b7` complete eBay listing and landed-cost evidence

Scope: remaining issues from the real eBay session log, plus shipping caps and
clickable recommendation links.

## Already fixed from the same log

These were found while analysing session
`8b963018b5e5442cae688ad4f638b103` and are already committed:

- `1037f96` persist session settings on `runs`; add migration
  `0003_runs_settings.py` and backfill site/currency. This was the root cause
  of the currency question in turn 1 and `settings.currency = None` in turn 3.
- `3fd7816` split primary and approximate shipping amounts inside one
  `ux-textspans` node. This fixes `US $399.73 (approx. RMB 2,669.20)` being
  stored as 399.73 CNY.
- `0c978b7` stop sending `preferences` to the requirement reviewer, which
  duplicated a clarification the user had already turned into a must-have;
  reuse the review/judge model so unsupported `response_format` is not
  retried on every call.

Deployment note: run `alembic upgrade head` before restarting the API.

## Findings to address

1. `update_requirements` can return `needs_clarify` with a question, but the
   agent loop does not enforce it. The model can ignore the question and
   continue searching.
2. Plan metadata keeps stale `search_queries` after hard requirements change.
3. Some eBay listing fields remain incomplete: `listing_type` is null on many
   cards, `sponsored=true` is suspiciously broad, and `location` is often
   missing.
4. Landed cost includes item price and shipping only; explicit import charges
   are not parsed.
5. Shipping can dominate the item price. Add configurable percentage and
   absolute caps.
6. Recommendation results already carry `url`, but the side panel and web
   console render it as plain text instead of a clickable link.

## Phase 1: Enforce clarification and refresh plan queries (P0/P1)

### 1.1 Enforce `needs_clarify`

File: `apps/api/app/domains/shopping/tools.py`

When `update_requirements` returns both `needs_clarify=true` and a
`clarify_question`, return a tool result with `control="wait_user"`:

```python
if updated.get("needs_clarify") and updated.get("clarify_question"):
    question = updated["clarify_question"]
    pending = {
        "question": question["question"],
        "reason": "clarify",
        "options": question.get("options", []),
        "metadata": {
            "field": question.get("field"),
            "source": "update_requirements",
        },
    }
    return ToolResult(
        call_id="",
        tool="update_requirements",
        ok=True,
        data=updated,
        meta=_plan_meta(_context, "requirements_updated"),
        control="wait_user",
        output={
            "question": pending["question"],
            "reason": "clarify",
            "pending_user": pending,
        },
    )
```

`AgentRuntime` already handles `wait_user` and returns `pending_user` to the
panel. Requirements are written before waiting, so the answer is not lost.

If `needs_clarify=true` but there is no question (for example
`ambiguous_fields`), keep the current behaviour and let the model call
`ask_user`.

Tests:

- `test_update_requirements_waits_when_review_returns_question`
- `test_update_requirements_continues_without_question`

### 1.2 Refresh plan requirements and clear stale queries

File: `apps/api/app/domains/shopping/tools.py`

When hard requirements change (`budget`, `category`, `use_case`,
`must_have`):

```python
metadata["requirements"] = updated["requirements"]
metadata["search_queries"] = {}
metadata["queries_stale_from_requirements"] = True
```

The prompt should say that empty `search_queries` means derive queries from
the current requirements. Do not leave old queries in place, because the
model is told to follow them.

Optional: if the search step already completed, mark it `pending` again only
when the category or must-have set changed. Clearing the queries is the
minimum fix for plan/behaviour consistency.

Tests:

- `test_update_requirements_clears_stale_plan_queries`

## Phase 2: Shipping caps (P1)

### 2.1 Rule

Support two optional caps:

- `max_shipping_ratio`: shipping / item price, stored as a 0..1 ratio.
  UI displays a percentage. Default `0.10`.
- `max_shipping_cost`: absolute shipping cap in the session currency.
  Default `null` (disabled).

When both are set, **both apply; exceeding either cap excludes the item**
(OR semantics). The absolute cap is a hard cap: exceeding it excludes the
item even when the percentage passes.

Allowed shipping is effectively:

```text
min(item_price * max_shipping_ratio, max_shipping_cost)
```

Examples with price 1000 CNY, ratio 10%, absolute 50 CNY:

| Shipping | Ratio | Absolute | Result |
|----------|-------|----------|--------|
| 40 | 4% | 40 <= 50 | keep |
| 80 | 8% | 80 > 50 | exclude (absolute) |
| 120 | 12% | 120 > 50 | exclude (both) |

Examples with price 1000, ratio 5%, absolute 100:

| Shipping | Ratio | Absolute | Result |
|----------|-------|----------|--------|
| 90 | 9% | 90 <= 100 | exclude (ratio) |

### 2.2 Settings

Files:

- `apps/api/app/config.py`
- `apps/api/app/store.py`
- `apps/api/app/schemas.py`
- `apps/api/app/main.py`

Add:

```python
max_shipping_ratio: float = 0.10   # 0..1
max_shipping_cost: float | None = None
```

Session defaults include both fields. When loading an older session that
lacks them, fall back to the configured defaults.

`CreateSessionRequest` and `PatchSessionSettingsRequest` accept both fields.
Use `model_fields_set` so an explicit `null` clears a cap while an omitted
field leaves it unchanged.

Validation:

- `max_shipping_ratio` in `0..1`
- `max_shipping_cost >= 0`
- Changing either cap clears `recommendations`, but keeps cached products

### 2.3 Eligibility

File: `apps/api/app/constraints.py`

```python
shipping = product.get("shipping_cost")
shipping_currency = normalize_currency(product.get("shipping_currency"))
product_currency = normalize_currency(product.get("currency"))
price = (
    product.get("price_max")
    if product.get("price_is_range")
    else product.get("price")
)

absolute = settings.get("max_shipping_cost")
ratio = settings.get("max_shipping_ratio")

if (
    shipping is not None
    and price is not None
    and float(price) > 0
    and shipping_currency in (None, product_currency)
):
    absolute_exceeded = absolute is not None and float(shipping) > float(absolute)
    ratio_exceeded = (
        ratio is not None
        and float(shipping) > float(price) * float(ratio)
    )
    if absolute_exceeded or ratio_exceeded:
        violations.append("shipping")
```

Rules:

- Price ranges use `price_max` so the ratio is not inflated by the minimum.
- Unknown shipping does not trigger `shipping`; the existing
  `cost_incomplete` rule still applies when a budget or shipping cap is
  configured and detail is required.
- A shipping currency that differs from the product currency skips the cap
  and keeps `cost_incomplete`.
- Add a diagnostic `limit_type` (`ratio` or `absolute`) in exclusion samples.
- Add `shipping` to `query_inventory`, recommendation guidance, and the
  prompt's exclusion-reason list.

### 2.4 UI

Files:

- `packages/extension/panel/sidepanel.html`
- `packages/extension/panel/sidepanel.js`
- `packages/extension/panel/sidepanel.css`

Add to both the new-session form and the chat settings area:

```text
Shipping cap:
  Max shipping ratio [10] %
  Max shipping amount [   ] <session currency>
```

- Percent input is 0..100 and is converted to 0..1 in the request.
- Blank amount disables the absolute cap.
- Copy: "When both are filled the stricter cap applies; exceeding either one
  excludes the item."
- On change, PATCH settings and clear old recommendations.

### 2.5 Tests

- Both caps set, absolute exceeded, ratio passed -> excluded
- Both caps set, ratio exceeded, absolute passed -> excluded
- Both caps set, both passed -> kept
- Ratio only -> ratio applies
- Absolute only -> absolute applies
- Shipping unknown -> not a `shipping` violation
- Shipping currency differs -> not a ratio comparison, remains incomplete
- Free shipping always passes
- PATCH/create validation, explicit null clears a cap
- Changing caps clears recommendations

## Phase 3: Clickable recommendation links (P1)

The backend already returns `url` from `ToolBox.recommend()` through
`_safe_product_url`, restricted to eBay/JD hosts.

### 3.1 Side panel

Files:

- `packages/extension/panel/session-view.js`
- `packages/extension/panel/sidepanel.js`
- `packages/extension/panel/sidepanel.css`

Add a testable helper:

```js
export function safeProductUrl(url) {
  // http/https only; ebay.com or jd.com (including subdomains)
}
```

In `appendBubble`, render recommendation items with DOM nodes, not
`innerHTML`:

```text
<li>
  <a href="..." target="_blank" rel="noopener noreferrer">Title</a>
  <span>CNY 1535.83</span>
  <div class="rec-reason">Reason</div>
</li>
```

If the URL is invalid or missing, render the title as text.

### 3.2 Web console

File: `apps/web/app.js`

Replace the plain URL text in recommendation cards with an anchor using the
same URL validation and `target="_blank" rel="noopener noreferrer"`.

### 3.3 Tests

- `safeProductUrl` accepts eBay/JD product URLs
- `javascript:` and unrelated hosts return null
- Missing URL degrades to text
- Manual check: clicking a recommendation title opens the original item page
  in a new tab

## Phase 4: eBay listing fields and import charges (P2)

### 4.1 `listing_type`

File: `packages/extension/adapters/ebay/index.js`

Fallbacks:

1. Existing attribute-row and purchase-option detection.
2. When the search URL has `LH_BIN=1` and no bid/auction signal is present,
   default to `buy_it_now`.
3. Explicit `Place bid` / `N bids` wins over `Buy It Now`.

Add a fixture for a card with no visible purchase-option text and a
`LH_BIN=1` search URL.

### 4.2 `sponsored` precision

File: `packages/extension/adapters/ebay/index.js`

Require both:

- `.s-card__footer .s-card__sep b` exists, and
- reversed `derosnopS` text or a data-URI background image is present

If real organic cards also carry this footer, remove the heuristic and keep
only explicit `Sponsored` badges. Add one sponsored fixture and one organic
fixture.

### 4.3 `location`

File: `packages/extension/adapters/ebay/index.js`

Add selectors:

```js
.s-item__location
.s-item__itemLocation
[data-testid="item-location"]
.s-card__location
```

Keep the existing `from ...` attribute-row parsing. Detail-page location
continues to overwrite the listing value after `fetch_detail`.

### 4.4 Import charges

Files:

- `packages/extension/adapters/ebay/index.js`
- `apps/api/app/tools.py`

Parse:

```js
import_charges
import_charges_currency
customs_may_apply
```

Rules:

- If an explicit import charge is shown, include it in `landed_cost` when
  price, shipping, and import charges share a currency.
- If the amount is in another currency without an approximate amount,
  `cost_complete=false`.
- If the page only warns that customs/duties may apply, do not mark the cost
  incomplete; set `customs_may_apply=true` and require the recommendation
  text to mention possible duties.

Tests:

- Import charges present and same currency -> included
- Import charges in a foreign currency -> incomplete
- Only a customs warning -> complete with `customs_may_apply=true`

## Implementation order

| Phase | Scope | Priority |
|-------|-------|----------|
| 1 | Enforce `needs_clarify`; clear stale plan queries | P0/P1 |
| 2 | Shipping ratio/absolute caps and settings UI | P1 |
| 3 | Clickable recommendation links | P1 |
| 4 | eBay listing fields and import charges | P2 |

## Acceptance

- A tool result with `needs_clarify=true` and a question stops the turn and
  returns a pending user question; the model cannot continue searching.
- After hard requirements change, plan metadata no longer contains stale
  `search_queries`.
- Shipping above either configured cap is excluded with reason `shipping`;
  when both are set, exceeding the absolute cap excludes even if the ratio
  passes.
- Recommendation titles are links that open the original eBay/JD item in a
  new tab.
- Explicit import charges are included in landed cost; unknown duties only
  produce a warning.
- eBay listing `listing_type`, `location`, and `sponsored` fixtures cover the
  current card layouts.
- API and extension test suites pass.
