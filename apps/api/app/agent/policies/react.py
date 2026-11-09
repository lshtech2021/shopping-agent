"""Minimal ReAct policy: ask the tool-calling model for the next action."""
from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from app.agent.state import AgentState


class ReActPolicy:
    def __init__(self, *, model: Any) -> None:
        self.model = model

    async def next_action(
        self, state: AgentState, context: list[BaseMessage]
    ) -> AIMessage:
        return await self.model.ainvoke(context)
