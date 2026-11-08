"""Shopping agent: tool loop with multi-turn reanalyze / research."""
from __future__ import annotations

import re
from typing import Any

from app.store import Session, store
from app.tools import BLOCKING, ToolBox
from app.trace import log_event, timed_step


async def run_agent(session: Session, content: str) -> dict[str, Any]:
    tools = ToolBox(session)
    session.status = "running"
    store.touch(session)

    text = (content or session.requirement or "").strip()
    log_event(
        session,
        "Agent 回合开始",
        stage="agent.run",
        source="agent",
        data={
            "content": text[:200],
            "sites": session.sites,
            "searched_sites": session.searched_sites,
            "product_count": len(session.products),
            "mock_browser": tools.mock_mode,
        },
    )

    with timed_step(session, "解析需求", stage="agent.requirements", data={"text": text[:120]}):
        tools.update_requirements(text)
        _apply_site_directives(session, text)

    log_event(
        session,
        "需求快照",
        stage="agent.requirements",
        source="agent",
        level="debug",
        data={"requirements": session.requirements, "sites": session.sites},
        persist=False,
    )

    if _wants_rate_limit_probe(text):
        site = session.sites[0]
        with timed_step(session, f"限流探针 search:{site}", stage="agent.rate_probe") as meta:
            result = await tools.search(site, "rate_limit_me")
            meta["summary"] = f"探针结果 {result.get('status')}"
            meta["status"] = result.get("status")
        if result.get("status") in BLOCKING:
            ask = tools.ask_user(
                f"{site} 访问过频或需验证，请稍后再点「继续」。",
                reason=str(result.get("status")),
            )
            return _interrupt_response(session, ask["question"], ask["reason"])

    inventory = tools.query_inventory()
    sites_needing_search = _sites_to_search(session, text, inventory["count"])
    log_event(
        session,
        "搜索计划",
        stage="agent.plan",
        source="agent",
        data={
            "inventory_count": inventory["count"],
            "total_cached": inventory.get("total_cached"),
            "sites_to_search": sites_needing_search,
            "mode": "search" if sites_needing_search else "reanalyze",
        },
    )

    if sites_needing_search:
        for site in sites_needing_search:
            query = _build_query(session, site)
            with timed_step(
                session,
                f"搜索 {site}",
                stage="agent.search",
                data={"query": query},
            ) as meta:
                searched = await tools.search(site, query)
                meta["status"] = searched.get("status")
                meta["summary"] = f"search={searched.get('status')}"
            if searched.get("status") in BLOCKING:
                ask = tools.ask_user(
                    f"{site} 暂时无法搜索（{searched.get('status')}），处理完成后点继续。",
                    reason=str(searched.get("status")),
                )
                return _interrupt_response(session, ask["question"], ask["reason"])
            with timed_step(session, f"抓列表 {site}", stage="agent.scrape") as meta:
                scraped = await tools.scrape_listing(site=site, page=1)
                meta["status"] = scraped.get("status")
                meta["accepted"] = scraped.get("accepted")
                meta["summary"] = (
                    f"scrape={scraped.get('status')} accepted={scraped.get('accepted')}"
                )
            if scraped.get("status") in BLOCKING:
                ask = tools.ask_user(
                    f"{site} 列表抓取受阻（{scraped.get('status')}）。",
                    reason=str(scraped.get("status")),
                )
                return _interrupt_response(session, ask["question"], ask["reason"])
            if site not in session.searched_sites:
                session.searched_sites.append(site)
        inventory = tools.query_inventory()
    else:
        inventory = tools.query_inventory()
        log_event(
            session,
            "跳过搜索，复用缓存重排",
            stage="agent.reanalyze",
            source="agent",
            data={"inventory_count": inventory["count"]},
        )

    if inventory["count"] == 0:
        ask = tools.ask_user("当前条件下没有商品，请调整预算或需求后再试。", "empty")
        return _interrupt_response(session, ask["question"], ask["reason"])

    detail_targets = [
        item
        for item in inventory["items"]
        if not item.get("detail_fetched")
        and "配件" not in (item.get("title") or "")
        and "贴纸" not in (item.get("title") or "")
        and not re.search(r"shop on ebay", item.get("title") or "", re.I)
    ][:3]
    log_event(
        session,
        f"准备抓取详情 {len(detail_targets)} 条",
        stage="agent.detail.plan",
        source="agent",
        data={"targets": [t.get("product_id") for t in detail_targets]},
    )
    for item in detail_targets:
        with timed_step(
            session,
            f"详情 {item['product_id']}",
            stage="agent.detail",
            data={"title": (item.get("title") or "")[:80]},
        ) as meta:
            detail = await tools.fetch_detail(item["product_id"])
            meta["status"] = detail.get("status")
            meta["summary"] = f"detail={detail.get('status')}"
        if detail.get("status") in BLOCKING:
            ask = tools.ask_user(
                f"详情抓取受阻（{detail.get('status')}），处理后可继续。",
                reason=str(detail.get("status")),
            )
            return _interrupt_response(session, ask["question"], ask["reason"])

    with timed_step(session, "生成推荐", stage="agent.recommend") as meta:
        picks = _pick_recommendations(session)
        meta["pick_count"] = len(picks)
        meta["summary"] = f"选出 {len(picks)} 条"
    if not picks:
        ask = tools.ask_user("硬约束过滤后没有合适商品，是否放宽预算或必须项？", "empty")
        return _interrupt_response(session, ask["question"], ask["reason"])

    mode = "search" if sites_needing_search else "reanalyze"
    answer = _compose_answer(session, picks, mode=mode)
    result = tools.recommend(picks, answer)
    session.messages.append({"role": "assistant", "content": answer})
    store.touch(session)
    log_event(
        session,
        "Agent 回合完成",
        stage="agent.done",
        source="agent",
        data={
            "mode": mode,
            "recommendation_count": len(result["recommendations"]),
            "product_count": len(session.products),
            "quotas": session.quotas,
        },
    )
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": result["answer"],
        "recommendations": result["recommendations"],
        "interrupted": False,
        "interrupt_reason": None,
        "execution_logs": session.execution_logs[-80:],
    }


