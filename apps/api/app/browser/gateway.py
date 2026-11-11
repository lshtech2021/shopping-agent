"""Live/mock browse execution with per-site pacing."""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from app import mock_browser
from app.config import settings
from app.store import Session
from app.trace import log_event
from app.ws_hub import hub

BLOCKING = {"rate_limited", "captcha", "login_required", "blocked"}
SITE_RISK = {"rate_limited", "captcha", "login_required"}

_last_browse_at: dict[str, float] = {}


async def throttle(site: str) -> float:
    """Keep >= browse_min_interval_s (+ jitter) between live calls to one site."""
    wait = 0.0
    last = _last_browse_at.get(site)
    if last is not None:
        gap = settings.browse_min_interval_s + random.uniform(
            0, settings.browse_jitter_s
        )
        wait = max(0.0, gap - (time.monotonic() - last))
    if wait > 0:
        await asyncio.sleep(wait)
    _last_browse_at[site] = time.monotonic()
    return wait


class BrowserGateway:
    def __init__(self, session: Session) -> None:
        self.session = session

    @property
    def mock_mode(self) -> bool:
        return bool(settings.mock_browser)

    async def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        args = {
            **args,
            "settings": dict(self.session.settings),
        }
        mode = "mock" if settings.mock_browser else "live"
        site = str(args.get("site") or "").lower()
        if not settings.mock_browser and hub.online_count > 0:
            waited = await throttle(site)
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
                result = self._mock_execute(tool, args)
            else:
                result = await hub.dispatch_job(
                    tool,
                    args,
                    timeout=settings.job_timeout_s,
                    session=self.session,
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
        if result.get("extension_version"):
            summary["extension_version"] = result["extension_version"]
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
            summary["extension_log_count"] = len(
                result.get("extension_logs") or []
            )
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

    @staticmethod
    def _mock_execute(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == "search":
            return mock_browser.mock_search(
                args.get("site", ""), args.get("query", "")
            )
        if tool == "scrape_listing":
            return mock_browser.mock_scrape_listing(
                args.get("site", ""), int(args.get("page") or 1)
            )
        if tool == "next_page":
            page = int(args.get("page") or 2)
            return mock_browser.mock_scrape_listing(args.get("site", ""), page)
        if tool == "fetch_detail":
            return mock_browser.mock_fetch_detail(
                args.get("site", ""), args.get("source_product_id", "")
            )
        return {
            "status": "parse_error",
            "error": f"unknown browse tool {tool}",
        }
