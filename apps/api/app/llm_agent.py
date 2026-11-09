"""Shopping policy entrypoint: wire the domain pack into the generic runtime."""
from __future__ import annotations

from typing import Any

from app import llm
from app.agent.checkpoint import PgCheckpointStore
from app.agent.context import ContextBuilder
from app.agent.policies.plan_execute import (
    PlanExecutePolicy,
    record_plan_evidence,
)
from app.agent.policies.react import ReActPolicy
from app.agent.runtime import AgentRuntime, TurnResult
from app.agent.state import PendingUser, Plan
from app.agent.tools import ToolContext, ToolRegistry, ToolResult
from app.config import settings
from app.domains.shopping.adapter import (
    ShoppingBrowser,
    ShoppingTrace,
    initial_state,
    to_message_response,
)
from app.domains.shopping.critic import ShoppingCritic
from app.domains.shopping.memory import ShoppingMemory
from app.domains.shopping.planner import ShoppingPlanner, ShoppingReplanner
from app.domains.shopping.prompt import SYSTEM_PROMPT
from app.domains.shopping.tools import (
    build_shopping_tools,
    to_langchain_tools,
)
from app.store import Session
from app.tools import ToolBox
from app.trace import log_event


def _finish_response(session: Session, result: TurnResult) -> dict[str, Any]:
    response = to_message_response(result)
    session.messages.append({"role": "assistant", "content": response.reply})
    payload = response.model_dump()
    payload["plan"] = session.plan
    return payload


async def run_llm_policy(
    session: Session,
    text: str,
    box: ToolBox,
    *,
    skip_parse: bool = False,
    checkpoint_resume: bool = False,
) -> dict[str, Any]:
    if not skip_parse:
        parsed = box.update_requirements(text)
        if parsed.get("needs_clarify"):
            questions = [
                {
                    "id": "budget",
                    "field": "budget",
                    "question": "预算大概是多少？请给出一个上限，或选择「不限预算」。",
                    "options": [
                        "3000以内",
                        "3000-5000",
                        "5000-8000",
                        "8000-15000",
                        "1.5万以上",
                        "不限预算",
                    ],
                }
            ]
            box.ask_clarify(questions)
            pending = PendingUser(
                question="请先确认以下筛选条件。",
                reason="clarify",
                metadata={"clarify": questions},
            )
            return _finish_response(
                session,
                TurnResult(
                    run_id=session.id,
                    status="waiting_user",
                    pending_user=pending,
                ),
            )

    turn_text = text
    if skip_parse:
        turn_text = (
            "（用户已回答澄清问题，请直接继续选品，不要再次调用 clarify。）\n"
            + text
        )

    base_model = llm.build_chat_model()
    if session.plan is None and hasattr(base_model, "with_structured_output"):
        planner = ShoppingPlanner(model=base_model)
        plan = await planner.plan(
            goal=session.requirement or text,
            context={
                "requirements": dict(session.requirements or {}),
                "sites": list(session.sites),
                "searched_sites": list(session.searched_sites),
                "product_count": len(session.products),
            },
        )
        session.plan = plan.model_dump()
        log_event(
            session,
            "生成执行计划",
            stage="plan.created",
            source="agent",
            data={"plan": session.plan},
        )

    checkpoint_store = PgCheckpointStore(settings.database_url)
    state = initial_state(session, turn_text)
    state["turn"] = session.turn
    saved = None
    if checkpoint_resume:
        candidate = await checkpoint_store.load(session.id)
        if (
            candidate is not None
            and int(candidate.get("turn") or 0) == session.turn
            and candidate.get("phase")
            in {"executing", "waiting_user", "failed"}
        ):
            saved = candidate
            state = saved
    else:
        await checkpoint_store.delete(session.id)
    memory = ShoppingMemory(session, box)
    specs = build_shopping_tools(box, state)
    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec)

    model = base_model.bind_tools(to_langchain_tools(specs))
    context = ToolContext(
        run_id=session.id,
        memory=memory,
        browser=ShoppingBrowser(box),
        trace=ShoppingTrace(session),
    )
    async def finalize(state, output):
        picks = list(output.get("picks") or [])
        committed = box.recommend(
            picks, str(output.get("reply") or "")
        )
        if not committed.get("ok"):
            raise RuntimeError(committed.get("error") or "recommend_failed")
        raw_plan = state.get("plan")
        if raw_plan:
            try:
                plan = Plan.model_validate(raw_plan)
            except (TypeError, ValueError):
                plan = None
            if plan is not None:
                roles = plan.metadata.get("step_roles") or {}
                step_id = (
                    roles.get("recommend")
                    if isinstance(roles, dict)
                    else None
                )
                if step_id:
                    record_plan_evidence(
                        state,
                        ToolResult(
                            call_id="finalize",
                            tool="recommend",
                            ok=True,
                            meta={
                                "plan_step_id": str(step_id),
                                "plan_step_done": True,
                                "evidence": [
                                    "recommended="
                                    + ",".join(
                                        str(item.get("product_id") or "")
                                        for item in picks
                                    )
                                ],
                            },
                        ),
                    )

    runtime = AgentRuntime(
        tools=registry,
        context_builder=ContextBuilder(),
        max_steps=settings.agent_max_steps,
        system_prompt=SYSTEM_PROMPT,
        memory=memory,
        tool_context=context,
        critic=ShoppingCritic(session, box),
        replanner=ShoppingReplanner(),
        max_replans=2,
        finalize=finalize,
        checkpoint_store=checkpoint_store,
    )
    policy = PlanExecutePolicy(delegate=ReActPolicy(model=model))
    if saved is not None and saved.get("phase") in {
        "executing",
        "waiting_user",
        "failed",
    }:
        result = await runtime.resume_from_checkpoint(
            run_id=session.id,
            policy=policy,
            user_input=turn_text,
        )
        if result is None:
            result = await runtime.run_turn(state=state, policy=policy)
    else:
        result = await runtime.run_turn(state=state, policy=policy)
    session.plan = state.get("plan") or session.plan

    response = to_message_response(result)
    if result.status == "waiting_user":
        session.status = "interrupted"
        session.interrupt_reason = (
            result.pending_user.reason if result.pending_user else "clarify"
        )
    elif result.status == "done":
        session.status = "interrupted" if response.interrupted else "done"
        session.interrupt_reason = response.interrupt_reason
    elif result.status in {"max_steps", "failed"}:
        box.ask_user(
            response.reply,
            response.interrupt_reason or "agent_error",
        )

    session.messages.append({"role": "assistant", "content": response.reply})
    payload = response.model_dump()
    payload["plan"] = session.plan
    return payload
