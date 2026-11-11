"""Nine shopping tools: memory + browse (+ mock) + recommend/ask_user."""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from app.browser.gateway import BLOCKING, SITE_RISK, BrowserGateway
from app.browser.gateway import throttle as _throttle
from app.constraints import check_product
from app.currency import is_supported_currency, normalize_currency
from app.fit_judge import judge_products
from app.domains.shopping.requirements_patch import (
    RequirementPatch,
    apply_requirement_patch,
)
from app.inventory import (
    build_product_index,
    inventory_summary,
    select_diverse_products,
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
        self.search_url_by_site: dict[str, str] = {}
        self.search_seq = 0
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
            "inventory_summary": inventory_summary(
                products, self.session.settings.get("currency")
            ),
            "recommendations": self.session.recommendations,
            "interrupt_reason": self.session.interrupt_reason,
            "quotas": self.session.quotas,
        }

    def update_requirements(
        self,
        patch: RequirementPatch | dict[str, Any] | None = None,
        **explicit_fields: Any,
    ) -> dict[str, Any]:
        """Apply structured requirement fields the model extracted itself."""
        req = dict(self.session.requirements or {})
        requirement_patch = (
            patch
            if isinstance(patch, RequirementPatch)
            else RequirementPatch.model_validate(patch or {})
        )

        if explicit_fields:
            explicit = RequirementPatch.model_validate(explicit_fields)
            merged = requirement_patch.model_dump()
            for field in explicit.model_fields_set:
                merged[field] = getattr(explicit, field)
            merged["ambiguous_fields"] = [
                field
                for field in requirement_patch.ambiguous_fields
                if field not in explicit.model_fields_set
            ]
            confidence = dict(requirement_patch.confidence)
            confidence.update(
                {
                    field: "high"
                    for field in explicit.model_fields_set
                    if field not in {"ambiguous_fields", "confidence", "source_spans"}
                }
            )
            merged["confidence"] = confidence
            requirement_patch = RequirementPatch.model_validate(merged)

        low_confidence = {
            field
            for field, confidence in requirement_patch.confidence.items()
            if confidence == "low"
        }
        if low_confidence:
            requirement_patch = requirement_patch.model_copy(
                update={
                    "ambiguous_fields": list(
                        dict.fromkeys(
                            [*requirement_patch.ambiguous_fields, *low_confidence]
                        )
                    )
                }
            )

        ambiguous = set(requirement_patch.ambiguous_fields)
        if {"must_have", "preferences"} & ambiguous:
            requirement_patch = requirement_patch.model_copy(
                update={
                    "must_have": (
                        []
                        if "must_have" in ambiguous
                        else requirement_patch.must_have
                    ),
                    "preferences": (
                        []
                        if "preferences" in ambiguous
                        else requirement_patch.preferences
                    ),
                }
            )

        budget_notes: list[str] = []
        budget_clarify: dict[str, Any] | None = None
        requested_budget = (
            requirement_patch.budget
            if "budget" in requirement_patch.model_fields_set
            else None
        )
        requested_min = (
            requirement_patch.budget_min
            if "budget_min" in requirement_patch.model_fields_set
            else None
        )
        requested_currency = normalize_currency(
            requirement_patch.budget_currency
        )
        session_currency = normalize_currency(
            self.session.settings.get("currency")
        )
        if requested_budget is not None or requested_min is not None:
            currency = (
                requested_currency
                or session_currency
                or normalize_currency(req.get("budget_currency"))
            )
            if not currency:
                budget_notes.append(
                    "budget_currency_required: budget not written"
                )
                budget_clarify = {
                    "field": "budget",
                    "question": "预算金额使用哪种货币？",
                    "options": ["CNY", "USD", "EUR", "GBP", "JPY"],
                }
                requirement_patch = requirement_patch.model_copy(
                    update={
                        "budget": None,
                        "budget_min": None,
                        "budget_currency": None,
                    }
                )
            elif not is_supported_currency(currency):
                budget_notes.append(
                    f"budget_currency_unsupported: {currency}"
                )
                budget_clarify = {
                    "field": "budget",
                    "question": f"暂不支持 {currency} 预算，请选择支持的货币。",
                    "options": ["CNY", "USD", "EUR", "GBP", "JPY"],
                }
                requirement_patch = requirement_patch.model_copy(
                    update={
                        "budget": None,
                        "budget_min": None,
                        "budget_currency": None,
                    }
                )
            elif session_currency and currency != session_currency:
                budget_notes.append(
                    "budget_currency_mismatch: "
                    f"budget={currency}, session={session_currency}"
                )
                budget_clarify = {
                    "field": "budget",
                    "question": (
                        f"当前会话货币是 {session_currency}，但预算是 "
                        f"{currency}。请改用 {session_currency} 预算，"
                        "或先在面板切换页面货币。"
                    ),
                    "options": [
                        f"改用 {session_currency} 预算",
                        "切换页面货币",
                    ],
                }
                requirement_patch = requirement_patch.model_copy(
                    update={
                        "budget": None,
                        "budget_min": None,
                        "budget_currency": None,
                    }
                )
            else:
                requirement_patch = requirement_patch.model_copy(
                    update={"budget_currency": currency}
                )
                if not session_currency:
                    self.session.settings["currency"] = currency
                    self.session.settings["currency_source"] = "manual"

        req = apply_requirement_patch(req, requirement_patch)
        if (
            req.get("budget") is not None
            or req.get("budget_min") is not None
        ) and not req.get("budget_currency"):
            req["budget_currency"] = normalize_currency(
                self.session.settings.get("currency")
            )
        parse_notes = [
            f"ambiguous_{field}: not written; call ask_user"
            for field in requirement_patch.ambiguous_fields
        ]
        parse_notes.extend(budget_notes)
        self.session.requirements = req
        store.touch(self.session)
        return {
            "ok": True,
            "requirements": req,
            "needs_clarify": bool(requirement_patch.ambiguous_fields)
            or budget_clarify is not None,
            "parse_notes": parse_notes,
            "clarify_question": budget_clarify,
        }

    async def ensure_fit(
        self,
        product_ids: list[str] | None = None,
        *,
        limit: int | None = None,
    ) -> Optional[str]:
        """Have the LLM judge category/must-have fit for products lacking a
        current verdict. Returns an error string when judging failed."""
        requirements = self.session.requirements or {}
        if product_ids is not None:
            products = [
                self.session.products[pid]
                for pid in product_ids
                if pid in self.session.products
            ]
        else:
            products = list(self.session.products.values())
            if limit is not None and limit > 0:
                products = products[:limit]
        outcome = await judge_products(products, requirements)
        if outcome["judged"]:
            store.touch(self.session)
        if outcome["error"]:
            log_event(
                self.session,
                f"商品匹配评估失败：{outcome['error']}",
                stage="tool.fit_judge",
                source="tool",
                level="warning",
                data=outcome,
            )
        return outcome["error"]

    def query_inventory(
        self,
        *,
        limit: int = 20,
        prefer_detail: bool = False,
    ) -> dict[str, Any]:
        products = list(self.session.products.values())
        requirements = self.session.requirements or {}
        eligible: list[dict[str, Any]] = []
        excluded: dict[str, int] = {}
        excluded_samples: list[dict[str, Any]] = []
        for product in products:
            eligibility = check_product(
                product,
                requirements,
                settings=self.session.settings,
                sites=self.session.sites,
                require_detail=prefer_detail,
            )
            if eligibility.ok:
                eligible.append(product)
                continue
            for violation in eligibility.violations:
                excluded[violation] = excluded.get(violation, 0) + 1
            if len(excluded_samples) < 3:
                excluded_samples.append(
                    {
                        "product_id": product.get("product_id"),
                        "title": product.get("title"),
                        "violations": eligibility.violations[:5],
                    }
                )
        samples = select_diverse_products(
            eligible,
            requirements,
            settings=self.session.settings,
            sites=self.session.sites,
            limit=limit,
            require_detail=prefer_detail,
        )
        return {
            "sort": "diverse",
            "count": len(eligible),
            "total_cached": len(products),
            "excluded": excluded,
            "excluded_samples": excluded_samples,
            "items": [
                {
                    "product_id": p["product_id"],
                    "source": p["source"],
                    "title": p["title"],
                    "price": p.get("price"),
                    "price_max": p.get("price_max"),
                    "currency": p.get("currency"),
                    "price_is_range": p.get("price_is_range", False),
                    "shop_name": p.get("shop_name"),
                    "sales": p.get("sales"),
                    "rating": p.get("rating"),
                    "shipping": p.get("shipping"),
                    "shipping_cost": p.get("shipping_cost"),
                    "landed_cost": p.get("landed_cost"),
                    "cost_complete": p.get("cost_complete"),
                    "condition": p.get("condition"),
                    "sponsored": p.get("sponsored", False),
                    "category": p.get("category"),
                    "image_url": p.get("image_url"),
                    "listing_type": p.get("listing_type"),
                    "availability": p.get("availability"),
                    "location": p.get("location"),
                    "seller": p.get("seller"),
                    "watchers": p.get("watchers"),
                    "sold": p.get("sold"),
                    "has_variations": p.get("has_variations", False),
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
            url = result.get("document_url") or result.get("url")
            if url:
                self.search_url_by_site[site] = str(url)
            self.search_seq += 1
        return result

    def _search_context(self, site: str) -> dict[str, Any]:
        """Identity of the active search page for action idempotency."""
        return {
            "query": self.active_query if self.active_site == site else None,
            "search_url": self.search_url_by_site.get(site),
            "search_seq": self.search_seq,
        }

    async def scrape_listing(self, site: str | None = None, page: int | None = None) -> dict[str, Any]:
        site = (site or self.active_site or (self.session.sites[0] if self.session.sites else "")).lower()
        refused = self._require_site(site) or self._guard(site, "scrape_listing")
        if refused:
            return refused
        page = int(page or self.page_by_site.get(site) or 1)
        result = await self._browse(
            "scrape_listing",
            {"site": site, "page": page, **self._search_context(site)},
        )
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
            "next_page",
            {"site": site, "page": next_page, **self._search_context(site)},
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
        # Charge quota only for successful detail opens. parse_error / blocks
        # still cost wall time, but must not burn the 5-slot budget that later
        # blocks recommend when the extension races or JD DOM is slow.
        if result.get("status") == "ok":
            self._bump_quota(site, "fetch_detail")
        if result.get("status") != "ok":
            return result
        product["detail_fetched"] = True
        product["detail"] = result
        primary_price = result.get("price")
        primary_currency = normalize_currency(result.get("currency"))
        approx_price = result.get("approx_price")
        approx_currency = normalize_currency(result.get("approx_currency"))
        product_currency = normalize_currency(product.get("currency"))
        session_currency = normalize_currency(
            self.session.settings.get("currency")
        )
        target_currency = session_currency or product_currency
        chosen_price = primary_price
        chosen_currency = primary_currency
        price_source = "primary"
        if (
            target_currency
            and approx_price is not None
            and approx_currency == target_currency
            and primary_currency != target_currency
        ):
            chosen_price = approx_price
            chosen_currency = approx_currency
            price_source = "approx"
        elif primary_currency is None and approx_currency:
            chosen_price = approx_price
            chosen_currency = approx_currency
            price_source = "approx"

        currency_mismatch = bool(
            target_currency
            and chosen_currency
            and chosen_currency != target_currency
        )
        product["detail_currency_mismatch"] = currency_mismatch
        product["detail_price_source"] = price_source
        product["detail_primary_currency"] = primary_currency
        if primary_price is not None:
            product["detail_primary_price"] = primary_price
        else:
            product.pop("detail_primary_price", None)
        if currency_mismatch:
            product["detail_currency"] = chosen_currency
            if chosen_price is not None:
                product["detail_price"] = chosen_price
        else:
            product.pop("detail_currency", None)
            product.pop("detail_price", None)

        if result.get("title"):
            product["title"] = result["title"]
        if chosen_price is not None and not currency_mismatch:
            product["price"] = chosen_price
            product["currency"] = chosen_currency or product.get("currency")
            price_is_range = (
                bool(result.get("price_is_range"))
                if price_source == "primary"
                else False
            )
            price_max = (
                result.get("price_max") if price_is_range else None
            )
            product["price_max"] = price_max
            product["price_is_range"] = price_is_range
        if result.get("attributes"):
            product["attributes"] = result["attributes"]
        keys = (
            "condition",
            "shipping",
            "shipping_cost",
            "returns",
            "location",
            "availability",
            "listing_type",
            "best_offer",
            "bids",
            "watchers",
            "sold",
            "quantity_available",
            "has_variations",
            "variations",
            "highlights",
            "images",
            "item_number",
        )
        for key in keys:
            if key == "shipping_cost" and (
                currency_mismatch or price_source == "approx"
            ):
                continue
            if result.get(key) is not None:
                product[key] = result[key]
        if currency_mismatch or product.get("price") is None:
            product.pop("shipping_cost", None)
            product["cost_complete"] = False
            product["landed_cost"] = None
        else:
            shipping = result.get("shipping_cost")
            if shipping is None:
                product.pop("shipping_cost", None)
                product["cost_complete"] = False
                product["landed_cost"] = None
            elif price_source == "approx" and float(shipping) != 0:
                # The approximate item price is in the session currency while
                # the shipping amount is in the primary listing currency; do
                # not add unlike currencies.
                product.pop("shipping_cost", None)
                product["cost_complete"] = False
                product["landed_cost"] = None
            else:
                price_cap = (
                    product.get("price_max")
                    if product.get("price_is_range")
                    else product.get("price")
                )
                product["shipping_cost"] = shipping
                product["landed_cost"] = round(
                    float(price_cap) + float(shipping), 2
                )
                product["cost_complete"] = True
        if isinstance(result.get("seller"), dict):
            product["seller"] = result["seller"]
        images = result.get("images")
        if isinstance(images, list) and images:
            product.setdefault("image_url", images[0])
        store.touch(self.session)
        response = {
            "status": "ok",
            "product_id": product_id,
            "product": _public_product(product),
        }
        if currency_mismatch:
            response["currency_mismatch"] = True
            response["detail_currency"] = chosen_currency
            response["session_currency"] = session_currency
        return response

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
                settings=self.session.settings,
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

    def ask_user(
        self,
        questions: list[dict[str, Any]],
        reason: str = "need_user",
    ) -> dict[str, Any]:
        self.session.status = "interrupted"
        self.session.interrupt_reason = reason
        self.session.pending_questions = questions
        self.last_ask = str(questions[0].get("question") or "")
        store.touch(self.session)
        return {"ok": True, "questions": questions, "reason": reason}

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
        "price_max": product.get("price_max"),
        "currency": product.get("currency"),
        "url": product.get("url"),
        "image_url": product.get("image_url"),
        "condition": product.get("condition"),
        "shipping": product.get("shipping"),
        "shipping_cost": product.get("shipping_cost"),
        "landed_cost": product.get("landed_cost"),
        "cost_complete": product.get("cost_complete"),
        "availability": product.get("availability"),
        "listing_type": product.get("listing_type"),
        "seller": product.get("seller"),
        "detail_fetched": product.get("detail_fetched"),
        "detail_currency_mismatch": product.get(
            "detail_currency_mismatch", False
        ),
        "detail_currency": product.get("detail_currency"),
        "detail_price_source": product.get("detail_price_source"),
        "detail_primary_currency": product.get("detail_primary_currency"),
        "attributes": product.get("attributes") or {},
    }
