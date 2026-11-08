from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_dual_site_mock_closed_loop():
    created = client.post(
        "/api/v1/sessions",
        json={
            "requirement": "开发笔记本 预算5000 内存32G",
            "sites": ["jd", "ebay"],
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
    assert "jd" in sources
    assert "ebay" in sources


def test_followup_reanalyze_without_new_search_when_stock_enough():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "开发笔记本 5000", "sites": ["jd"]},
    ).json()
    sid = created["session_id"]
    first = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "开发笔记本 预算五千"},
    ).json()
    assert first["recommendations"]
    assert "重新搜索后" in first["reply"] or "完成选品" in first["reply"]

    second = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "只看京东"},
    ).json()
    assert second["interrupted"] is False
    assert second["recommendations"]
    assert "重排" in second["reply"]


def test_followup_adds_ebay_triggers_search_for_new_site_only():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "开发笔记本 5000", "sites": ["jd"]},
    ).json()
    sid = created["session_id"]
    client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "开发笔记本 预算五千"},
    )
    before = client.get(f"/api/v1/sessions/{sid}").json()
    jd_count = sum(1 for p in before["products"] if p["source"] == "jd")

    after = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "也看看 eBay"},
    ).json()
    assert after["interrupted"] is False
    detail = client.get(f"/api/v1/sessions/{sid}").json()
    assert detail["sites"] == ["jd", "ebay"]
    assert sum(1 for p in detail["products"] if p["source"] == "jd") == jd_count
    assert any(p["source"] == "ebay" for p in detail["products"])
    assert "重新搜索后" in after["reply"]


def test_tighter_ram_constraint_can_research_when_empty():
    created = client.post(
        "/api/v1/sessions",
        json={"requirement": "笔记本 5000", "sites": ["jd"]},
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
