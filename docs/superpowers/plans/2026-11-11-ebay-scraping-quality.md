# eBay Scraping Quality Improvement Plan

Status: implemented on branch `fix/ebay-scraping-quality` (2026-11-11).
Reference implementation: `ai-shopping-assistant/extensions/collector`.

## Problem

The eBay path returned fewer, less reliable observations than the reference
collector and could silently misreport prices and rankings.

Verified defects:

- `parseCurrency('¥12,800', 'USD')` returned `USD`; JPY was not in
  `fx_rates`, so budgets were compared against the wrong currency.
- `parseRating('1,234 product ratings')` returned `1`; the card parser fed
  review counts into the rating field.
- `parseSales('10 watchers')` returned `10`; watch counts were ranked as
  sales.
- Detail parsing only returned title, price, currency, and item-specific
  rows. It missed condition, seller, shipping, returns, location,
  quantity/sold, availability, variations, highlights, and images. The fit
  judge therefore returned `null` for must-haves that only appeared in those
  fields.
- eBay error pages such as "Something went wrong on our end" were classified
  as successful empty listings (`parse_error: empty_listing`), so the site was
  not surfaced as risky.
- Listing parsing used fixed 500–800 ms delays and a single parse attempt,
  which lost cards and prices while the page was still hydrating.
- Images, total result counts, variation URLs, and modern `s-card` condition
  and shipping rows were not captured.

## Borrowed from the collector

- `waitForListing` and explicit page readiness before parsing.
- `blocked` / `blockedByUrl` style classification for login and challenge
  pages.
- `matchesUrl` listing-page guard.
- `upgradeImage` (`s-l500` → `s-l1600`).
- Currency handling where a bare `¥` means JPY.
- Next-page URL fallback when the anchor is missing.
- Parse retry with backoff.
- Fixture-driven parser tests for legacy and modern layouts.

Not borrowed: batch upload, the admin import API, the side-panel state
machine, and 20-page bulk collection. The agent loop and per-turn quotas remain
the owner of paging.

## Implemented changes

### Phase 1 — Readiness and block safety

- Added `matchesUrl`, `waitForListing`, and `waitForDetail` to the eBay
  adapter.
- Content bridge waits for hydration and returns `listing_not_ready` /
  `detail_not_found` instead of parsing an incomplete page.
- `browser-jobs.js` retries transient listing and detail parse results with
  500 ms / 1 s backoff and no longer relies on fixed delays.
- Block detection now covers eBay error pages, access-denied pages, browser
  challenge pages, and sign-in pages while preserving valid-item precedence.
- Added error/challenge/login fixtures and a `matchesUrl` guard in the bridge.

### Phase 2 — Listing quality

- Modern `s-card` and legacy `s-item` extraction.
- Title cleanup for `New Listing`, `Sponsored`, and "Opens in a new window".
- `image_url` with size upgrade.
- Condition and shipping extraction from subtitles and attribute rows.
- Separate `sales` (`N sold`) and `watchers` (`N watchers`).
- Star-only ratings bounded to 0–5.
- Explicit `Sponsored` detection.
- Supported currencies: CNY, USD, CAD, AUD, HKD, SGD, NZD, GBP, EUR, JPY.
- Total `result_count`, `page_item_count`, `has_next`, `next_url`, and
  result-count-based pagination fallback.
- Variation parameters (`var`, `epid`) preserved in item URLs.

### Phase 3 — Detail quality

- Item-specific rows support both legacy and `-content` DOM variants (up to
  40 attributes).
- Added condition, seller/feedback, shipping/cost, returns, location,
  quantity available, sold, watchers, availability, listing type, best offer,
  bids, variations, highlights, gallery images, and item number.
- Detail price ranges are preserved when merged into the product.
- Fit-judge payload now includes `detail_condition` and up to ten
  `detail_highlights` entries.

### Phase 4 — Inventory and API integration

- `upsert_listing` preserves listing observations across rescrapes and does
  not erase detail-owned fields.
- `query_inventory` and `_public_product` expose image, availability, listing
  type, seller, watchers, sold, location, and shipping cost.
- Ended or unavailable products are ineligible; the critic has a matching
  hint and hard-constraint entry.
- Added `JPY: 0.048` to `fx_rates`; unsupported currencies stay unconverted
  (`price_cny=None`).

## Tests

- `packages/extension/tests/ebay-parser.test.js`: legacy/modern cards, images,
  currency, sales/watchers, ratings, result counts, pagination fallback,
  readiness, block pages, rich detail, ended detail, `matchesUrl`.
- `packages/extension/tests/browser-jobs.test.js`: transient
  `listing_not_ready` retries and existing tab/stale-document guards.
- `apps/api/tests/test_ebay_detail_quality.py`: detail merge, price range,
  fit-judge payload.
- `apps/api/tests/test_ebay_inventory.py`: inventory preservation, JPY
  conversion, unavailable eligibility, query observations.

Commands run:

```powershell
cd packages/extension && npm test
cd apps/api && $env:PYTHONPATH = (Get-Location).Path; python -m pytest -q
```

Results: extension suite green (37 tests); API suite green (241 passed,
2 skipped).

## Acceptance criteria

- A normal eBay search returns title, price, currency, and image for at least
  90% of visible cards.
- `result_count` is the total shown by eBay; `page_item_count` is the page
  size.
- `US $`, `$`, `RMB/CNY`, and `¥` map to the correct currency; ranges survive
  listing and detail parsing; supported currencies convert to CNY.
- Detail results include condition, seller, shipping, returns, location,
  quantity/sold, and highlights when visible.
- Error, challenge, and login pages never produce `status: ok` with zero
  items.
- A deliberately slow page still parses after hydration.
- Both test suites pass.

Live verification remains a manual step; it requires a logged-in Chrome
session and the extension loaded from `packages/extension`.

## Remaining limitations

- eBay DOM changes require refreshing fixtures and selectors.
- The item description iframe is cross-origin and is not scraped.
- Only `ebay.com` is supported.
- Bulk multi-page collection remains out of scope; per-site pacing and quotas
  are unchanged.
- Per-visit waits are capped at 8 s (listing) and 6 s (detail) to stay well
  inside the 50 s job deadline.

## Phase commits

1. `fix(extension): wait for eBay hydration and classify blocked pages`
2. `feat(extension): enrich eBay listing fields and pagination`
3. `feat: extract richer eBay detail data for fit judgments`
4. `feat(api): persist eBay observations and convert JPY prices`
5. `fix(extension): reject non-listing eBay pages before parsing`
6. `fix(extension): broaden eBay detail readiness and sponsored detection`
7. `docs: document the eBay adapter contract and quality checks`
