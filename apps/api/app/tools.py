"""Nine shopping tools: memory + browse (+ mock) + recommend/ask_user."""
from __future__ import annotations

import asyncio
import random
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from app.config import settings
from app import mock_browser
from app.constraints import check_product
from app.inventory import select_diverse_products
from app.store import Session, store
from app.trace import log_event
from app.ws_hub import hub

QUOTA_LIMITS = {"search": 2, "next_page": 2, "fetch_detail": 5}
BLOCKING = {"rate_limited", "captcha", "login_required", "blocked"}
# Site-level risk states: once seen, the site stays closed until the user resumes.
SITE_RISK = {"rate_limited", "captcha", "login_required"}
_BUDGET_CHANGE = re.compile(
    r"(?:预算|价格|价位)[^。；;]{0,10}?"
    r"(?:改成|改为|调整|改到|降到|提高到|不要超过|不超过|最多|控制在|是|为)?"
    r"\s*[:：]?\s*\d"
    r"|(?:改成|改为|调整|改到|降到|提高到)[^。；;]{0,8}?(?:预算|价格|价位)"
    r"|\d[^。；;]{0,4}(?:以内|以下|封顶)"
)
_BUDGET_UNLIMITED = re.compile(
    r"(?:预算|价格|价位)[^。；;]{0,8}(?:不限|取消|不用管|无所谓|随意|越贵越好)"
    r"|(?:不限|取消|不用管|越贵越好)[^。；;]{0,6}(?:预算|价格|价位)"
    r"|^越贵越好$"
)
_UNLIMITED_ANSWER = re.compile(
    r"不限|无所谓|都可以|越贵越好|取消预算|不用管预算|随意"
)
_CATEGORY_UPDATE = re.compile(r"改成|改为|换成|只看|只要|品类|类别|换购")
_RAM_CHANGE = re.compile(
    r"(?:内存|ram|运存)[^。；;]{0,10}?(?:改成|改为|调整|改到|提到|提高到|升到|降到|至少|不低于)"
    r"|(?:改成|改为|调整|改到|提到|提高到|升到|降到)[^。；;]{0,10}?(?:内存|ram|运存)",
    re.I,
)
_QUESTION_MENTION = re.compile(r"怎么样|如何|吗|哪款|哪个|这个|这款|够用|值不值")
_SPEC_NUMBER = re.compile(
    r"(?:rtx|gtx|i[3579][- ]?|锐龙|ryzen|骁龙|snapdragon)\s*\d"
    r"|\d[\d.,]*\s*(?:g|gb|t|tb|mah|像素|万像素|个|件|台|年|款|代|核|hz|k|寸|英寸|mm|cm|w|v)\b",
    re.I,
)

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
        self.page_by_site: dict[str, int] = {}
        self.last_ask: Optional[str] = None
        self._product_index: dict[tuple[str, str], str] = {}
        self._reindex_products()

    def _require_site(self, site: str) -> Optional[dict[str, Any]]:
        if site and site in self.session.sites:
            return None
        refused = {
            "status": "blocked",
            "error": "site_not_selected",
            "site": site,
        }
        log_event(
            self.session,
            f"拒绝 browse：站点 {site or '(空)'} 不在当前会话选择中",
            stage="tool.guard",
            source="tool",
            level="warning",
            data=refused,
        )
        return refused

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
        parsed_budget = parse_budget(text)
        needs_clarify = False
        parse_notes: list[str] = []
        budget_is_new = (
            req.get("budget_cny") is None
            and req.get("budget_min_cny") is None
            and not req.get("budget_unlimited")
        )
        if _BUDGET_UNLIMITED.search(text):
            req["budget_cny"] = None
            req["budget_min_cny"] = None
            req["budget_unlimited"] = True
        elif parsed_budget.confidence == "high" and (
            parsed_budget.max_cny is not None
            or parsed_budget.min_cny is not None
        ):
            explicit_change = bool(_BUDGET_CHANGE.search(text))
            asked_about_price = bool(_QUESTION_MENTION.search(text))
            if explicit_change or (budget_is_new and not asked_about_price):
                req["budget_cny"] = parsed_budget.max_cny
                req["budget_min_cny"] = parsed_budget.min_cny
                req["budget_unlimited"] = False
        elif (
            parsed_budget.confidence == "low"
            and re.search(r"\d", text)
            and not _SPEC_NUMBER.search(text)
        ):
            needs_clarify = True
            parse_notes.append(
                "ambiguous_budget: bare/low-confidence number not written; call clarify"
            )
        # Only record what the user said; never infer category/use case on our own.
        category = _extract_category(text)
        if category and (
            req.get("category") is None or _CATEGORY_UPDATE.search(text)
        ):
            req["category"] = category
        ram = _extract_min_ram_gb(text)
        has_ram = any(
            _RAM_MUST.search(str(item))
            for item in (req.get("must_have") or [])
        )
        if ram is not None and (not has_ram or _RAM_CHANGE.search(text)):
            must = [
                m
                for m in (req.get("must_have") or [])
                if not _RAM_MUST.search(str(m))
            ]
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
        return {
            "ok": True,
            "requirements": req,
            "needs_clarify": needs_clarify,
            "parse_notes": parse_notes,
        }

    def query_inventory(
        self,
        *,
        limit: int = 20,
        prefer_detail: bool = False,
    ) -> dict[str, Any]:
        products = list(self.session.products.values())
        requirements = self.session.requirements or {}
        eligible = [
            product
            for product in products
            if check_product(
                product,
                requirements,
                sites=self.session.sites,
                require_detail=prefer_detail,
            ).ok
        ]
        samples = select_diverse_products(
            eligible,
            requirements,
            sites=self.session.sites,
            limit=limit,
            require_detail=prefer_detail,
        )
        return {
            "sort": "diverse",
            "count": len(eligible),
            "total_cached": len(products),
            "items": [
                {
                    "product_id": p["product_id"],
                    "source": p["source"],
                    "title": p["title"],
                    "price": p.get("price"),
                    "currency": p.get("currency"),
                    "price_cny": p.get("price_cny"),
                    "price_max_cny": p.get("price_max_cny"),
                    "price_is_range": p.get("price_is_range", False),
                    "shop_name": p.get("shop_name"),
                    "sales": p.get("sales"),
                    "rating": p.get("rating"),
                    "shipping": p.get("shipping"),
                    "condition": p.get("condition"),
                    "sponsored": p.get("sponsored", False),
                    "category": p.get("category"),
                    "attributes": p.get("attributes") or {},
                    "detail_fetched": p.get("detail_fetched", False),
                    "url": p.get("url"),
                }
                for p in samples
            ],
        }

    # --- browse ---

    async def search(
        self, site: str, query: str, **filters: Any
    ) -> dict[str, Any]:
        site = site.lower()
        refused = self._require_site(site)
        if refused:
            return refused
        blocked = self._check_quota(site, "search")
        if blocked:
            return blocked
        args = {"site": site, "query": query, **filters}
        result = await self._browse("search", args)
        self._bump_quota(site, "search")
        if result.get("status") == "ok":
            self.active_site = site
            self.active_query = query
            self.page_by_site[site] = int(result.get("page") or 1)
        return result

    async def scrape_listing(self, site: str | None = None, page: int | None = None) -> dict[str, Any]:
        site = (site or self.active_site or (self.session.sites[0] if self.session.sites else "")).lower()
        refused = self._require_site(site) or self._guard(site, "scrape_listing")
        if refused:
            return refused
        page = int(page or self.page_by_site.get(site) or 1)
        result = await self._browse("scrape_listing", {"site": site, "page": page})
        if result.get("status") != "ok":
            return result
        accepted = 0
        for item in result.get("items") or []:
            if self._upsert_listing(site, item):
                accepted += 1
        self.page_by_site[site] = int(result.get("page") or page)
        if site and site not in self.session.searched_sites:
            self.session.searched_sites.append(site)
        store.touch(self.session)
        return {
            **result,
            "accepted": accepted,
            "product_count": len(self.session.products),
        }

    async def next_page(self, site: str | None = None) -> dict[str, Any]:
        site = (
            site
            or self.active_site
            or (self.session.sites[0] if self.session.sites else "")
        ).lower()
        refused = self._require_site(site)
        if refused:
            return refused
        blocked = self._check_quota(site, "next_page")
        if blocked:
            return blocked
        current_page = int(self.page_by_site.get(site) or 1)
        next_page = current_page + 1
        result = await self._browse(
            "next_page", {"site": site, "page": next_page}
        )
        self._bump_quota(site, "next_page")
        if result.get("status") != "ok":
            return result
        self.page_by_site[site] = int(result.get("page") or next_page)
        accepted = 0
        for item in result.get("items") or []:
            if self._upsert_listing(site, item):
                accepted += 1
        store.touch(self.session)
        return {**result, "accepted": accepted}

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
            product["price_max_cny"] = None
            product["price_is_range"] = False
        if result.get("attributes"):
            product["attributes"] = result["attributes"]
        store.touch(self.session)
        return {"status": "ok", "product_id": product_id, "product": _public_product(product)}

    # --- close ---

    def recommend(
        self,
        picks: list[dict[str, Any]],
        answer: str,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        requirements = self.session.requirements or {}
        recommendations = []
        unknown: list[str] = []
        violations: list[str] = []
        details: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pick in picks:
            pid = str(pick.get("product_id") or "")
            if pid in seen:
                continue
            seen.add(pid)
            product = self.session.products.get(pid)
            if product is None:
                unknown.append(pid)
                continue
            eligibility = check_product(
                product,
                requirements,
                sites=self.session.sites,
                # Mock catalog has no detail pages; live mode must verify details.
                require_detail=not self.mock_mode,
            )
            if not eligibility.ok:
                violations.extend(eligibility.violations)
                details.append(
                    {"product_id": pid, "codes": eligibility.violations}
                )
                continue
            recommendations.append(
                {
                    "product_id": pid,
                    "title": product.get("title") or pid,
                    "reason": str(pick.get("reason") or "")[:300],
                    "url": _safe_product_url(product.get("url")),
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
        if violations:
            log_event(
                self.session,
                "recommend 引用了不满足硬约束的商品",
                stage="tool.recommend",
                source="tool",
                level="warning",
                data={"details": details},
            )
            return {
                "ok": False,
                "error": "ineligible_product",
                "violations": list(dict.fromkeys(violations)),
                "details": details,
                "hint": "只能推荐当前预算、站点、must_have 和详情验证都满足的商品",
            }
        if not recommendations:
            # Never close a turn on an empty/invalid recommendation (design review focus).
            return {
                "ok": False,
                "error": "no_valid_product_ids",
                "unknown": unknown,
                "hint": "recommend only product_ids returned by query_inventory",
            }
        proposal = {
            "ok": True,
            "answer": str(answer or "")[:2000],
            "recommendations": recommendations,
        }
        if not commit:
            return proposal
        self.session.recommendations = recommendations
        self.session.status = "done"
        self.session.interrupt_reason = None
        store.touch(self.session)
        return proposal

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
        """Merge answered clarify questions into requirements by semantic field."""
        req = dict(self.session.requirements or {})
        field_by_id = {
            str(question.get("id") or ""): str(
                question.get("field") or "other"
            )
            for question in (self.session.pending_questions or [])
            if isinstance(question, dict)
        }
        known_fields = {
            "budget",
            "category",
            "use_case",
            "must_have",
            "preferences",
        }
        applied: dict[str, str] = {}
        needs_clarify = False
        for qid, raw in (answers or {}).items():
            value = str(raw or "").strip()
            if not value:
                continue
            qid = str(qid)
            if qid == "budget_ram_conflict":
                if "放宽" in value:
                    req["budget_cny"] = None
                    req["budget_min_cny"] = None
                    req["budget_unlimited"] = True
                elif re.search(r"16", value):
                    self._merge_must_have_answer(req, "16G")
                applied[qid] = value
                continue
            field = field_by_id.get(qid)
            if not field or field == "other":
                field = qid if qid in known_fields else "other"
            if field == "budget":
                if _UNLIMITED_ANSWER.search(value):
                    req["budget_cny"] = None
                    req["budget_min_cny"] = None
                    req["budget_unlimited"] = True
                else:
                    parsed = parse_budget(value)
                    if parsed.confidence == "high" and (
                        parsed.max_cny is not None
                        or parsed.min_cny is not None
                    ):
                        req["budget_cny"] = parsed.max_cny
                        req["budget_min_cny"] = parsed.min_cny
                        req["budget_unlimited"] = False
                    else:
                        bare = re.fullmatch(
                            r"\s*(\d+(?:\.\d+)?)\s*(?:元|块)?\s*", value
                        )
                        if bare:
                            req["budget_cny"] = float(bare.group(1))
                            req["budget_min_cny"] = None
                            req["budget_unlimited"] = False
                        else:
                            needs_clarify = True
            elif field == "category":
                if not _is_no_preference(value):
                    req["category"] = value
            elif field == "use_case":
                if not _is_no_preference(value):
                    req["use_case"] = value
            elif field == "must_have":
                if not _is_no_preference(value):
                    self._merge_must_have_answer(req, value)
            elif field == "preferences":
                if not _is_no_preference(value):
                    preferences = list(req.get("preferences") or [])
                    if value not in preferences:
                        preferences.append(value)
                    req["preferences"] = preferences
            else:
                clarifications = dict(req.get("clarifications") or {})
                clarifications[str(qid)] = value
                req["clarifications"] = clarifications
            applied[str(qid)] = value
        self.session.requirements = req
        self.session.pending_questions = []
        store.touch(self.session)
        log_event(
            self.session,
            "应用澄清回答",
            stage="tool.clarify",
            source="tool",
            data={
                "applied": applied,
                "requirements": req,
                "needs_clarify": needs_clarify,
            },
        )
        return {
            "ok": True,
            "applied": applied,
            "requirements": req,
            "needs_clarify": needs_clarify,
        }

    @staticmethod
    def _merge_must_have_answer(req: dict[str, Any], value: str) -> None:
        must = list(req.get("must_have") or [])
        ram_match = re.fullmatch(
            r"\s*(\d{1,3})\s*g(?:b)?\s*", value, re.I
        ) or re.search(
            r"(?:内存|ram|运存)\s*(\d{1,3})\s*g(?:b)?", value, re.I
        )
        if ram_match:
            ram = int(ram_match.group(1))
            must = [m for m in must if not _RAM_MUST.search(str(m))]
            normalized = f"{ram}G内存"
        else:
            normalized = value
        if normalized not in must:
            must.append(normalized)
        req["must_have"] = must

    def _reindex_products(self) -> None:
        index: dict[tuple[str, str], str] = {}
        for product_id, product in self.session.products.items():
            source = str(product.get("source") or "")
            source_id = str(product.get("source_product_id") or "").strip()
            if source and source_id:
                index[(source, source_id)] = str(product_id)
        self._product_index = index

    def _upsert_listing(self, site: str, item: dict[str, Any]) -> bool:
        source_id = str(item.get("source_product_id") or "").strip()
        if not source_id:
            return False
        key = self._product_index.get((site, source_id))
        if key is None or key not in self.session.products:
            key = uuid.uuid4().hex[:12]
        price = item.get("price")
        currency = item.get("currency") or ("CNY" if site == "jd" else "USD")
        existing = self.session.products.get(key, {})
        detail_owned = bool(existing.get("detail_fetched"))
        price_is_range = bool(item.get("price_is_range"))
        price_max = item.get("price_max") if price_is_range else None
        listing_fields = {
            "title": item.get("title") or source_id,
            "price": price,
            "currency": currency,
            "price_cny": _to_cny(price, currency),
            "price_max_cny": _to_cny(price_max, currency),
            "price_is_range": price_is_range,
        }
        if detail_owned:
            listing_fields = {
                "title": existing.get("title") or source_id,
                "price": existing.get("price"),
                "currency": existing.get("currency") or currency,
                "price_cny": existing.get("price_cny"),
                "price_max_cny": existing.get("price_max_cny"),
                "price_is_range": existing.get("price_is_range", False),
            }
        self.session.products[key] = {
            **existing,
            "product_id": key,
            "source": site,
            "source_product_id": source_id,
            "url": item.get("url") or existing.get("url"),
            "shop_name": item.get("shop_name") or existing.get("shop_name"),
            "sales": item.get("sales", existing.get("sales")),
            "rating": item.get("rating", existing.get("rating")),
            "sponsored": item.get("sponsored", existing.get("sponsored", False)),
            "shipping": item.get("shipping", existing.get("shipping")),
            "condition": item.get("condition", existing.get("condition")),
            "badges": item.get("badges", existing.get("badges") or []),
            "category": item.get("category", existing.get("category")),
            "detail_fetched": existing.get("detail_fetched", False),
            "attributes": existing.get("attributes") or {},
            **listing_fields,
        }
        self._product_index[(site, source_id)] = key
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
    return (
        f"库存摘要：商品数={len(products)}；总计={len(products)}；"
        f"{site_part}；{price_part}"
    )


_RAM_MUST = re.compile(r"(\d{1,3})\s*G内存", re.I)
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"万": 10000.0, "千": 1000.0, "k": 1000.0, "K": 1000.0, "": 1.0}


@dataclass(frozen=True)
class BudgetParse:
    max_cny: float | None
    min_cny: float | None
    confidence: Literal["high", "low"]


_NON_BUDGET_TAIL = re.compile(
    r"^\s*(?:万)?(?:像素|mAh|mah|个|件|台|年|款|代|g(?:b)?(?![\d.]))",
    re.I,
)
_NUM = r"(\d+(?:\.\d+)?)"


def _has_non_budget_tail(value: str, end: int) -> bool:
    return bool(_NON_BUDGET_TAIL.match(value[end:]))


def parse_budget(text: str) -> BudgetParse:
    """Parse a CNY budget without treating specs, counters, or model numbers as money."""
    value = str(text or "").strip()
    if not value:
        return BudgetParse(None, None, "low")
    if _BUDGET_UNLIMITED.search(value):
        return BudgetParse(None, None, "high")

    def amount(number: str, unit: str | None) -> float:
        return float(number) * _UNIT.get((unit or "").lower(), 1.0)

    cue = re.search(r"预算|价格|价位|不超过|最多|控制在", value)

    # Open-ended minimum first: a currency word may sit before the marker.
    minimum = re.search(
        rf"(?:至少|不低于)\s*(?:预算)?\s*[:：]?\s*{_NUM}\s*(万|千|k)?\s*(?:元|块)?",
        value,
        re.I,
    )
    if minimum and not _has_non_budget_tail(value, minimum.end()):
        return BudgetParse(
            None,
            amount(minimum.group(1), minimum.group(2)),
            "high",
        )
    minimum_suffix = re.search(
        rf"{_NUM}\s*(万|千|k)?\s*(?:元|块)?\s*(?:以上|起|≥)",
        value,
        re.I,
    )
    if minimum_suffix and not _has_non_budget_tail(
        value, minimum_suffix.end()
    ):
        return BudgetParse(
            None,
            amount(minimum_suffix.group(1), minimum_suffix.group(2)),
            "high",
        )

    # Range: "预算3000-5000" and "预算五千到八千" use the upper bound.
    range_match = re.search(
        rf"{_NUM}\s*(万|千|k)?\s*[-~到至]\s*{_NUM}\s*(万|千|k)?",
        value,
        re.I,
    )
    if range_match:
        lower = amount(range_match.group(1), range_match.group(2))
        upper = amount(range_match.group(3), range_match.group(4))
        year_like = 1900 <= lower <= 2100 and 1900 <= upper <= 2100
        if (
            (cue or re.search(r"元|块|¥|￥|万|千|k", value, re.I))
            or (lower >= 500 and upper >= lower and not year_like)
        ) and not _has_non_budget_tail(value, range_match.end()):
            return BudgetParse(upper, None, "high")
    cn_range = re.search(
        r"([一二两三四五六七八九])\s*(千|万)\s*[-~到至]\s*"
        r"([一二两三四五六七八九])\s*(千|万)",
        value,
    )
    if cn_range and not _has_non_budget_tail(value, cn_range.end()):
        lower = _CN_DIGITS[cn_range.group(1)] * _UNIT[cn_range.group(2)]
        upper = _CN_DIGITS[cn_range.group(3)] * _UNIT[cn_range.group(4)]
        return BudgetParse(upper, None, "high")

    # "1万5" means 15000, not 10000.
    compact_wan = re.search(rf"{_NUM}\s*万\s*(\d)(?!\d)", value)
    if compact_wan and not _has_non_budget_tail(value, compact_wan.end()):
        return BudgetParse(
            float(compact_wan.group(1)) * 10000
            + int(compact_wan.group(2)) * 1000,
            None,
            "high",
        )

    # Budget cue before the number, including "价格不要超过8000".
    cue_match = re.search(
        rf"(?:预算|价格|价位)[^。；;]{{0,8}}?"
        rf"(?:不要超过|不超过|最多|控制在|改成|改为|是|为)?"
        rf"\s*[:：]?\s*{_NUM}\s*(万|千|k)?",
        value,
        re.I,
    )
    if cue_match and not _has_non_budget_tail(value, cue_match.end()):
        return BudgetParse(
            amount(cue_match.group(1), cue_match.group(2)),
            None,
            "high",
        )

    cap_before = re.search(
        rf"(?:不要超过|不超过|最多|控制在|封顶)\s*[:：]?\s*"
        rf"{_NUM}\s*(万|千|k)?",
        value,
        re.I,
    )
    if cap_before and not _has_non_budget_tail(value, cap_before.end()):
        return BudgetParse(
            amount(cap_before.group(1), cap_before.group(2)),
            None,
            "high",
        )

    cap_suffix = re.search(
        rf"{_NUM}\s*(万|千|k)?\s*(?:元|块)?\s*(?:以内|以下|封顶|内(?!存))",
        value,
        re.I,
    )
    if cap_suffix and not _has_non_budget_tail(value, cap_suffix.end()):
        return BudgetParse(
            amount(cap_suffix.group(1), cap_suffix.group(2)),
            None,
            "high",
        )

    cn = re.search(r"([一二两三四五六七八九])\s*(千|万)", value)
    if cn and not _has_non_budget_tail(value, cn.end()):
        return BudgetParse(
            _CN_DIGITS[cn.group(1)] * _UNIT[cn.group(2)],
            None,
            "high",
        )

    symbol = re.search(rf"(?:¥|￥)\s*{_NUM}", value)
    if symbol and not _has_non_budget_tail(value, symbol.end()):
        return BudgetParse(float(symbol.group(1)), None, "high")

    unit = re.search(rf"{_NUM}\s*(元|块|万|千)", value)
    if unit and not _has_non_budget_tail(value, unit.end()):
        return BudgetParse(amount(unit.group(1), unit.group(2)), None, "high")

    if cue:
        unit_k = re.search(rf"{_NUM}\s*k", value, re.I)
        if unit_k and not _has_non_budget_tail(value, unit_k.end()):
            return BudgetParse(float(unit_k.group(1)) * 1000, None, "high")

    return BudgetParse(None, None, "low")


def _extract_budget(text: str) -> float | None:
    return parse_budget(text).max_cny


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


def _is_no_preference(value: str) -> bool:
    return bool(
        re.search(
            r"无偏好|不限|无所谓|都可以|随意|no\s*preference", value or "", re.I
        )
    )


def _extract_min_ram_gb(text: str) -> int | None:
    """Only accept RAM written next to a RAM word (so '512G SSD' is never RAM)."""
    text = text or ""
    for pattern in (
        r"(?:内存|ram|运存)\s*(?:改成|改为|调整到?|提到|提高到|升到|降到|至少|不低于|不少于|>=|≥)?\s*(\d{1,3})\s*g",
        r"(?<![\d.])(\d{1,3})\s*g(?:b)?\s*(?:及以上|以上)?\s*(?:内存|ram|运存)",
    ):
        match = re.search(pattern, text, re.I)
        if match and 2 <= int(match.group(1)) <= 256:
            return int(match.group(1))
    return None


def _safe_product_url(url: Any) -> str | None:
    if not url:
        return None
    text = str(url)
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").lower()
    if host == "jd.com" or host.endswith(".jd.com"):
        return text
    if host == "ebay.com" or host.endswith(".ebay.com"):
        return text
    return None


def _to_cny(price: Any, currency: str | None) -> float | None:
    if price is None:
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    code = (currency or "CNY").upper()
    rates = {
        str(key).upper(): float(rate)
        for key, rate in (settings.fx_rates or {}).items()
    }
    rate = rates.get(code)
    if rate is None or rate <= 0:
        return None
    return round(value * rate, 2)


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
