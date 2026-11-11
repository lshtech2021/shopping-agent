# Task 7 Report: Prompt/context cleanup and leftover deletion

## Status

Complete.

## Implementation

- Removed the unused `fx_rates` configuration.
- Added a prompt boundary regression test that excludes prompt-side FX instructions and legacy price fields.
- Removed a redundant legacy-name assertion outside the session migration tests.
- Confirmed the inventory no longer contains the obsolete currency conversion helper.
- Confirmed runtime extension code has no legacy CNY or FX identifiers; API runtime matches are limited to `normalize_session_dict` migration handling.

## Verification

- `cd apps/api && PYTHONPATH=. .venv/bin/pytest -q` — 268 passed, 2 skipped.
- `cd packages/extension && node --test` — 53 passed.
- Runtime grep gate passed with only the allowed API session migration matches.
- `git diff --check` passed.
