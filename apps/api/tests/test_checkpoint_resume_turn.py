"""Resume must load checkpoints saved under the previous turn number."""
from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.checkpoint import MemoryCheckpointStore
from app.agent_runner import run_agent
from app.store import store


def test_resume_loads_checkpoint_saved_before_turn_bump(fake_llm, monkeypatch):
    shared = MemoryCheckpointStore()

    class SharedCheckpointStore:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def save(self, run_id, state):
            await shared.save(run_id, state)

        async def load(self, run_id):
            return await shared.load(run_id)

        async def delete(self, run_id):
            await shared.delete(run_id)

    monkeypatch.setattr("app.llm_agent.PgCheckpointStore", SharedCheckpointStore)

    seen_blobs: list[str] = []

    def capture(messages):
        blob = "\n".join(str(getattr(m, "content", "") or "") for m in messages)
        seen_blobs.append(blob)
        return AIMessage(content="已从断点继续")

    model = fake_llm([capture])
    session = store.create("预算5000的笔记本", ["jd"])
    session.requirements.update(
        {
            "budget_cny": 5000,
            "category": "笔记本",
            "budget_unlimited": False,
        }
    )
    session.plan = {
        "goal": "选笔记本",
        "steps": [],
        "metadata": {},
    }
    session.turn = 1
    session.status = "interrupted"
    session.interrupt_reason = "captcha"
    # Checkpoint-only marker must not appear unless resume loads turn-1 state.
    checkpoint_marker = "CHECKPOINT_ONLY_SEARCH_PLAN"

    asyncio.run(
        shared.save(
            session.id,
            {
                "run_id": session.id,
                "messages": [
                    HumanMessage(content="预算5000的笔记本"),
                    AIMessage(content=checkpoint_marker),
                ],
                "plan": session.plan,
                "step": 3,
                "turn": 1,
                "phase": "waiting_user",
                "tool_results": [{"tool": "search", "ok": True}],
                "pending_user": {
                    "question": "验证码",
                    "reason": "captcha",
                },
                "error": None,
            },
        )
    )

    result = asyncio.run(
        run_agent(
            session,
            "continue",
            checkpoint_resume=True,
        )
    )

    assert result["reply"] == "已从断点继续"
    assert model.calls == 1
    assert seen_blobs and checkpoint_marker in seen_blobs[0]
    assert session.turn == 2
