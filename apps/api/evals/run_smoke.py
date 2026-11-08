"""Run mock-browser smoke evals against the in-process API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.store import store  # noqa: E402


def main() -> int:
    cases = json.loads((Path(__file__).parent / "smoke_cases.json").read_text(encoding="utf-8"))
    settings.mock_browser = True
    store.reset("memory")
    client = TestClient(app)

    passed = 0
    failed = 0
    for case in cases:
        store.reset("memory")
        created = client.post(
            "/api/v1/sessions",
            json={"requirement": case["requirement"], "sites": case["sites"]},
        )
        if created.status_code != 200:
            print(f"[FAIL] {case['id']}: create {created.status_code}")
            failed += 1
            continue
        session_id = created.json()["session_id"]
        result = client.post(
            f"/api/v1/sessions/{session_id}/messages",
            json={"content": case["message"]},
        )
        if result.status_code != 200:
            print(f"[FAIL] {case['id']}: message {result.status_code}")
            failed += 1
            continue
        body = result.json()
        expect = case["expect"]
        errors = []
        if body.get("interrupted") is not expect.get("interrupted"):
            errors.append(
                f"interrupted={body.get('interrupted')} expected {expect.get('interrupted')}"
            )
        if "interrupt_reason" in expect and body.get("interrupt_reason") != expect["interrupt_reason"]:
            errors.append(
                f"reason={body.get('interrupt_reason')} expected {expect['interrupt_reason']}"
            )
        recs = body.get("recommendations") or []
        if expect.get("min_recommendations") and len(recs) < expect["min_recommendations"]:
            errors.append(f"recs={len(recs)} < {expect['min_recommendations']}")
        needles = expect.get("title_must_include_any") or []
        if needles:
            blob = " ".join(f"{r.get('title','')} {r.get('reason','')}" for r in recs)
            if not any(n in blob for n in needles):
                errors.append(f"missing any of {needles} in recommendations")
        sources_needed = expect.get("sources_include") or []
        if sources_needed:
            detail = client.get(f"/api/v1/sessions/{session_id}").json()
            sources = {p.get("source") for p in detail.get("products") or []}
            missing = [s for s in sources_needed if s not in sources]
            if missing:
                errors.append(f"missing sources {missing}")
        if errors:
            failed += 1
            print(f"[FAIL] {case['id']}: {'; '.join(errors)}")
        else:
            passed += 1
            print(f"[PASS] {case['id']}")

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
