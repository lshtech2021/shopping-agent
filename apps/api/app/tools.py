"""Nine shopping tools: memory + browse (+ mock) + recommend/ask_user."""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from app.browser.gateway import BLOCKING, SITE_RISK, BrowserGateway
from app.browser.gateway import throttle as _throttle
from app.constraints import check_product
from app.domains.shopping.requirements import (
    CATEGORIES,
    BudgetParse,
    _BUDGET_CHANGE,
    _BUDGET_UNLIMITED,
    _CATEGORY_UPDATE,
    _QUESTION_MENTION,
    _RAM_CHANGE,
    _RAM_MUST,
    _SPEC_NUMBER,
    _UNLIMITED_ANSWER,
    _extract_budget,
    _extract_category,
    _extract_min_ram_gb,
    _is_no_preference,
    parse_budget,
)
from app.inventory import (
    build_product_index,
    inventory_summary,
    select_diverse_products,
    to_cny,
    upsert_listing,
)
from app.store import Session, store
from app.trace import log_event

QUOTA_LIMITS = {"search": 2, "next_page": 2, "fetch_detail": 5}


class ToolBox:
    def __init__(self, session: Session):
        self.session = session
        self.active_site: Optional[str] = None
        self.active_query: Optional[str] = None
        self.page_by_site: dict[str, int] = {}
        self.last_ask: Optional[str] = None
        self._product_index: dict[tuple[str, str], str] = {}
        self._browser = BrowserGateway(session)
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
        return self._browser.mock_mode

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
        return await self._browser.execute(tool, args)

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
            "inventory_summary": inventory_summary(products),
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

    async def dispatch_browse(
        self, tool: str, args: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Public browse entry used by adapters; applies quotas/site guards."""
        payload = dict(args or {})
        name = str(tool or "").strip()
        if name == "search":
            filters = {
                key: value
                for key, value in payload.items()
                if key not in {"site", "query"}
            }
            return await self.search(
                str(payload.get("site") or ""),
                str(payload.get("query") or ""),
                **filters,
            )
        if name == "scrape_listing":
            page = payload.get("page")
            return await self.scrape_listing(
                payload.get("site"),
                int(page) if page is not None else None,
            )
        if name == "next_page":
            return await self.next_page(payload.get("site"))
        if name == "fetch_detail":
            product_id = str(
                payload.get("product_id")
                or payload.get("source_product_id")
                or ""
            )
            return await self.fetch_detail(product_id)
        return {
            "status": "parse_error",
            "error": f"unknown_browse_tool:{name}",
        }

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
            product["price_cny"] = to_cny(product["price"], product.get("currency"))
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
        self._product_index = build_product_index(self.session.products)

    def _upsert_listing(self, site: str, item: dict[str, Any]) -> bool:
        return upsert_listing(
            self.session.products,
            self._product_index,
            site=site,
            item=item,
        )


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
