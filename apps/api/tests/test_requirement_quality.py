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

    async def fake_review(_requirements):
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
    assert state["plan"]["metadata"]["requirements"]["must_have"] == [
        "32G内存"
    ]
