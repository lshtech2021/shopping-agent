from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_single_site_mock_closed_loop(mock_shopper):
    created = client.post(
        "/api/v1/sessions",
        json={
            "requirement": "开发笔记本 预算5000 内存32G",
            "site": "jd",
        },
    ).json()
    body = client.post(
        f"/api/v1/sessions/{created['session_id']}/messages",
        json={"content": "开发笔记本 预算5000 内存至少32G"},
    ).json()
    assert body["interrupted"] is False
    assert body["recommendations"]
    detail = client.get(f"/api/v1/sessions/{created['session_id']}").json()
    sources = {p["source"] for p in detail["products"]}
    assert sources == {"jd"}


def test_followup_reanalyze_without_new_search_when_stock_enough(mock_shopper):
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "开发笔记本 5000", "site": "jd"},
    ).json()
    sid = created["session_id"]
    first = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "开发笔记本 预算五千"},
    ).json()
    assert first["recommendations"]
    assert first["interrupted"] is False

    second = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "只看京东"},
    ).json()
    assert second["interrupted"] is False
    assert second["recommendations"]
    # Re-rank path: no new search quota burned on jd this turn.
    from app.store import store

    assert store.get(sid).quotas.get("jd", {}).get("search", 0) == 0


def test_chat_text_does_not_mutate_session_sites(mock_shopper):
    """Sites are fixed at create time (Side Panel); chat must not rewrite them."""
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "开发笔记本 5000", "site": "jd"},
    ).json()
    sid = created["session_id"]
    client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "开发笔记本 预算五千"},
    )
    follow = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "也看看 eBay"},
    ).json()
    assert follow["interrupted"] is False
    detail = client.get(f"/api/v1/sessions/{sid}").json()
    assert detail["sites"] == ["jd"]
    assert all(p["source"] == "jd" for p in detail["products"])


def test_tighter_ram_constraint_can_research_when_empty(mock_shopper):
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本 5000", "site": "jd"},
    ).json()
    sid = created["session_id"]
    client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "笔记本 预算五千"},
    )
    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "内存至少 32G"},
    ).json()
    assert body["interrupted"] is False
    assert body["recommendations"]
    assert all(
        "16G" not in item["title"] or "32" in item["title"]
        for item in body["recommendations"]
    )
