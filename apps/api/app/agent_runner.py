"""Agent entrypoint: one turn per user message / resume.

Two interchangeable policies drive the same nine tools (``ToolBox``):

* ``llm_agent``  — LangGraph tool-calling loop (used when ``SA_LLM_API_KEY`` is set).
* rule policy    — deterministic pipeline below (offline default, eval baseline, mock demos).

All hard guarantees (quotas, risk-site lockout, recommend id validation) live in
``ToolBox`` so they hold for both policies.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app import llm
from app.config import settings
from app.store import Session, store
from app.tools import BLOCKING, CATEGORY_OPTIONS, ToolBox, english_term, min_ram_gb
from app.trace import log_event, timed_step, turn_logs

logger = logging.getLogger(__name__)


async def run_agent(session: Session, content: str, answers: dict[str, str] | None = None) -> dict[str, Any]:
    session.turn += 1
    session.status = "running"
    session.interrupt_reason = None
    session.quotas = {}  # design §6: quotas are per turn, not per session
    tools = ToolBox(session)
    text = (content or session.requirement or "").strip()
    clarified = False
    if answers:
        if session.pending_questions:
            tools.apply_clarify(answers)
            clarified = True  # answers are merged; don't re-parse raw text over them
        else:
            log_event(
                session,
                "收到澄清回答但没有待答问题，已忽略",
                stage="agent.clarify",
                source="agent",
                level="warning",
                data={"answers": answers},
            )
    policy = "llm" if llm.llm_enabled() else "rule"
    log_event(
        session,
        "Agent 回合开始",
        stage="agent.run",
        source="agent",
        data={
            "policy": policy,
            "content": text[:200],
            "sites": session.sites,
            "searched_sites": session.searched_sites,
            "product_count": len(session.products),
            "mock_browser": tools.mock_mode,
        },
        persist=True,
    )

    try:
        if policy == "llm":
            from app.llm_agent import run_llm_policy

            result = await run_llm_policy(session, text, tools, skip_parse=clarified)
        else:
            result = await _run_rule_policy(session, text, tools, skip_parse=clarified)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent turn failed sid=%s", session.id[:8])
        log_event(
            session,
            f"回合异常：{type(exc).__name__}: {exc}",
            stage="agent.error",
            source="agent",
            level="error",
            data={"policy": policy},
        )
        ask = tools.ask_user(f"执行出错（{type(exc).__name__}），可点「继续」重试。", "agent_error")
        result = interrupt_response(session, ask["question"], ask["reason"])

    store.touch(session)
    result["execution_logs"] = turn_logs(session)
    return result


async def resume_agent(session: Session, note: str) -> dict[str, Any]:
    cleared = dict(session.blocked_sites)
    session.blocked_sites.clear()
    log_event(
        session,
        "恢复中断回合（用户已处理风控）" if cleared else "恢复中断回合",
        stage="agent.resume",
        source="agent",
        data={"note": note, "cleared_sites": cleared},
    )
    content = (
        note.strip()
        if note and note.strip() not in {"continue", "user continued"}
        else session.requirement
    )
    return await run_agent(session, content)


# --- rule policy -----------------------------------------------------------


def blocked_text(site: str, result: dict[str, Any], what: str) -> str:
    status = str(result.get("status"))
    error = result.get("error")
    if error == "no_extension_online":
        return "没有检测到在线的浏览器插件。请打开 Chrome 并确认 Side Panel 显示 WS: connected 后点「继续」。"
    if error == "extension_disconnected":
        return "插件连接中断，请确认插件在线后点「继续」。"
    if error == "job_timeout":
        return f"{site} {what}超时（页面可能卡住或需要验证），处理后点「继续」。"
    if status in {"rate_limited", "captcha", "login_required"}:
        label = {"rate_limited": "访问过频", "captcha": "出现验证码", "login_required": "需要登录"}[status]
        return f"{site} {what}受阻：{label}。请在浏览器中处理后点「继续」。"
    return f"{site} {what}受阻（{error or status}），处理后点「继续」。"


async def _run_rule_policy(
    session: Session, text: str, tools: ToolBox, *, skip_parse: bool = False
) -> dict[str, Any]:
    if skip_parse:
        log_event(
            session,
            "本轮以澄清回答为准，跳过文本重解析",
            stage="agent.requirements",
            source="agent",
            level="debug",
        )
    else:
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
    )

    # Test hook: only meaningful against the mock browser.
    if settings.mock_browser and _wants_rate_limit_probe(text):
        site = session.sites[0]
        with timed_step(session, f"限流探针 search:{site}", stage="agent.rate_probe") as meta:
            result = await tools.search(site, "rate_limit_me")
            meta["summary"] = f"探针结果 {result.get('status')}"
            meta["status"] = result.get("status")
        if result.get("status") in BLOCKING:
            ask = tools.ask_user(blocked_text(site, result, "搜索"), str(result.get("status")))
            return interrupt_response(session, ask["question"], ask["reason"])

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
        # Interview gate: key filter conditions missing/conflicting → ask first,
        # in one structured batch, before spending any browse calls. Skipped when
        # this turn already applied clarify answers (the user had their say).
        if not skip_parse and not _skip_clarify(text):
            gaps = _clarify_gaps(session)
            if gaps:
                return _clarify_turn(session, tools, gaps)
        for site in sites_needing_search:
            query = _build_query(session, site)
            with timed_step(
                session, f"搜索 {site}", stage="agent.search", data={"query": query}
            ) as meta:
                searched = await tools.search(site, query)
                meta["status"] = searched.get("status")
                meta["summary"] = f"search={searched.get('status')}"
            if searched.get("status") in BLOCKING:
                ask = tools.ask_user(
                    blocked_text(site, searched, "搜索"), str(searched.get("status"))
                )
                return interrupt_response(session, ask["question"], ask["reason"])
            with timed_step(session, f"抓列表 {site}", stage="agent.scrape") as meta:
                scraped = await tools.scrape_listing(site=site, page=1)
                meta["status"] = scraped.get("status")
                meta["accepted"] = scraped.get("accepted")
                meta["summary"] = (
                    f"scrape={scraped.get('status')} accepted={scraped.get('accepted')}"
                )
            if scraped.get("status") in BLOCKING:
                ask = tools.ask_user(
                    blocked_text(site, scraped, "列表抓取"), str(scraped.get("status"))
                )
                return interrupt_response(session, ask["question"], ask["reason"])
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
        return interrupt_response(session, ask["question"], ask["reason"])

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
        if detail.get("status") in BLOCKING and detail.get("error") != "quota_exceeded":
            ask = tools.ask_user(
                blocked_text(item.get("source") or "站点", detail, "详情抓取"),
                str(detail.get("status")),
            )
            return interrupt_response(session, ask["question"], ask["reason"])

    with timed_step(session, "生成推荐", stage="agent.recommend") as meta:
        picks = _pick_recommendations(session)
        meta["pick_count"] = len(picks)
        meta["summary"] = f"选出 {len(picks)} 条"
    if not picks:
        ask = tools.ask_user("硬约束过滤后没有合适商品，是否放宽预算或必须项？", "empty")
        return interrupt_response(session, ask["question"], ask["reason"])

    mode = "search" if sites_needing_search else "reanalyze"
    answer = _compose_answer(session, picks, mode=mode)
    result = tools.recommend(picks, answer)
    session.messages.append({"role": "assistant", "content": answer})
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
    }


def interrupt_response(session: Session, question: str, reason: str) -> dict[str, Any]:
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
    }


def _clarify_gaps(session: Session) -> list[dict[str, Any]]:
    """Filter conditions we cannot pin down → ask the user (one batch, with options)."""
    req = session.requirements or {}
    questions: list[dict[str, Any]] = []
    budget = req.get("budget_cny")
    if budget is None:
        questions.append(
            {
                "id": "budget",
                "question": "这次的预算范围大概是多少？（用于过滤商品价格）",
                "options": ["3000以内", "3000-5000", "5000-8000", "8000-15000", "1.5万以上", "不限预算"],
            }
        )
    category = req.get("category")
    if not category:
        questions.append(
            {
                "id": "category",
                "question": "想买什么品类？",
                "options": [*CATEGORY_OPTIONS[:6], "其他（请直接输入）"],
            }
        )
    else:
        min_ram = min_ram_gb(req)
        if budget is not None and min_ram and min_ram >= 32 and budget < 4500:
            questions.append(
                {
                    "id": "budget_ram_conflict",
                    "question": (
                        f"预算约¥{budget:.0f} 与 内存≥{min_ram}G 同时满足的{category}很少，"
                        "优先保证哪一个？"
                    ),
                    "options": ["放宽预算", "内存降到16G", "维持硬约束"],
                }
            )
    return questions


def _skip_clarify(text: str) -> bool:
    return bool(re.search(r"跳过澄清|不用问|别问了|直接搜|直接开始", text or ""))


def _clarify_turn(
    session: Session, tools: ToolBox, gaps: list[dict[str, Any]]
) -> dict[str, Any]:
    ask = tools.ask_clarify(gaps)
    question_text = "为了精准筛选，请先确认以下条件（可点选候选项，或直接填写）：\n" + "\n".join(
        f"{i}. {q['question']} 候选：{'/'.join(q['options'])}" for i, q in enumerate(ask["questions"], 1)
    )
    session.messages.append({"role": "assistant", "content": question_text})
    log_event(
        session,
        f"发起澄清：{len(gaps)} 个问题",
        stage="agent.clarify",
        source="agent",
        data={"questions": gaps},
    )
    store.touch(session)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": question_text,
        "recommendations": session.recommendations,
        "interrupted": True,
        "interrupt_reason": "clarify",
        "clarify": gaps,
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
    category = req.get("category")
    if site == "ebay":
        term = english_term(str(category)) if category else "item"
        return term[:80]

    parts = [str(category) if category else "商品"]
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
