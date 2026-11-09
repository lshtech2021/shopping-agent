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
        messages = [
            SystemMessage(content=PLANNER_PROMPT),
            HumanMessage(
                content="Goal:\n"
                + goal
                + "\n\nContext:\n"
                + json.dumps(context, ensure_ascii=False, default=str)
            ),
        ]
        try:
            structured = self.model
            if hasattr(self.model, "with_structured_output"):
                structured = self.model.with_structured_output(Plan)
            response = await structured.ainvoke(messages)
            plan = self._coerce_plan(response)
        except Exception:  # noqa: BLE001 — planner failure must not kill the turn
            try:
                plan = self._coerce_plan(await self.model.ainvoke(messages))
            except Exception:  # noqa: BLE001
                plan = self._fallback_plan(goal, context)
        return self._normalize(plan, goal=goal, context=context)

    @staticmethod
    def _coerce_plan(response: Any) -> Plan:
        if isinstance(response, Plan):
            return response
        if isinstance(response, dict):
            return Plan.model_validate(response)
        content = getattr(response, "content", None)
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        if isinstance(content, str):
            text = content.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                text = "\n".join(lines).strip()
            return Plan.model_validate_json(text)
        raise ValueError("planner did not return a Plan")

    def _normalize(
        self, plan: Plan, *, goal: str, context: dict[str, Any]
    ) -> Plan:
        steps: list[PlanStep] = []
        seen_ids: set[str] = set()
        for index, step in enumerate(plan.steps):
            step_id = str(step.id or "").strip() or f"step-{index + 1}"
            base = step_id
            suffix = 2
            while step_id in seen_ids:
                step_id = f"{base}-{suffix}"
                suffix += 1
            seen_ids.add(step_id)
            steps.append(step.model_copy(update={"id": step_id}))
        if not steps:
            steps = [
                PlanStep(id="search", description="search"),
                PlanStep(id="verify", description="verify"),
                PlanStep(id="recommend", description="recommend"),
            ]

        metadata = dict(plan.metadata)
        queries = metadata.get("search_queries")
        if not isinstance(queries, dict):
            metadata["search_queries"] = {}
        else:
            metadata["search_queries"] = {
                str(site): (
                    [str(query)]
                    if isinstance(raw, str)
                    else [str(query) for query in (raw or [])]
                )
                for site, raw in queries.items()
            }
        checklist = metadata.get("evidence_checklist")
        metadata["evidence_checklist"] = (
            [str(item) for item in checklist]
            if isinstance(checklist, (list, tuple))
            else ([str(checklist)] if checklist else [])
        )
        stops = metadata.get("stop_conditions")
        metadata["stop_conditions"] = (
            [str(item) for item in stops]
            if isinstance(stops, (list, tuple))
            else ([str(stops)] if stops else [])
        )
        roles = metadata.get("step_roles")
        if not isinstance(roles, dict):
            roles = self._derive_roles(steps)
        by_id = {step.id: step for step in steps}
        executable: list[PlanStep] = []
        used_ids: set[str] = set()
        for role in ("search", "verify", "recommend"):
            step_id = str(roles.get(role) or role)
            step = by_id.get(step_id)
            if step is None:
                step = PlanStep(id=role, description=role)
            if step.id in used_ids:
                step = PlanStep(id=role, description=step.description)
            used_ids.add(step.id)
            roles[role] = step.id
            executable.append(step)
        metadata["step_roles"] = {
            role: str(roles[role]) for role in ("search", "verify", "recommend")
        }
        metadata.setdefault("requirements", dict(context.get("requirements") or {}))
        return Plan(
            goal=plan.goal or goal,
            steps=executable,
            metadata=metadata,
        )

    @staticmethod
    def _derive_roles(steps: list[PlanStep]) -> dict[str, str]:
        roles: dict[str, str] = {}
        for step in steps:
            text = f"{step.id} {step.description}".lower()
            if "search" not in roles and any(
                token in text for token in ("search", "搜索", "collect", "搜")
            ):
                roles["search"] = step.id
            elif "verify" not in roles and any(
                token in text for token in ("verify", "detail", "详情", "核验")
            ):
                roles["verify"] = step.id
            elif "recommend" not in roles and any(
                token in text for token in ("recommend", "推荐", "compare", "比较")
            ):
                roles["recommend"] = step.id
        unused = [step for step in steps if step.id not in roles.values()]
        for role in ("search", "verify", "recommend"):
            if role in roles:
                continue
            roles[role] = (unused.pop(0) if unused else steps[-1]).id
        return roles

    @staticmethod
    def _fallback_plan(goal: str, context: dict[str, Any]) -> Plan:
        sites = [str(site) for site in context.get("sites") or ["jd"]]
        queries = {site: [goal] for site in sites}
        steps = [
            PlanStep(id="search", description="search"),
            PlanStep(id="verify", description="verify"),
            PlanStep(id="recommend", description="recommend"),
        ]
        return Plan(
            goal=goal,
            steps=steps,
            metadata={
                "search_queries": queries,
                "evidence_checklist": [],
                "stop_conditions": [],
                "step_roles": {
                    "search": "search",
                    "verify": "verify",
                    "recommend": "recommend",
                },
                "requirements": dict(context.get("requirements") or {}),
            },
        )


