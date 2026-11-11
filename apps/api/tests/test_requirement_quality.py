"""Requirement classification is an LLM review, not regex extraction."""
from __future__ import annotations

import asyncio

from app.domains.shopping import tools as shopping_tools
from app.domains.shopping.tools import build_shopping_tools
from app.requirement_review import RequirementReview, review_requirements
from app.store import store


class ReviewModel:
    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        return {
            "hard_must_have": ["32G内存"],
            "soft_must_have": ["性能足够高"],
            "dropped_must_have": ["不限品牌"],
            "clarify_question": "性能希望达到什么标准？",
            "clarify_options": ["16G+i5", "32G+i7"],
        }


class FailingModel:
    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _messages):
        raise RuntimeError("review unavailable")


class CapturingReviewModel(ReviewModel):
    def __init__(self):
        self.payload = None

    async def ainvoke(self, messages):
        import json

        self.payload = json.loads(messages[-1].content)
        return await super().ainvoke(messages)


class StructuredUnavailableReviewModel:
    def __init__(self):
        self.structured_calls = 0
        self.text_calls = 0

    def with_structured_output(self, _schema):
        outer = self

        class BrokenStructured:
            async def ainvoke(self, _messages):
                outer.structured_calls += 1
                raise RuntimeError(
                    "Error code: 400 - {'error': {'message': "
                    "'This response_format type is unavailable now', "
                    "'type': 'invalid_request_error'}}"
                )

        return BrokenStructured()

    async def ainvoke(self, _messages):
        self.text_calls += 1
        return {
            "hard_must_have": ["32G内存"],
            "soft_must_have": [],
            "dropped_must_have": [],
            "clarify_question": None,
            "clarify_options": [],
        }


def test_review_requirements_classifies_existing_items():
    review = asyncio.run(
        review_requirements(
            {
                "must_have": ["性能足够高", "32G内存", "不限品牌"],
                "preferences": [],
            },
            model=ReviewModel(),
        )
    )

    assert review is not None
    assert review.hard_must_have == ["32G内存"]
    assert review.soft_must_have == ["性能足够高"]
    assert review.dropped_must_have == ["不限品牌"]
    assert review.clarify_options == ["16G+i5", "32G+i7"]


def test_review_requirements_returns_none_without_items():
    assert (
        asyncio.run(
            review_requirements({"must_have": []}, model=ReviewModel())
        )
        is None
    )


def test_review_requirements_failure_is_non_fatal():
    assert (
        asyncio.run(
            review_requirements(
                {"must_have": ["性能足够高"]}, model=FailingModel()
            )
        )
        is None
    )


def test_review_requirements_does_not_send_preferences():
    model = CapturingReviewModel()
    asyncio.run(
        review_requirements(
            {
                "must_have": ["32G内存"],
                "preferences": ["性能足够高"],
            },
            model=model,
        )
    )
    assert model.payload is not None
    assert "preferences" not in model.payload


def test_default_review_model_caches_structured_failure(monkeypatch):
    model = StructuredUnavailableReviewModel()
    monkeypatch.setattr(
        "app.requirement_review.build_review_model", lambda: model
    )

    for _ in range(2):
        review = asyncio.run(
            review_requirements({"must_have": ["32G内存"]})
        )
        assert review is not None and review.hard_must_have == ["32G内存"]

    assert model.structured_calls == 1
    assert model.text_calls == 2


def test_update_requirements_tool_applies_llm_review(monkeypatch):
    session = store.create("需要笔记本", "ebay")
    state = {"plan": {"metadata": {"requirements": {}}}}
    specs = {
        spec.name: spec
        for spec in build_shopping_tools(
            shopping_tools.ToolBox(session), state
        )
    }
    spec = specs["update_requirements"]

    async def fake_review(_requirements, _session=None):
        return RequirementReview(
            hard_must_have=["32G内存", "1TB SSD"],
            soft_must_have=["性能足够高"],
            dropped_must_have=["不限品牌"],
            clarify_question="性能希望达到什么标准？",
            clarify_options=["16G+i5", "32G+i7"],
        )

    monkeypatch.setattr(
        shopping_tools, "_review_requirements", fake_review
    )

    result = asyncio.run(
        spec.handler(
            spec.args_model(
                must_have=["性能足够高", "32G内存", "不限品牌"]
            ),
            None,
        )
    )

    req = session.requirements
    assert req["must_have"] == ["32G内存"]
    assert "性能足够高" in req["preferences"]
    assert "不限品牌" not in req["preferences"]
    assert "1TB SSD" not in req["must_have"]
    assert result.data["needs_clarify"] is True
    assert result.data["clarify_question"]["question"] == (
        "性能希望达到什么标准？"
    )
    assert result.control == "wait_user"
    assert result.output["pending_user"]["question"] == (
        "性能希望达到什么标准？"
    )
    assert result.output["pending_user"]["options"] == ["16G+i5", "32G+i7"]
    assert state["plan"]["metadata"]["requirements"]["must_have"] == [
        "32G内存"
    ]


def test_update_requirements_continues_without_question(monkeypatch):
    session = store.create("需要笔记本", "ebay")
    specs = {
        spec.name: spec
        for spec in build_shopping_tools(
            shopping_tools.ToolBox(session), {}
        )
    }
    spec = specs["update_requirements"]

    async def fake_review(_requirements, _session=None):
        return RequirementReview(
            hard_must_have=["32G内存"],
            soft_must_have=[],
            dropped_must_have=[],
        )

    monkeypatch.setattr(
        shopping_tools, "_review_requirements", fake_review
    )

    result = asyncio.run(
        spec.handler(
            spec.args_model(must_have=["32G内存"]),
            None,
        )
    )

    assert result.data["needs_clarify"] is False
    assert result.control == "continue"


def test_review_requirements_reports_provider_fallback():
    events = []

    def on_event(stage, data):
        events.append((stage, data))

    asyncio.run(
        review_requirements(
            {"must_have": ["32G内存"]},
            model=StructuredUnavailableReviewModel(),
            on_event=on_event,
        )
    )

    assert events
    stage, data = events[0]
    assert stage == "provider.fallback"
    assert data["component"] == "requirement_review"
    assert data["error_code"] == "provider_structured_output_unsupported"
