"""Deterministic shopping rubric behind the generic Critic protocol."""
from __future__ import annotations

from typing import Any

from app.agent.critic import Critique
from app.agent.state import AgentState, Plan
from app.config import settings
from app.constraints import check_product
from app.store import Session
from app.tools import ToolBox


class ShoppingCritic:
    def __init__(self, session: Session, toolbox: ToolBox) -> None:
        self.session = session
        self.toolbox = toolbox

    async def evaluate(
        self, *, state: AgentState, output: dict[str, Any]
    ) -> Critique:
        recommendations = list(output.get("recommendations") or [])
        if not recommendations:
            return Critique(
                ok=False,
                violations=["no_recommendations"],
                reasons=["没有可提交的推荐"],
                retry=True,
                hints=["重新查询库存或放宽已确认的约束"],
                scores={"hard_constraints": 0.0, "evidence": 0.0, "diversity": 0.0},
            )

        requirements = self.session.requirements or {}
        violations: list[str] = []
        hints: list[str] = []
        reasons: list[str] = []
        seen: set[str] = set()
        detail_count = 0
        sponsored_count = 0

        for item in recommendations:
            product_id = str(item.get("product_id") or "")
            if product_id in seen:
                violations.append(f"duplicate:{product_id}")
                hints.append("每个商品最多推荐一次")
                continue
            seen.add(product_id)
            product = self.session.products.get(product_id)
            if product is None:
                violations.append(f"unknown_product:{product_id}")
                hints.append("只能推荐 query_inventory 返回的商品")
                continue
            if product.get("detail_fetched"):
                detail_count += 1
            if product.get("sponsored"):
                sponsored_count += 1
            eligibility = check_product(
                product,
                requirements,
                sites=self.session.sites,
                require_detail=not self.toolbox.mock_mode,
            )
            if not eligibility.ok:
                violations.extend(eligibility.violations)
                if "budget" in eligibility.violations:
                    hints.append("移除超预算商品或更正价格")
                if "budget_min" in eligibility.violations:
                    hints.append("移除低于预算下限的商品")
                if any(
                    violation.startswith("must_have:")
                    for violation in eligibility.violations
                ):
                    hints.append("补齐硬性要求或换一个候选")
                if "detail" in eligibility.violations:
                    hints.append("先 fetch_detail 验证规格")

        plan_violation = self._plan_violation(state)
        if plan_violation:
            violations.append(plan_violation)
            hints.append("先完成计划中的推荐步骤")

        violations = list(dict.fromkeys(violations))
        hard = {
            "budget",
            "budget_min",
            "site",
            "category",
            "detail",
        }
        hard_violations = [
            violation
            for violation in violations
            if violation in hard
            or violation.startswith("must_have:")
            or violation.startswith("duplicate:")
            or violation.startswith("unknown_product:")
        ]
        scores = {
            "hard_constraints": 0.0 if hard_violations else 1.0,
            "evidence": round(detail_count / len(recommendations), 3),
            "diversity": round(len(seen) / len(recommendations), 3),
            "risk": round(1.0 - sponsored_count / len(recommendations), 3),
        }
        if violations:
            reasons.append("；".join(dict.fromkeys(violations)))
        return Critique(
            ok=not violations,
            violations=violations,
            scores=scores,
            reasons=reasons,
            retry=bool(violations),
            hints=list(dict.fromkeys(hints)),
        )

    @staticmethod
    def _plan_violation(state: AgentState) -> str | None:
        raw = state.get("plan")
        if not raw:
            return None
        try:
            plan = Plan.model_validate(raw)
        except (TypeError, ValueError):
            return "invalid_plan"
        roles = plan.metadata.get("step_roles") or {}
        recommend_id = roles.get("recommend") if isinstance(roles, dict) else None
        if not recommend_id:
            return None
        step = next(
            (item for item in plan.steps if item.id == recommend_id), None
        )
        if step is None:
            return None
        index = plan.steps.index(step)
        if step.status == "pending":
            return "plan_step_incomplete"
        if any(previous.status != "done" for previous in plan.steps[:index]):
            return "plan_step_incomplete"
        return None
