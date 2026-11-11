# eBay Search Pipeline Hardening

Status: implemented on branch `fix/ebay-scraping-quality` (2026-11-11).

Source incident: a session asked for a software-development laptop under
5,000 CNY on eBay. The agent reported no matching products. The session log
showed:

```text
scrape_listing -> result_count=0, accepted=1
search URL: ..._nkw=used laptop for software development book accessory...
```

## Root causes

1. **Natural-language eBay query.** eBay ANDs every `_nkw` token. The first
   query, `laptop for software development`, required `software` and
   `development` to appear in the listing, so the page reported zero exact
   matches and showed fallback cards.
2. **Positive exclude terms.** `book` and `accessory` were appended without a
   leading `-`, turning exclusions into required keywords. The second query
   could not return useful results.
3. **Action-id collision.** `scrape_listing` used the deterministic args
   `{site, page}`. Two searches in one turn therefore produced the same
   action id, and the second scrape received the first search's cached
   observation instead of parsing the new page.
4. **Fallback cards ingested.** `parseListing` only treated a page as empty
   when it had zero cards. A page reporting `0 results` with one "results
   matching fewer words" card was accepted as a successful one-item page.
5. **Unverifiable must-have.** `性能足够高` ("high enough performance") was
   stored as a hard `must_have`. The fit judge cannot prove it from a
   listing, so it returns `null`; `check_product` treats `null` as a
   violation, making every candidate ineligible.
6. **Replanner invented filters.** The second replan appended `-used` even
   though the user allowed used items, and it read a stale requirement
   snapshot from plan metadata.
7. **No exclusion diagnostics.** `query_inventory` returned only
   `count=0`, so the agent could not tell whether price, category, fit, or
   availability removed the cached product.

## Implemented fixes

### Search construction

- Normalize exclusion terms in `buildSearchUrl`: accept `book` or `-book`
  and emit `-book`; de-duplicate terms.
- Add site-specific planner/search instructions: eBay uses 1–3 short
  keywords, not use-case sentences; constraints go through category and
  price filters; negative terms use `-`.
- Make the template planner site-aware: `笔记本电脑` maps to `laptop` for
  eBay, and a Chinese use-case phrase is not appended to an eBay query.
- Tell the agent to convert CNY budgets conservatively (round down, leave a
  shipping/tax buffer).

### Navigation idempotency

- `ToolBox` now tracks the active search URL and a search sequence.
- `scrape_listing` and `next_page` include `query`, `search_url`, and
  `search_seq` in their action args. A new search produces a new
  deterministic action id, so the second scrape really dispatches and parses
  the new page.

### Zero-result and hydration safety

- `parseListing` discards fallback cards when the results heading reports
  zero or the page has a no-results marker. It returns
  `no_results=true`, `fallback_item_count`, and no items.
- `waitForListing` waits until the usable-card count is stable for several
  polls, so a single early placeholder cannot be mistaken for the full grid.
- Extension logs now include `resultCount`, `pageItemCount`,
  `fallbackItemCount`, and the first item title.

### Requirement quality

- Send existing `must_have` items to an LLM requirement review that
  classifies each as hard, soft, or dropped. Move soft goals such as
  `性能足够高` to `preferences`, drop unrestricted entries such as
  `不限品牌`, and return the reviewer's measurable `clarify_question`.
  No regex is used for this semantic classification.
- Update the system prompt and tool schema: `must_have` is only for
  objective, listing-verifiable attributes; vague quality goals need a
  measurable threshold or must remain preferences.

### Inventory and replanning

- `query_inventory` returns aggregated `excluded` violation counts and up to
  three `excluded_samples`, so the agent can choose between detail
  verification, a broader search, or asking the user.
- The replanner no longer adds `-used`; it keeps only the accessory
  suppression suffix, and repeated eBay replans stop instead of inventing
  new exclusions.
- `update_requirements` refreshes `plan.metadata["requirements"]` so the
  replanner sees current category/budget values.

## Tests

- Extension: exclusion normalization, zero-result fallback rejection,
  card-grid stabilization, existing parser/readiness suites.
- API: action identity after a new search, LLM requirement review,
  inventory exclusion diagnostics, replanner used-item regression, plan
  requirement synchronization, planner keyword generation.

Results at implementation time:

```text
packages/extension: 40 tests passed
apps/api:           250 passed, 2 skipped
```

## Remaining checks

- Run the same shopping prompt with a real eBay session and confirm:
  - the first search uses a short keyword query;
  - `result_count` and `page_item_count` agree;
  - a second search produces a different scrape `action_id`;
  - `query_inventory` returns candidates or actionable `excluded` reasons.
- eBay DOM changes may still require fixture refreshes.
- Budget conversion remains model-guided; shipping/tax are not available in
  the eBay search filter and are only reflected in the prompt guidance.
