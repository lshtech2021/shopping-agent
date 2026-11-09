"""Shopping policy entrypoint: wire the domain pack into the generic runtime."""
from __future__ import annotations

from typing import Any

from app import llm
from app.agent.context import ContextBuilder
from app.agent.policies.react import ReActPolicy
from app.agent.runtime import AgentRuntime, TurnResult
from app.agent.state import PendingUser
from app.agent.tools import ToolContext, ToolRegistry
from app.config import settings
from app.domains.shopping.adapter import (
    ShoppingBrowser,
    ShoppingTrace,
    initial_state,
    to_message_response,
)
from app.domains.shopping.memory import ShoppingMemory
from app.domains.shopping.prompt import SYSTEM_PROMPT
from app.domains.shopping.tools import (
    build_shopping_tools,
    to_langchain_tools,
)
from app.store import Session
from app.tools import ToolBox


def _finish_response(session: Session, result: TurnResult) -> dict[str, Any]:
    response = to_message_response(result)
    session.messages.append({"role": "assistant", "content": response.reply})
    return response.model_dump()


async def run_llm_policy(
    session: Session,
    text: str,
    box: ToolBox,
    *,
    skip_parse: bool = False,
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

    state = initial_state(session, turn_text)
    memory = ShoppingMemory(session, box)
    specs = build_shopping_tools(box, state)
    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec)

    model = llm.build_chat_model().bind_tools(to_langchain_tools(specs))
    context = ToolContext(
        run_id=session.id,
        memory=memory,
        browser=ShoppingBrowser(box),
        trace=ShoppingTrace(session),
    )
    runtime = AgentRuntime(
        tools=registry,
        context_builder=ContextBuilder(),
        max_steps=settings.agent_max_steps,
        system_prompt=SYSTEM_PROMPT,
        memory=memory,
        tool_context=context,
    )
    result = await runtime.run_turn(
        state=state, policy=ReActPolicy(model=model)
    )

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
    return response.model_dump()
