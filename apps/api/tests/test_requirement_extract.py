import asyncio

from app.domains.shopping.extract import extract_requirement_patch


def test_extract_with_injected_llm_returns_patch():
    async def fake_llm(prompt: str) -> dict:
        return {
            "budget_cny": 5000,
            "category": "笔记本",
            "confidence": {"budget_cny": "high", "category": "high"},
            "ambiguous_fields": [],
            "must_have": ["16G内存"],
            "preferences": [],
        }

    patch = asyncio.run(
        extract_requirement_patch(
            "预算5000买笔记本，至少16G内存",
            current={},
            llm=fake_llm,
        )
    )
    assert patch.budget_cny == 5000
    assert patch.category == "笔记本"
    assert "16G内存" in patch.must_have
