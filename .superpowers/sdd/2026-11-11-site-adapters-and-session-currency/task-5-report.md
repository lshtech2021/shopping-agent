# Task 5 Report: Currency detect, browse gates, settings sync

## Status

Complete.

## Implementation

- Added fail-closed browse guards for `site_mismatch`, `currency_mismatch`, and `adapter_missing`.
- Applied the guard before search, listing scrape, next-page, and detail parsing, with a second check after next-page navigation.
- Passed session settings with browse jobs and cached them per session in the extension.
- Added page currency detection through the content bridge; JD reports CNY and eBay checks geo/currency controls and visible prices.
- When session currency is unset, the extension PATCHes the detected currency with `currency_source: detected` before continuing.
- Added the extension `patchSessionSettings` API.
- Extended session settings PATCH with `currency_source` and `clear_products`; clearing removes both products and recommendations.
- Added unit and integration coverage for mismatch errors, detected-currency adoption, and clearing inventory.

## Verification

- `cd packages/extension && npm test` — 50 passed.
- `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q` — 266 passed, 2 skipped.
- `git diff --check` passed.
