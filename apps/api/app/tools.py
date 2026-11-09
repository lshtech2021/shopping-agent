"""Nine shopping tools: memory + browse (+ mock) + recommend/ask_user."""
from __future__ import annotations

import asyncio
import random
import re
import time
import uuid
from typing import Any, Optional

from app.config import settings
from app import mock_browser
from app.store import Session, store
from app.trace import log_event
from app.ws_hub import hub

QUOTA_LIMITS = {"search": 2, "next_page": 2, "fetch_detail": 5}
BLOCKING = {"rate_limited", "captcha", "login_required", "blocked"}
# Site-level risk states: once seen, the site stays closed until the user resumes.
SITE_RISK = {"rate_limited", "captcha", "login_required"}

_last_browse_at: dict[str, float] = {}


async def _throttle(site: str) -> float:
    """Keep >= browse_min_interval_s (+ jitter) between live calls to one site."""
    wait = 0.0
    last = _last_browse_at.get(site)
    if last is not None:
        gap = settings.browse_min_interval_s + random.uniform(0, settings.browse_jitter_s)
        wait = max(0.0, gap - (time.monotonic() - last))
    if wait > 0:
        await asyncio.sleep(wait)
    _last_browse_at[site] = time.monotonic()
    return wait


class ToolBox:
    def __init__(self, session: Session):
        self.session = session
        self.active_site: Optional[str] = None
        self.active_query: Optional[str] = None
        self.page = 0
        self.last_ask: Optional[str] = None

    @property
    def mock_mode(self) -> bool:
        return bool(settings.mock_browser)

    def _quota(self, site: str, tool: str) -> dict[str, int]:
        site_q = self.session.quotas.setdefault(site, {})
        return site_q

    def _guard(self, site: str, tool: str) -> Optional[dict[str, Any]]:
        """Design §6: a site that showed risk signals stays closed until the user
        resumes. Refusals are free — they must not consume quota."""
        risk = self.session.blocked_sites.get(site)
        if not risk:
            return None
        refused = {
            "status": "blocked",
            "error": "site_blocked_until_resume",
            "site": site,
            "reason": risk,
        }
        log_event(
            self.session,
            f"拒绝 browse:{tool}：{site} 处于 {risk}，等待用户处理后继续",
            stage="tool.guard",
            source="tool",
            level="warning",
            data=refused,
        )
        return refused

    def _check_quota(self, site: str, tool: str) -> Optional[dict[str, Any]]:
        refused = self._guard(site, tool)
        if refused:
            return refused
        used = self._quota(site, tool).get(tool, 0)
        limit = QUOTA_LIMITS.get(tool, 99)
        if used >= limit:
            blocked = {
                "status": "blocked",
                "error": "quota_exceeded",
                "tool": tool,
                "site": site,
                "used": used,
                "limit": limit,
            }
            log_event(
                self.session,
                f"配额耗尽 {site}.{tool}",
                stage="tool.quota",
                source="tool",
                level="warning",
                data=blocked,
            )
            return blocked
        return None

    def _bump_quota(self, site: str, tool: str) -> None:
        q = self._quota(site, tool)
        q[tool] = q.get(tool, 0) + 1

    async def _browse(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        mode = "mock" if settings.mock_browser else "live"
        site = str(args.get("site") or "").lower()
        if not settings.mock_browser and hub.online_count > 0:
            waited = await _throttle(site)
            if waited > 0:
                log_event(
                    self.session,
                    f"节流等待 {waited:.1f}s ({site})",
                    stage="tool.throttle",
                    source="tool",
                    level="debug",
                    data={"site": site, "waited_s": round(waited, 2)},
                )
        started = time.perf_counter()
        log_event(
            self.session,
            f"调用 browse:{tool} ({mode})",
            stage=f"tool.{tool}",
            source="tool",
            level="debug",
            data={"args": args, "mode": mode},
            persist=False,
        )
        try:
            if settings.mock_browser:
                if tool == "search":
                    result = mock_browser.mock_search(args.get("site", ""), args.get("query", ""))
                elif tool == "scrape_listing":
                    result = mock_browser.mock_scrape_listing(
                        args.get("site", ""), int(args.get("page") or 1)
                    )
                elif tool == "next_page":
                    page = int(args.get("page") or 2)
                    result = mock_browser.mock_scrape_listing(args.get("site", ""), page)
                elif tool == "fetch_detail":
                    result = mock_browser.mock_fetch_detail(
                        args.get("site", ""), args.get("source_product_id", "")
                    )
                else:
                    result = {"status": "parse_error", "error": f"unknown browse tool {tool}"}
            else:
                result = await hub.dispatch_job(
                    tool, args, timeout=settings.job_timeout_s, session=self.session
                )
        except Exception as exc:  # noqa: BLE001
            log_event(
                self.session,
                f"browse:{tool} 异常",
                stage=f"tool.{tool}",
                source="tool",
                level="error",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={"error": str(exc), "args": args},
            )
            raise

        if result.get("status") in SITE_RISK and site:
            self.session.blocked_sites[site] = str(result["status"])
        summary: dict[str, Any] = {
            "status": result.get("status"),
            "mode": mode,
            "error": result.get("error"),
        }
        if "items" in result:
            summary["item_count"] = len(result.get("items") or [])
        if result.get("extension_logs"):
            for entry in result.get("extension_logs") or []:
                if isinstance(entry, dict):
                    log_event(
                        self.session,
                        str(entry.get("message") or "extension"),
                        stage=str(entry.get("stage") or f"extension.{tool}"),
                        source="extension",
                        level=str(entry.get("level") or "info"),
                        duration_ms=entry.get("duration_ms"),
                        data=entry.get("data"),
                        persist=False,
                    )
            summary["extension_log_count"] = len(result.get("extension_logs") or [])
        log_event(
            self.session,
            f"browse:{tool} → {result.get('status')}",
            stage=f"tool.{tool}",
            source="tool",
            level="warning" if result.get("status") in BLOCKING else "info",
            duration_ms=(time.perf_counter() - started) * 1000,
            data=summary,
        )
        return result

    # --- memory ---

    def get_session(self) -> dict[str, Any]:
        products = list(self.session.products.values())
        return {
            "session_id": self.session.id,
            "status": self.session.status,
            "sites": self.session.sites,
            "requirement": self.session.requirement,
            "requirements": self.session.requirements,
            "product_count": len(products),
            "inventory_summary": _inventory_summary(products),
            "recommendations": self.session.recommendations,
            "interrupt_reason": self.session.interrupt_reason,
            "quotas": self.session.quotas,
        }

    def update_requirements(self, text: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        req = dict(self.session.requirements or {})
        req["raw"] = text or req.get("raw") or self.session.requirement
        budget = _extract_budget(text)
        if budget is not None:
            req["budget_cny"] = budget
        # Only record what the user said; never infer category/use case on our own.
        category = _extract_category(text)
        if category:
            req["category"] = category
        ram = _extract_min_ram_gb(text)
        if ram is not None:
            must = [m for m in (req.get("must_have") or []) if not _RAM_MUST.search(str(m))]
            must.append(f"{ram}G内存")
            req["must_have"] = must
        if patch:
            for key, value in patch.items():
                if value is None:
                    continue
                if key in {"must_have", "preferences"}:
                    merged = list(req.get(key) or [])
                    for item in value if isinstance(value, list) else [value]:
                        if item and item not in merged:
                            merged.append(item)
                    req[key] = merged
                else:
                    req[key] = value
        self.session.requirements = req
        store.touch(self.session)
        return {"ok": True, "requirements": req}

    def query_inventory(
        self,
        *,
        limit: int = 20,
        prefer_detail: bool = False,
    ) -> dict[str, Any]:
        products = list(self.session.products.values())
        budget = self.session.requirements.get("budget_cny")
        min_ram = min_ram_gb(self.session.requirements or {})
        filtered = []
        for product in products:
            if product.get("source") not in self.session.sites:
                continue
            if budget is not None and product.get("price_cny") is not None:
                if float(product["price_cny"]) > float(budget):
                    continue
            if min_ram is not None:
                ram = _ram_gb(product)
                if ram is None or ram < min_ram:
                    continue
            if prefer_detail and not product.get("detail_fetched"):
                continue
            filtered.append(product)
        filtered.sort(key=lambda p: (not p.get("detail_fetched"), p.get("price_cny") or 1e18))
        samples = filtered[:limit]
        return {
            "count": len(filtered),
            "total_cached": len(products),
            "items": [
                {
                    "product_id": p["product_id"],
                    "source": p["source"],
                    "title": p["title"],
                    "price": p.get("price"),
                    "currency": p.get("currency"),
                    "price_cny": p.get("price_cny"),
                    "detail_fetched": p.get("detail_fetched", False),
                    "url": p.get("url"),
                }
                for p in samples
            ],
        }

    # --- browse ---

    async def search(self, site: str, query: str) -> dict[str, Any]:
        site = site.lower()
        blocked = self._check_quota(site, "search")
        if blocked:
            return blocked
        result = await self._browse("search", {"site": site, "query": query})
        self._bump_quota(site, "search")
        if result.get("status") == "ok":
            self.active_site = site
            self.active_query = query
            self.page = 1
        return result

    async def scrape_listing(self, site: str | None = None, page: int | None = None) -> dict[str, Any]:
        site = (site or self.active_site or (self.session.sites[0] if self.session.sites else "")).lower()
        page = int(page or self.page or 1)
        refused = self._guard(site, "scrape_listing")
        if refused:
            return refused
        result = await self._browse("scrape_listing", {"site": site, "page": page})
        if result.get("status") != "ok":
            return result
        accepted = 0
        for item in result.get("items") or []:
            if self._upsert_listing(site, item):
                accepted += 1
        if site and site not in self.session.searched_sites:
            self.session.searched_sites.append(site)
        store.touch(self.session)
        return {
            **result,
            "accepted": accepted,
            "product_count": len(self.session.products),
        }

    async def next_page(self, site: str | None = None) -> dict[str, Any]:
        site = (site or self.active_site or "").lower()
        blocked = self._check_quota(site, "next_page")
        if blocked:
            return blocked
        self.page = int(self.page or 1) + 1
        result = await self._browse("next_page", {"site": site, "page": self.page})
        self._bump_quota(site, "next_page")
        if result.get("status") != "ok":
            return result
        accepted = 0
        for item in result.get("items") or []:
            if self._upsert_listing(site, item):
                accepted += 1
        store.touch(self.session)
        return {**result, "accepted": accepted, "page": self.page}

    async def fetch_detail(self, product_id: str) -> dict[str, Any]:
        product = self.session.products.get(product_id)
        if product is None:
            return {"status": "parse_error", "error": "unknown product_id"}
        site = product["source"]
        blocked = self._check_quota(site, "fetch_detail")
        if blocked:
            return blocked
        result = await self._browse(
            "fetch_detail",
            {
                "site": site,
                "source_product_id": product["source_product_id"],
                "url": product.get("url"),
                "product_id": product_id,
            },
        )
        self._bump_quota(site, "fetch_detail")
        if result.get("status") != "ok":
            return result
        product["detail_fetched"] = True
        product["detail"] = result
        if result.get("title"):
            product["title"] = result["title"]
        if result.get("price") is not None:
            product["price"] = result["price"]
            product["currency"] = result.get("currency") or product.get("currency")
            product["price_cny"] = _to_cny(product["price"], product.get("currency"))
        if result.get("attributes"):
            product["attributes"] = result["attributes"]
        store.touch(self.session)
        return {"status": "ok", "product_id": product_id, "product": _public_product(product)}

    # --- close ---

    def recommend(
        self,
        picks: list[dict[str, Any]],
        answer: str,
    ) -> dict[str, Any]:
        recommendations = []
        unknown: list[str] = []
        for pick in picks:
            pid = str(pick.get("product_id") or "")
            product = self.session.products.get(pid)
            if product is None:
                unknown.append(pid)
                continue
            recommendations.append(
                {
                    "product_id": pid,
                    "title": product.get("title") or pid,
                    "reason": str(pick.get("reason") or "")[:300],
                    "url": product.get("url"),
                    "price": product.get("price"),
                    "currency": product.get("currency"),
                }
            )
        if unknown:
            log_event(
                self.session,
                f"recommend 引用了不存在的 product_id：{unknown}",
                stage="tool.recommend",
                source="tool",
                level="warning",
                data={"unknown": unknown},
            )
        if not recommendations:
            # Never close a turn on an empty/invalid recommendation (design review focus).
            return {
                "ok": False,
                "error": "no_valid_product_ids",
                "unknown": unknown,
                "hint": "recommend only product_ids returned by query_inventory",
            }
        self.session.recommendations = recommendations
        self.session.status = "done"
        self.session.interrupt_reason = None
        store.touch(self.session)
        return {
            "ok": True,
            "answer": answer,
            "recommendations": recommendations,
        }

    def ask_user(self, question: str, reason: str = "clarify") -> dict[str, Any]:
        self.session.status = "interrupted"
        self.session.interrupt_reason = reason
        self.last_ask = question
        store.touch(self.session)
        return {"ok": True, "question": question, "reason": reason}

    def ask_clarify(self, questions: list[dict[str, Any]]) -> dict[str, Any]:
        """Interrupt with a structured questionnaire (each item: id/question/options)."""
        self.session.status = "interrupted"
        self.session.interrupt_reason = "clarify"
        self.session.pending_questions = questions
        self.last_ask = "请确认筛选条件"
        store.touch(self.session)
        return {"ok": True, "questions": questions}

    def apply_clarify(self, answers: dict[str, str]) -> dict[str, Any]:
        """Merge answered clarify questions into requirements; clear pending list."""
        req = dict(self.session.requirements or {})
        applied: dict[str, str] = {}
        for qid, raw in (answers or {}).items():
            value = str(raw or "").strip()
            if not value:
                continue
            if qid == "budget":
                if re.search(r"不限|无所谓|都可以|越贵越好", value):
                    req["budget_cny"] = None
                else:
                    parsed = _parse_budget_answer(value)
                    if parsed is not None:
                        req["budget_cny"] = parsed
            elif qid == "category":
                req["category"] = value
            elif qid == "budget_ram_conflict":
                if "放宽" in value:
                    req["budget_cny"] = None
                elif re.search(r"16", value):
                    req["must_have"] = ["16G内存"]
                # 维持硬约束 → keep both; an empty result will interrupt later.
            applied[qid] = value
        self.session.requirements = req
        self.session.pending_questions = []
        store.touch(self.session)
        log_event(
            self.session,
            "应用澄清回答",
            stage="tool.clarify",
            source="tool",
            data={"applied": applied, "requirements": req},
        )
        return {"ok": True, "applied": applied, "requirements": req}

    def _upsert_listing(self, site: str, item: dict[str, Any]) -> bool:
        source_id = str(item.get("source_product_id") or "").strip()
        if not source_id:
            return False
        key = None
        for pid, existing in self.session.products.items():
            if existing.get("source") == site and existing.get("source_product_id") == source_id:
                key = pid
                break
        if key is None:
            key = uuid.uuid4().hex[:12]
        price = item.get("price")
        currency = item.get("currency") or ("CNY" if site == "jd" else "USD")
        self.session.products[key] = {
            **self.session.products.get(key, {}),
            "product_id": key,
            "source": site,
            "source_product_id": source_id,
            "title": item.get("title") or source_id,
            "price": price,
            "currency": currency,
            "price_cny": _to_cny(price, currency),
            "url": item.get("url"),
            "shop_name": item.get("shop_name"),
            "detail_fetched": self.session.products.get(key, {}).get("detail_fetched", False),
            "attributes": self.session.products.get(key, {}).get("attributes") or {},
        }
        return True


def _inventory_summary(products: list[dict[str, Any]]) -> str:
    if not products:
        return "库存摘要：无已抓商品"
    by_site: dict[str, int] = {}
    for p in products:
        by_site[p.get("source") or "?"] = by_site.get(p.get("source") or "?", 0) + 1
    priced = [p["price_cny"] for p in products if p.get("price_cny") is not None]
    price_part = (
        f"价区¥{min(priced):.0f}-¥{max(priced):.0f}" if priced else "价区未知"
    )
    site_part = "；".join(f"{k}={v}" for k, v in sorted(by_site.items()))
    sample = "；".join((p.get("title") or "")[:30] for p in products[:3])
    return f"库存摘要：{site_part}；总计={len(products)}；{price_part}；样本：{sample}"


_RAM_MUST = re.compile(r"(\d{1,3})\s*G内存", re.I)
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"万": 10000.0, "千": 1000.0, "k": 1000.0, "K": 1000.0, "": 1.0}


def min_ram_gb(req: dict[str, Any]) -> int | None:
    """Minimum RAM (GB) demanded by must_have entries like '32G内存'."""
    return max(
        (
            int(m.group(1))
            for item in (req.get("must_have") or [])
            if (m := _RAM_MUST.search(str(item)))
        ),
        default=None,
    )


def _parse_budget_answer(value: str) -> float | None:
    """Parse a clarify answer like '3000以内' / '5000-8000' / '1.5万' / '六千' → upper bound."""
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(万|千|k)?(?:\s*[-~到至]\s*(\d+(?:\.\d+)?)\s*(万|千|k)?)?",
        value,
        re.I,
    )
    if m and m.group(1):
        def val(num: str, unit: str | None) -> float:
            return float(num) * {"万": 10000.0, "千": 1000.0, "k": 1000.0}.get(
                (unit or "").lower(), 1.0
            )

        if m.group(3):
            return val(m.group(3), m.group(4))
        return val(m.group(1), m.group(2))
    cn = re.search(r"([一二两三四五六七八九])\s*(千|万)", value)
    if cn:
        return _CN_DIGITS[cn.group(1)] * _UNIT[cn.group(2)]
    return None


