"""Shopping policy entrypoint: wire the domain pack into the generic runtime."""
from __future__ import annotations

import time
from typing import Any

from app import llm
from app.agent.checkpoint import PgCheckpointStore
from app.agent.context import ContextBuilder
from app.agent.policies.plan_execute import (
    PlanExecutePolicy,
    record_plan_evidence,
)
from app.agent.policies.react import ReActPolicy
from app.agent.runtime import AgentRuntime
from app.agent.state import Plan
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
from app.domains.shopping.planner import (
    ShoppingPlanner,
    ShoppingReplanner,
    planning_ready,
)
from app.domains.shopping.prompt import system_prompt
from app.domains.shopping.tools import (
    build_shopping_tools,
    to_langchain_tools,
)
from app.store import Session, store
from app.tools import ToolBox
from app.trace import log_event


async def run_llm_policy(
    session: Session,
    text: str,
    box: ToolBox,
    *,
    checkpoint_resume: bool = False,
) -> dict[str, Any]:
    # The host does not parse requirements: the model reads the user message
    # and saves constraints itself via the `update_requirements` tool.
    if text and session.pending_questions:
        session.pending_questions = []

    # A turn with no new user text (resume after a risk-control block) gets a
    # neutral nudge instead of replaying the original requirement.
    turn_text = text or "（用户已处理中断，请继续当前任务。）"

    base_model = llm.build_chat_model()
    if session.plan is None and hasattr(base_model, "with_structured_output"):
        planner = ShoppingPlanner(model=base_model)
        context = {
            "requirements": dict(session.requirements or {}),
            "sites": list(session.sites),
            "searched_sites": list(session.searched_sites),
            "product_count": len(session.products),
        }
        if planning_ready(session.requirements, sites=session.sites):
            started = time.perf_counter()
            plan = await planner.plan(
                goal=session.requirement or text,
                context=context,
            )
            log_event(
                session,
                "规划 LLM 调用",
                stage="llm.call",
                source="llm",
                duration_ms=(time.perf_counter() - started) * 1000,
                data={
                    "component": "planner",
                    "model": getattr(base_model, "model_name", None),
                    "structured": True,
                },
            )
        else:
            # The first turn usually has no parsed category/budget yet. A
            # structural plan lets the main LLM extract requirements first;
            # queries are generated from those requirements afterward.
            plan = planner.fallback(
                goal=session.requirement or text,
                context=context,
            )
            log_event(
                session,
                "使用通用步骤计划",
                stage="plan.fallback",
                source="agent",
                data={"reason": "requirements_incomplete"},
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
        # run_agent bumps turn before resume, so a checkpoint saved during the
        # interrupted turn is under turn-1 (or equal if turn was not bumped).
        candidate_turn = int(candidate.get("turn") or 0) if candidate else -1
        if (
            candidate is not None
            and candidate_turn in {session.turn, session.turn - 1}
            and candidate.get("phase")
            in {"executing", "waiting_user", "failed"}
        ):
            saved = candidate
            state = saved
            state["turn"] = session.turn
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
        system_prompt=system_prompt(
            session.settings, session.requirements
        ),
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
            result.pending_user.reason if result.pending_user else "need_user"
        )
        questions = (
            result.pending_user.metadata.get("questions")
            if result.pending_user
            else None
        )
        session.pending_questions = (
            [dict(item) for item in questions if isinstance(item, dict)]
            if isinstance(questions, list)
            else []
        )
    elif result.status == "done":
        session.status = "interrupted" if response.interrupted else "done"
        session.interrupt_reason = response.interrupt_reason
    elif result.status in {"max_steps", "failed"}:
        session.status = "interrupted"
        session.interrupt_reason = response.interrupt_reason or "agent_error"
        store.touch(session)

    session.messages.append({"role": "assistant", "content": response.reply})
    payload = response.model_dump()
    payload["plan"] = session.plan
    return payload