class ShoppingReplanner:
    def __init__(self, max_replans: int = 2) -> None:
        self.max_replans = max_replans

    async def replan(
        self,
        *,
        state: AgentState,
        output: dict[str, Any],
        critique: Critique,
    ) -> Plan | None:
        raw_plan = state.get("plan")
        if not raw_plan:
            return None
        try:
            plan = Plan.model_validate(raw_plan)
        except (TypeError, ValueError):
            return None

        metadata = dict(plan.metadata)
        used = int(metadata.get("replan_count") or 0)
        if used >= self.max_replans:
            return None
        if not self._is_poor(state, output, critique, metadata):
            return None

        requirements = metadata.get("requirements") or {}
        if not isinstance(requirements, dict):
            requirements = {}
        queries = metadata.get("search_queries") or {}
        if not isinstance(queries, dict):
            queries = {}
        adjusted: dict[str, list[str]] = {}
        for site, raw_queries in queries.items():
            values = (
                [raw_queries]
                if isinstance(raw_queries, str)
                else list(raw_queries or [])
            )
            adjusted[str(site)] = [
                self._adjust_query(
                    str(site),
                    str(query),
                    requirements,
                    used,
                )
                for query in values
            ]
        if not any(adjusted.values()) or adjusted == queries:
            return None

        metadata["search_queries"] = adjusted
        metadata["replan_count"] = used + 1
        roles = metadata.get("step_roles") or {}
        search_id = roles.get("search") if isinstance(roles, dict) else None
        steps = list(plan.steps)
        search_id = search_id or (steps[0].id if steps else None)
        if search_id:
            for step in steps:
                if step.id == search_id:
                    step.status = "in_progress"
                    break
        return Plan(goal=plan.goal, steps=steps, metadata=metadata)

    @staticmethod
    def _adjust_query(
        site: str,
        query: str,
        requirements: dict[str, Any],
        used: int,
    ) -> str:
        base = query.strip()
        for marker in (" -book -accessory", " -used", " 推荐", " 优选"):
            if base.endswith(marker):
                base = base[: -len(marker)].strip()
        category = str(requirements.get("category") or "")
        if site == "ebay":
            if category in {"书", "图书", "配件"}:
                return f"{base} {'new' if used == 0 else 'reviewed'}"
            return f"{base} {'-book -accessory' if used == 0 else '-used'}"
        return f"{base} {category or '商品'} {'推荐' if used == 0 else '优选'}"

    @staticmethod
    def _is_poor(
        state: AgentState,
        output: dict[str, Any],
        critique: Critique,
        metadata: dict[str, Any],
    ) -> bool:
        if critique.violations:
            return True
        if "recommendations" in output and not output.get("recommendations"):
            return True
        if ShoppingReplanner._is_poor_result(output):
            return True
        results = state.get("tool_results") or []
        if results and isinstance(results[-1], dict):
            data = results[-1].get("data") or {}
            if ShoppingReplanner._is_poor_result(data):
                return True
        checklist = metadata.get("evidence_checklist") or []
        evidence = [
            evidence
            for result in results
            if isinstance(result, dict)
            for evidence in (result.get("meta") or {}).get("evidence") or []
        ]
        return bool(checklist) and not evidence

    @staticmethod
    def _is_poor_result(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        if data.get("quality") == "poor":
            return True
        if data.get("result_count") == 0 or data.get("no_results"):
            return True
        return False