# canonical category -> (regex over user text, English search term for global sites)
CATEGORIES: dict[str, tuple[str, str]] = {
    "笔记本": (r"笔记本|laptop|notebook", "laptop"),
    "手机": (r"手机|phone|smartphone", "smartphone"),
    "平板": (r"平板|tablet|ipad", "tablet"),
    "耳机": (r"耳机|headphone|earphone|earbud", "headphones"),
    "显示器": (r"显示器|monitor", "monitor"),
    "台式机": (r"台式机|desktop", "desktop computer"),
    "相机": (r"相机|camera", "camera"),
    "键盘": (r"键盘|keyboard", "keyboard"),
    "鞋": (r"跑鞋|球鞋|运动鞋|shoes?|sneakers?", "shoes"),
    "背包": (r"背包|双肩包|backpack", "backpack"),
}
def _extract_category(text: str) -> str | None:
    for name, (pattern, _en) in CATEGORIES.items():
        if re.search(pattern, text or "", re.I):
            return name
    return None


def _extract_min_ram_gb(text: str) -> int | None:
    """Only accept RAM written next to a RAM word (so '512G SSD' is never RAM)."""
    text = text or ""
    for pattern in (
        r"(?:内存|ram|运存)\s*(?:至少|不低于|不少于|>=|≥)?\s*(\d{1,3})\s*g",
        r"(?<![\d.])(\d{1,3})\s*g(?:b)?\s*(?:及以上|以上)?\s*(?:内存|ram|运存)",
    ):
        match = re.search(pattern, text, re.I)
        if match and 2 <= int(match.group(1)) <= 256:
            return int(match.group(1))
    return None


