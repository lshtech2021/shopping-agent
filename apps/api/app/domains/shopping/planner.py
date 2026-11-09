"""Shopping planner: produce a generic Plan with shopping metadata."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app import llm
from app.agent.state import Plan

PLANNER_PROMPT = """You plan a shopping selection run. Return a generic Plan:
- goal: the user's shopping goal.
- steps: 3-5 ordered steps with stable ids and short descriptions.
- metadata.search_queries: object site -> list of search strings.
- metadata.evidence_checklist: specs/prices/details that must be verified.
- metadata.stop_conditions: when to stop collecting and recommend.

Do not add fields to the generic Plan schema. Use only user-stated requirements
from the supplied context; never invent a budget, brand, or specification.
Plan steps must not be skipped: order them so each step has observable evidence.
"""


class ShoppingPlanner:
    def __init__(self, model: Any | None = None) -> None:
        self.model = model or llm.build_chat_model()

    async def plan(
        self, *, goal: str, context: dict[str, Any]
    ) -> Plan:
        structured = self.model
        if hasattr(self.model, "with_structured_output"):
            structured = self.model.with_structured_output(Plan)
        messages = [
            SystemMessage(content=PLANNER_PROMPT),
            HumanMessage(
                content="Goal:\n"
                + goal
                + "\n\nContext:\n"
                + json.dumps(context, ensure_ascii=False, default=str)
            ),
        ]
        response = await structured.ainvoke(messages)
        plan = self._coerce_plan(response)
        metadata = dict(plan.metadata)
        metadata.setdefault("search_queries", {})
        metadata.setdefault("evidence_checklist", [])
        metadata.setdefault("stop_conditions", [])
        return plan.model_copy(update={"metadata": metadata})

    @staticmethod
    def _coerce_plan(response: Any) -> Plan:
        if isinstance(response, Plan):
            return response
        if isinstance(response, dict):
            return Plan.model_validate(response)
        content = getattr(response, "content", None)
        if isinstance(content, str):
            return Plan.model_validate_json(content)
        raise ValueError("planner did not return a Plan")