async def resume_agent(session: Session, note: str) -> dict[str, Any]:
    log_event(
        session,
        "恢复中断回合",
        stage="agent.resume",
        source="agent",
        data={"note": note},
    )
    session.interrupt_reason = None
    session.status = "running"
    store.touch(session)
    content = (
        note.strip()
        if note and note.strip() not in {"continue", "user continued"}
        else session.requirement
    )
    return await run_agent(session, content)


def _interrupt_response(session: Session, question: str, reason: str) -> dict[str, Any]:
    log_event(
        session,
        f"中断：{reason}",
        stage="agent.interrupt",
        source="agent",
        level="warning",
        data={"question": question, "reason": reason},
    )
    session.messages.append({"role": "assistant", "content": question})
    store.touch(session)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": question,
        "recommendations": session.recommendations,
        "interrupted": True,
        "interrupt_reason": reason,
        "execution_logs": session.execution_logs[-80:],
    }


def _apply_site_directives(session: Session, text: str) -> None:
    lower = text.lower()
    if re.search(r"只看京东|只要京东|仅京东", text):
        session.sites = ["jd"]
    elif re.search(r"只看\s*ebay|只要\s*ebay|仅\s*ebay", text, re.I):
        session.sites = ["ebay"]
    elif re.search(r"也看看\s*ebay|加上\s*ebay|再看\s*ebay|以及\s*ebay", text, re.I):
        if "ebay" not in session.sites:
            session.sites.append("ebay")
    elif re.search(r"也看看京东|加上京东|再看京东", text):
        if "jd" not in session.sites:
            session.sites.append("jd")
    elif "jd" in lower and "ebay" in lower and re.search(r"两站|都看|全部", text):
        session.sites = ["jd", "ebay"]
    store.touch(session)


def _sites_to_search(session: Session, text: str, inventory_count: int) -> list[str]:
    if _explicit_research(text):
        return list(session.sites)

    missing = [
        site
        for site in session.sites
        if site not in session.searched_sites
        and not any(p.get("source") == site for p in session.products.values())
    ]
    if missing:
        return missing

    if inventory_count == 0:
        return list(session.sites)

    # New hard constraints that empty the current activation set → research.
    preview = ToolBox(session).query_inventory()
    if preview["count"] == 0:
        return list(session.sites)

    return []


def _explicit_research(text: str) -> bool:
    return bool(
        re.search(
            r"重新搜索|再搜|换一批|重搜|research|search again",
            text or "",
            re.I,
        )
    )


def _build_query(session: Session, site: str) -> str:
    req = session.requirements or {}
    if site == "ebay":
        parts = []
        if req.get("category") in {None, "笔记本"} or "笔记本" in (session.requirement or ""):
            parts.append("laptop")
        elif req.get("category"):
            parts.append(str(req["category"]))
        else:
            parts.append("item")
        if req.get("use_case") == "开发":
            parts.append("development")
        return " ".join(parts)[:80]

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
        if re.search(r"shop on ebay", title, re.I):
            continue
        reason_bits = []
        budget = session.requirements.get("budget_cny")
        if budget and product.get("price_cny") is not None:
            reason_bits.append(f"约¥{product['price_cny']:.0f}，在预算内")
        attrs = product.get("attributes") or {}
        ram = attrs.get("ram") or attrs.get("RAM Size") or attrs.get("内存")
        if ram:
            reason_bits.append(f"内存{ram}")
        ssd = attrs.get("ssd") or attrs.get("SSD Capacity") or attrs.get("硬盘")
        if ssd:
            reason_bits.append(f"SSD {ssd}")
        if session.requirements.get("use_case") == "开发":
            reason_bits.append("适合开发使用")
        reason_bits.append(f"来源{product.get('source')}")
        picks.append(
            {
                "product_id": product["product_id"],
                "reason": "；".join(reason_bits) or "符合当前筛选",
            }
        )
        if len(picks) >= 2:
            break
    return picks


def _compose_answer(session: Session, picks: list[dict[str, Any]], *, mode: str) -> str:
    titles = []
    for pick in picks:
        product = session.products[pick["product_id"]]
        titles.append(product.get("title") or pick["product_id"])
    budget = session.requirements.get("budget_cny")
    budget_text = f"预算约¥{budget:.0f}，" if budget else ""
    mode_text = "重新搜索后" if mode == "search" else "基于已有商品重排后"
    return (
        f"{mode_text}完成选品（{budget_text}站点 {','.join(session.sites)}）。"
        f"推荐：{'；'.join(titles)}。"
    )


def _wants_rate_limit_probe(content: str) -> bool:
    return bool(re.search(r"rate_limit_me|触发限流", content or "", re.I))
