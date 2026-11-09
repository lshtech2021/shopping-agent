"""Shopping-domain adapter over the generic Memory protocol."""
from __future__ import annotations

import uuid
from typing import Any

from app.store import Session, store
from app.tools import ToolBox


class ShoppingMemory:
    def __init__(self, session: Session, toolbox: ToolBox) -> None:
        self.session = session
        self.toolbox = toolbox
        self._extra: dict[str, Any] = {}

    def get_requirements(self) -> dict:
        return self.session.requirements

    def query_inventory(self, *, limit: int) -> list[dict]:
        result = self.toolbox.query_inventory(limit=limit)
        return list(result.get("items") or [])

    def record_product(self, product: dict) -> None:
        product_id = str(
            product.get("product_id")
            or product.get("source_product_id")
            or uuid.uuid4().hex[:12]
        )
        recorded = dict(product)
        recorded["product_id"] = product_id
        self.session.products[product_id] = recorded
        store.touch(self.session)

    async def load(self, run_id: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "run_id": run_id,
            "session_id": self.session.id,
            "status": self.session.status,
            "requirements": dict(self.session.requirements or {}),
            "sites": list(self.session.sites),
            "product_count": len(self.session.products),
            "recommendation_count": len(self.session.recommendations),
        }
        data.update(self._extra)
        return data

    async def save(self, run_id: str, patch: dict[str, Any]) -> None:
        self._extra.update(patch)
        for key in ("requirements", "status", "recommendations"):
            if key in patch:
                setattr(self.session, key, patch[key])
        store.touch(self.session)

    async def query(
        self, query: str, *, limit: int
    ) -> list[dict[str, Any]]:
        needle = str(query or "").lower().strip()
        if not needle:
            return []
        matches: list[dict[str, Any]] = []
        for product in self.session.products.values():
            text = " ".join(
                [
                    str(product.get("title") or ""),
                    str(product.get("category") or ""),
                    str(product.get("attributes") or ""),
                ]
            ).lower()
            if needle in text:
                matches.append(product)
            if len(matches) >= limit:
                break
        return matches

    async def summarize(self, run_id: str) -> str:
        from app.domains.shopping.prompt import context_block

        return context_block(self.toolbox)
