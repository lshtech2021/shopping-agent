"""JD backend site pack."""
from __future__ import annotations

from typing import Any, Literal


class JDAdapter:
    id: Literal["jd"] = "jd"

    def system_prompt_extra(
        self, settings: dict[str, Any], requirements: dict[str, Any]
    ) -> str:
        currency = str(settings.get("currency") or "the session currency")
        return (
            "## JD site guidance\n"
            "- Use concise Chinese product keywords for JD searches.\n"
            f"- The budget and search price cap are in {currency}.\n"
            "- Treat a displayed price range as a range until a detail page "
            "confirms the selected variation."
        )

    def build_search_filters(
        self,
        *,
        query: str,
        requirements: dict[str, Any],
        settings: dict[str, Any],
        max_price: float | None,
    ) -> dict[str, Any]:
        return {}

adapter = JDAdapter()