def _extract_budget(text: str) -> float | None:
    """Budget in CNY. Requires a budget cue (预算/价位/元/块/¥/万/千) so spec
    numbers like '512G SSD' or 'i5-13500H' are never read as a price."""
    text = text or ""
    num = r"(\d+(?:\.\d+)?)"

    # 1) explicit cue: 预算 5000 / 预算 1.2万 / 预算五千
    cue = re.search(rf"(?:预算|价位|价格|不超过|最多|控制在)\s*[:：]?\s*{num}\s*(万|千|k|K)?", text)
    if cue:
        return float(cue.group(1)) * _UNIT[cue.group(2) or ""]
    cn = re.search(r"(?:预算|价位|价格|不超过|最多|控制在)?\s*([一二两三四五六七八九])\s*(千|万)", text)
    if cn:
        return _CN_DIGITS[cn.group(1)] * _UNIT[cn.group(2)]

    # 2) range with a currency unit: 4000-6000元 -> upper bound
    rng = re.search(rf"{num}\s*[-~到至]\s*{num}\s*(万|千|元|块)", text)
    if rng:
        return float(rng.group(2)) * _UNIT.get(rng.group(3), 1.0)

    # 3) number + currency unit: 5000元 / 5000块 / ¥5000 / 1.2万 / 5千
    unit = re.search(rf"(?:¥|￥)\s*{num}|{num}\s*(万|千|元|块)", text)
    if unit:
        if unit.group(1):
            return float(unit.group(1))
        return float(unit.group(2)) * _UNIT.get(unit.group(3), 1.0)

    # 4) last resort: a standalone 4-6 digit number ('笔记本 5000'); never part of a
    #    model/spec token (i5-13500H, 512G, 2024款 all have a word char or '-' attached).
    bare = re.search(r"(?<![\w.\-])(\d{4,6})(?![\w.\-])", text)
    if bare and 500 <= int(bare.group(1)) <= 200000:
        return float(bare.group(1))
    return None


