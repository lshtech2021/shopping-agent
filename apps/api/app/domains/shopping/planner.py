"""Shopping planner: produce a generic Plan with shopping metadata."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app import llm
from app.agent.critic import Critique
from app.agent.state import AgentState, Plan, PlanStep

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


class ShoppingReplanner:
    def __init__(self, max_replans: int = 2) -> None:
        self.max_replans = max_replans
        self._used = 0

    async def replan(
        self,
        *,
        state: AgentState,
        output: dict[str, Any],
        critique: Critique,
    ) -> Plan | None:
        if self._used >= self.max_replans:
            return None
        if not self._is_poor(state, output, critique):
            return None
        raw_plan = state.get("plan")
        if not raw_plan:
            return None
        try:
            plan = Plan.model_validate(raw_plan)
        except (TypeError, ValueError):
            return None

        metadata = dict(plan.metadata)
        queries = metadata.get("search_queries") or {}
        adjusted: dict[str, list[str]] = {}
        for site, raw_queries in queries.items():
            values = (
                [raw_queries]
                if isinstance(raw_queries, str)
                else list(raw_queries or [])
            )
            adjusted[str(site)] = [
                self._adjust_query(str(query)) for query in values
            ]
        if not any(adjusted.values()):
            return None
        metadata["search_queries"] = adjusted
        self._used += 1
        metadata["replan_count"] = int(metadata.get("replan_count") or 0) + 1
        steps = list(plan.steps)
        steps.append(
            PlanStep(
                id=f"replan-{self._used}",
                description="re-search with adjusted queries",
            )
        )
        return Plan(goal=plan.goal, steps=steps, metadata=metadata)

    @staticmethod
    def _adjust_query(query: str) -> str:
        if "-book" in query:
            return query
        return f"{query} -book -accessory"

    @staticmethod
    def _is_poor(
        state: AgentState, output: dict[str, Any], critique: Critique
    ) -> bool:
        if critique.violations:
            return True
        recommendations = output.get("recommendations")
        if recommendations is not None and not recommendations:
            return True
        for result in list(state.get("tool_results") or [])[-3:]:
            if not isinstance(result, dict):
                continue
            data = result.get("data") or {}
            if not isinstance(data, dict):
                continue
            if data.get("quality") == "poor":
                return True
            if data.get("result_count") == 0 or data.get("no_results"):
                return True
        return False
