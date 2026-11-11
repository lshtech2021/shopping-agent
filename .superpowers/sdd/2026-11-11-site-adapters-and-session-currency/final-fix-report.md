# Final fix report: site adapters + session currency

## Status

All Important findings I1–I5 are resolved.

## Commit

- `499e03a fix: resolve final site adapter review findings`

## Fixes

- Site PATCH now invalidates the plan, searched-site state, and stale
  recommendations while retaining inventory. The panel refreshes the server
  snapshot and resumes only interrupted/blocked sessions; error recovery
  refreshes instead of restoring cached settings.
- eBay currency detection returns `null` when the page has no currency signal.
- Extension URL matching and detail URL construction now come from adapter
  packs and registry lookup, including the content bridge.
- The backend adapter contract now contains only
  `system_prompt_extra` and `build_search_filters`; shared constraints remain
  the sole budget eligibility authority.
- Currency mismatch UI only accepts an interrupted/blocked mismatch whose
  logged session currency still equals current settings and whose page
  currency still differs.

## Verification

- Focused API:
  `PYTHONPATH=. .venv/bin/pytest -q tests/test_session_settings.py tests/test_site_adapters.py tests/test_agent_constraints.py`
  — 35 passed.
- Focused extension:
  `node --test tests/session-view.test.js tests/currency-gate.test.js tests/adapter-registry.test.js tests/browser-jobs.test.js`
  — 31 passed.
- Full API: `PYTHONPATH=. .venv/bin/pytest -q`
  — 269 passed, 2 skipped.
- Full extension: `node --test`
  — 57 passed.
