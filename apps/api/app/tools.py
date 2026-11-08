"""Nine shopping tools: memory + browse (+ mock) + recommend/ask_user."""
from __future__ import annotations

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

    def _check_quota(self, site: str, tool: str) -> Optional[dict[str, Any]]:
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
                result = await hub.dispatch_job(tool, args, session=self.session)
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
        if re.search(r"开发|编程|coding|development", text, re.I):
            req["use_case"] = "开发"
            req["category"] = req.get("category") or "笔记本"
        if re.search(r"笔记本|laptop|notebook", text, re.I):
            req["category"] = "笔记本"
        if re.search(r"32\s*g|内存至少\s*32", text, re.I):
            must = list(req.get("must_have") or [])
            if "32G内存" not in must:
                must.append("32G内存")
            req["must_have"] = must
        if patch:
            for key, value in patch.items():
                if value is not None:
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
        must = self.session.requirements.get("must_have") or []
        need_32g = any("32" in str(m) for m in must)
        category = str(self.session.requirements.get("category") or "")
        filtered = []
        for product in products:
            if product.get("source") not in self.session.sites:
                continue
            title = product.get("title") or ""
            if category == "笔记本" and any(
                token in title for token in ("配件", "贴纸", "保护膜", "贴膜")
            ):
                continue
            if budget is not None and product.get("price_cny") is not None:
                if float(product["price_cny"]) > float(budget):
                    continue
            if need_32g:
                ram = _ram_gb(product)
                if ram is None or ram < 32:
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
        result = await self._browse("scrape_listing", {"site": site, "page": page})
        if result.get("status") != "ok":
            return result
        accepted = 0
        for item in result.get("items") or []:
            if self._upsert_listing(site, item):
                accepted += 1
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
        for pick in picks:
            pid = str(pick.get("product_id") or "")
            product = self.session.products.get(pid)
            if product is None:
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


def _extract_budget(text: str) -> float | None:
    text = text or ""
    patterns = [
        r"预算\s*(\d+(?:\.\d+)?)\s*万",
        r"(\d+(?:\.\d+)?)\s*万\s*以内",
        r"(?:预算|价格|价位)?\s*(\d{3,6})\s*元?",
        r"(\d)\s*千",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = float(match.group(1))
        if "万" in pattern:
            return value * 10000
        if "千" in pattern:
            return value * 1000
        if value < 100 and "千" not in text:
            continue
        return value
    if re.search(r"5\s*千|五千", text):
        return 5000.0
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