def _to_cny(price: Any, currency: str | None) -> float | None:
    if price is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    code = (currency or "CNY").upper()
    rates = {"CNY": 1.0, "USD": 7.2, "EUR": 7.8, "GBP": 9.1}
    rate = rates.get(code)
    if rate is None:
        return None
    return round(value * rate, 2)


def _norm(value: str) -> str:
    return "".join(str(value).lower().split())


def _product_blob(product: dict[str, Any]) -> str:
    parts = [product.get("title") or ""]
    attrs = product.get("attributes") or {}
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            parts.append(str(k))
            parts.append(str(v))
    return _norm(" ".join(parts))


def _ram_source_text(product: dict[str, Any]) -> str:
    """Keep spaces so '锐龙7 32G' is not collapsed into '锐龙732g'."""
    parts = [str(product.get("title") or "")]
    attrs = product.get("attributes") or {}
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            parts.append(str(key))
            parts.append(str(value))
    return " ".join(parts).lower()


def _ram_gb(product: dict[str, Any]) -> int | None:
    text = _ram_source_text(product)
    labeled = re.search(r"(?:内存|ram|运行内存)\s*(\d{1,3})\s*g", text)
    if labeled:
        value = int(labeled.group(1))
        if 2 <= value <= 128:
            return value
    for size in (128, 64, 32, 16, 8, 4):
        if re.search(rf"(?<![\d.]){size}\s*g(?:b)?(?![\d.])", text):
            return size
    return None


def _public_product(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": product["product_id"],
        "title": product.get("title"),
        "price": product.get("price"),
        "currency": product.get("currency"),
        "price_cny": product.get("price_cny"),
        "url": product.get("url"),
        "detail_fetched": product.get("detail_fetched"),
        "attributes": product.get("attributes") or {},
    }
