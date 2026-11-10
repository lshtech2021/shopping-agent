from fastapi.testclient import TestClient

from app.main import app
from conftest import _call
from test_phase5_hardening import _new_session

client = TestClient(app)

REQ_FULL = "帮我挑一台 5000 元内用于开发的笔记本，内存至少 32G"


def test_missing_budget_and_category_asks_structured_questions(mock_shopper):
    sid = _new_session("随便推荐点东西")
    body = client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "随便推荐点东西"}
    ).json()
    assert body["interrupted"] is True
    assert body["interrupt_reason"] == "clarify"
    ids = [q["id"] for q in body["clarify"]]
    assert "budget" in ids and "category" in ids
    for q in body["clarify"]:
        assert q["question"] and q["options"]
    assert client.get(f"/api/v1/sessions/{sid}").json()["pending_questions"]


def test_known_category_only_asks_budget(mock_shopper):
    sid = _new_session("帮我挑一台笔记本")
    body = client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "帮我挑一台笔记本"}
    ).json()
    assert body["interrupt_reason"] == "clarify"
    assert [q["id"] for q in body["clarify"]] == ["budget"]


def test_answers_apply_and_turn_completes(mock_shopper):
    sid = _new_session("帮我挑一台笔记本")
    first = client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "帮我挑一台笔记本"}
    ).json()
    assert first["interrupt_reason"] == "clarify"

    second = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "budget: 5000-8000\ncategory: 笔记本"},
    ).json()
    assert second["interrupted"] is False
    assert second["recommendations"]
    detail = client.get(f"/api/v1/sessions/{sid}").json()
    assert detail["requirements"]["budget_cny"] == 8000.0  # upper bound of the chosen range
    assert detail["requirements"]["category"] == "笔记本"
    assert detail["pending_questions"] == []


def test_conflict_question_for_low_budget_high_ram(mock_shopper):
    sid = _new_session("3000块的笔记本 内存至少32G")
    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "3000块的笔记本 内存至少32G"},
    ).json()
    assert body["interrupt_reason"] == "clarify"
    assert [q["id"] for q in body["clarify"]] == ["budget_ram_conflict"]

    relaxed = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "budget_ram_conflict: 放宽预算"},
    ).json()
    assert relaxed["interrupted"] is False and relaxed["recommendations"]
    req = client.get(f"/api/v1/sessions/{sid}").json()["requirements"]
    assert req["budget_cny"] is None and req["must_have"] == ["32G内存"]


def test_free_text_answer_is_parsed(mock_shopper):
    sid = _new_session("帮我挑一台笔记本")
    client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "帮我挑一台笔记本"})
    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "budget: 大概六千左右"},
    ).json()
    assert body["interrupted"] is False
    assert client.get(f"/api/v1/sessions/{sid}").json()["requirements"]["budget_cny"] == 6000.0


def test_skip_keyword_bypasses_clarify(mock_shopper):
    sid = _new_session("帮我挑一台笔记本")
    body = client.post(
        f"/api/v1/sessions/{sid}/messages",
        json={"content": "帮我挑一台笔记本，跳过澄清直接开始"},
    ).json()
    assert body["interrupted"] is False
    assert body["recommendations"]


def test_reanalyze_path_does_not_re_clarify(mock_shopper):
    sid = _new_session(REQ_FULL)
    client.post(f"/api/v1/sessions/{sid}/messages", json={"content": REQ_FULL})
    followup = client.post(
        f"/api/v1/sessions/{sid}/messages", json={"content": "重量轻一点的优先"}
    ).json()
    # Cached inventory → re-rank without browsing, so no interview.
    assert followup["interrupted"] is False


def test_llm_policy_can_clarify(fake_llm):
    model = fake_llm(
        [
            _call(
                "ask_user",
                questions=[
                    {
                        "id": "budget",
                        "question": "预算多少？",
                        "options": ["3000-5000", "5000-8000"],
                    }
                ],
                reason="clarify",
            )
        ]
    )
    sid = _new_session("笔记本")
    body = client.post(f"/api/v1/sessions/{sid}/messages", json={"content": "笔记本"}).json()
    assert body["interrupted"] and body["interrupt_reason"] == "clarify"
    assert body["clarify"][0]["id"] == "budget"
    assert model.calls == 1
    assert client.get(f"/api/v1/sessions/{sid}").json()["pending_questions"]
