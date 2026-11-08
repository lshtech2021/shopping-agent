"""Phase 1 shopping agent: deterministic tool loop (+ optional LLM later)."""
from __future__ import annotations

import re
from typing import Any

from app.store import Session, store
from app.tools import ToolBox, BLOCKING


async def run_agent(session: Session, content: str) -> dict[str, Any]:
    tools = ToolBox(session)
    session.status = "running"
    store.touch(session)

    tools.update_requirements(content or session.requirement)

    if _wants_rate_limit_probe(content):
        site = session.sites[0]
        result = await tools.search(site, "rate_limit_me")
        if result.get("status") in BLOCKING:
            ask = tools.ask_user(
                "京东访问过频或需验证，请稍后再点「继续」。",
                reason=str(result.get("status")),
            )
            return _interrupt_response(session, ask["question"], ask["reason"])

    inventory = tools.query_inventory()
    if inventory["count"] == 0:
        for site in session.sites:
            query = _build_query(session)
            searched = await tools.search(site, query)
            if searched.get("status") in BLOCKING:
                ask = tools.ask_user(
                    f"{site} 暂时无法搜索（{searched.get('status')}），处理完成后点继续。",
                    reason=str(searched.get("status")),
                )
                return _interrupt_response(session, ask["question"], ask["reason"])
            scraped = await tools.scrape_listing(site=site, page=1)
            if scraped.get("status") in BLOCKING:
                ask = tools.ask_user(
                    f"{site} 列表抓取受阻（{scraped.get('status')}）。",
                    reason=str(scraped.get("status")),
                )
                return _interrupt_response(session, ask["question"], ask["reason"])

    inventory = tools.query_inventory()
    if inventory["count"] == 0:
        ask = tools.ask_user("当前条件下没有商品，请调整预算或需求后再试。", "empty")
        return _interrupt_response(session, ask["question"], ask["reason"])

    # Fetch details for top candidates (skip obvious accessories)
    detail_targets = [
        item
        for item in inventory["items"]
        if "配件" not in (item.get("title") or "")
        and "贴纸" not in (item.get("title") or "")
    ][:3]
    for item in detail_targets:
        detail = await tools.fetch_detail(item["product_id"])
        if detail.get("status") in BLOCKING:
            ask = tools.ask_user(
                f"详情抓取受阻（{detail.get('status')}），处理后可继续。",
                reason=str(detail.get("status")),
            )
            return _interrupt_response(session, ask["question"], ask["reason"])

    picks = _pick_recommendations(session)
    if not picks:
        ask = tools.ask_user("硬约束过滤后没有合适商品，是否放宽预算或必须项？", "empty")
        return _interrupt_response(session, ask["question"], ask["reason"])

    answer = _compose_answer(session, picks)
    result = tools.recommend(picks, answer)
    session.messages.append({"role": "assistant", "content": answer})
    store.touch(session)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": result["answer"],
        "recommendations": result["recommendations"],
        "interrupted": False,
        "interrupt_reason": None,
    }


async def resume_agent(session: Session, note: str) -> dict[str, Any]:
    session.interrupt_reason = None
    session.status = "running"
    store.touch(session)
    # Continue closed loop with original requirement / latest user note
    content = note.strip() if note and note.strip() not in {"continue", "user continued"} else session.requirement
    return await run_agent(session, content)


def _interrupt_response(session: Session, question: str, reason: str) -> dict[str, Any]:
    session.messages.append({"role": "assistant", "content": question})
    store.touch(session)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": question,
        "recommendations": session.recommendations,
        "interrupted": True,
        "interrupt_reason": reason,
    }


def _build_query(session: Session) -> str:
    req = session.requirements or {}
    parts = []
    if req.get("category"):
        parts.append(str(req["category"]))
    elif "笔记本" in (session.requirement or ""):
        parts.append("笔记本")
    else:
        parts.append("商品")
    if req.get("use_case"):
        parts.append(str(req["use_case"]))
    return " ".join(parts)[:80]


def _pick_recommendations(session: Session) -> list[dict[str, Any]]:
    inventory_items = ToolBox(session).query_inventory(limit=10)["items"]
    picks: list[dict[str, Any]] = []
    for item in inventory_items:
        product = session.products[item["product_id"]]
        title = product.get("title") or ""
        if "配件" in title or "贴纸" in title:
            continue
        reason_bits = []
        budget = session.requirements.get("budget_cny")
        if budget and product.get("price_cny") is not None:
            reason_bits.append(f"约¥{product['price_cny']:.0f}，在预算内")
        attrs = product.get("attributes") or {}
        if attrs.get("ram"):
            reason_bits.append(f"内存{attrs['ram']}")
        if attrs.get("ssd"):
            reason_bits.append(f"SSD {attrs['ssd']}")
        if session.requirements.get("use_case") == "开发":
            reason_bits.append("适合开发使用")
        picks.append(
            {
                "product_id": product["product_id"],
                "reason": "；".join(reason_bits) or "符合当前筛选",
            }
        )
        if len(picks) >= 2:
            break
    return picks


def _compose_answer(session: Session, picks: list[dict[str, Any]]) -> str:
    titles = []
    for pick in picks:
        product = session.products[pick["product_id"]]
        titles.append(product.get("title") or pick["product_id"])
    budget = session.requirements.get("budget_cny")
    budget_text = f"预算约¥{budget:.0f}，" if budget else ""
    return (
        f"已根据需求完成一轮选品（{budget_text}站点 {','.join(session.sites)}）。"
        f"推荐：{'；'.join(titles)}。"
    )


def _wants_rate_limit_probe(content: str) -> bool:
    return bool(re.search(r"rate_limit_me|触发限流", content or "", re.I))
