# Shopping Agent Extension

Manifest V3 side panel that connects to the Shopping Agent API over WebSocket
and executes browser jobs in the user's own browser session. The service worker
owns tab/job orchestration; per-site content adapters parse the page DOM.

## Layout

| Path | Responsibility |
|------|----------------|
| `background/service-worker.js` | WebSocket connection and job dispatch |
| `background/browser-jobs.js` | Tab lifecycle, navigation, parse retries |
| `content/bridge.js` | Content-script message handler |
| `content/ebay.js`, `content/jd.js` | Site adapters |
| `content/parse-utils.js` | Price, rating, sales, currency helpers |
| `panel/`, `options/` | Side panel and options UI |
| `shared/` | Logger, API client, action cache, WS URL helpers |

## Adapter contract

Each adapter is a UMD module that exports:

```js
{
  site, detectBlock, parseListing, parseDetail,
  hasNextPage, nextPageUrl, clickNextPage,
  currentPageNumber, buildSearchUrl,
  // eBay additions
  matchesUrl, waitForListing, waitForDetail
}
```

`bridge.js` handles these messages:

| Message | Behavior |
|---------|----------|
| `detect_block` | Returns `login_required`, `captcha`, `rate_limited`, or `null` |
| `parse_listing` | Waits for usable cards (eBay, 8 s max), then parses |
| `parse_detail` | Waits for detail hydration (eBay, 6 s max), then parses |
| `has_next_page` | Returns `hasNext` and the next URL when known |
| `click_next_page` | Only used by SPA adapters; eBay pagination is URL-based |

`parseListing` returns a page object:

```js
{
  status, items,
  page, result_count,        // result_count is eBay's total, not page size
  page_item_count,           // cards parsed on this page
  has_next, next_url, no_results
}
```

`parseDetail` returns a flat evidence object (`title`, `price`, `price_max`,
`price_is_range`, `currency`, `attributes`, `condition`, `seller`, `shipping`,
`shipping_cost`, `returns`, `location`, `quantity_available`, `sold`,
`watchers`, `availability`, `listing_type`, `best_offer`, `bids`,
`has_variations`, `variations`, `highlights`, `images`, `item_number`).

## eBay listing fields

`parseListing` extracts one object per `li.s-item` / `li.s-card`:

`source_product_id`, `url` (variation parameters kept, tracking stripped),
`title`, `price`, `price_max`, `price_is_range`, `currency`, `image_url`,
`shop_name`, `sales`, `watchers`, `rating`, `sponsored`, `shipping`,
`condition`, `badges`, `category`, `listing_type`.

Quality rules:

- A card is only usable when it has an item id, a title, and a price. This is
  also the readiness condition used before parsing.
- `sales` requires an explicit `N sold` token. Watchers are reported
  separately and are never used as sales.
- `rating` requires a star-rating element or an `X out of 5` label. Review
  counts such as `1,234 product ratings` return `null`.
- `sponsored` requires an explicit `Sponsored` badge/label. Top-rated-seller
  badges are not treated as sponsored.
- `¥` maps to JPY; `RMB`/`CNY` map to CNY. Unsupported currency symbols keep
  the raw price but do not silently become USD.
- Currency detection samples up to five price nodes, treats a bare `$` on
  ebay.com as USD, and uses the geo control only as a last resort.
- A detail page may display a different currency from the listing; the
  backend keeps the listing price/currency and flags the detail mismatch.
  When eBay also shows an approximate price in the session currency, the
  backend uses that approximate price instead of reporting a mismatch.
- `buyItNowOnly` maps to eBay `LH_BIN=1`; hard-budget searches use it to
  avoid auctions whose final price is unknown.
- Condition values are limited to known eBay labels; a brand subtitle such
  as `Dell` is not reported as condition.
- `result_count` comes from the results heading when available. If the next
  anchor is missing, pagination falls back to `_pgn + 1` only when the total
  proves another page exists.
- A zero result count or a "no exact matches" marker discards eBay's
  "results matching fewer words" fallback cards instead of ingesting them.
- Parsing waits until the card count is stable for a few polls, not just
  until the first card appears.
- Exclusion terms are normalized to `-term`, because eBay treats every
  `_nkw` token as required.
- `matchesUrl` rejects detail/account pages before a listing parse.

## Block and parse signals

| Condition | Status / error |
|-----------|----------------|
| `signin.ebay.com`, `/signin`, sign-in form | `login_required` |
| Challenge/CAPTCHA markers, "Pardon our interruption" | `captcha` |
| Access denied / too many requests | `rate_limited` (`access_denied`) |
| eBay error page ("Something went wrong on our end") | `rate_limited` (`ebay_error_page`) |
| Empty body | `rate_limited` (`empty_body`) |
| Cards not hydrated before the wait deadline | `parse_error: listing_not_ready` |
| Detail not hydrated before the wait deadline | `parse_error: detail_not_found` |
| Non-listing eBay URL | `parse_error: not_a_listing_page` |

`browser-jobs.js` retries `listing_not_ready`, `empty_listing`, and
`detail_not_found` up to three times with 500 ms / 1 s backoff.

## Test fixtures

Fixture HTML lives in `tests/fixtures/`. Keep fixtures anonymized: no
credentials, order ids, addresses, or tracking parameters. Prefer small,
synthetic DOM snippets that cover a specific selector contract.

Covered fixtures include legacy `s-item` cards, modern `s-card` cards, price
ranges, CNY/JPY prices, error/challenge/login pages, and rich/ended detail
pages.

## Tests

```powershell
cd packages/extension
npm install
npm test
```

## Manual live check

1. Load `packages/extension` in Chrome and confirm `WS: connected`.
2. Run a shopping turn with `SA_MOCK_BROWSER=false`.
3. Check the Side Panel/log timeline for `browse:scrape_listing`:
   `item_count`, `result_count`, `page_item_count`, and `has_next` should
   match the page; `fallback_item_count` must not be ingested.
4. Open one candidate and check `browse:fetch_detail`: condition, attributes,
   seller, shipping, and availability should be present when visible.
5. Trigger a challenge or sign-in page only by normal browsing; the session
   should interrupt with a clear reason, not report an empty listing.

Known limitations: eBay DOM changes require fixture updates; the description
iframe is cross-origin and is not scraped; eBay marketplaces other than
`ebay.com` are not supported.
