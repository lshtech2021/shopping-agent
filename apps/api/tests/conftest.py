import json
import os
import uuid

os.environ["SA_DATABASE_URL"] = "memory"  # force in-memory store before app settings import

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app import llm
from app.config import settings
from app.store import store


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "memory")
    monkeypatch.setattr(settings, "mock_browser", True)
    store.reset("memory")
    yield
    store.reset("memory")


# --- shared helpers for LLM-policy tests (fake chat model, no network) ---------


def _call(name: str, **args) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex, "type": "tool_call"}],
    )


def _last_tool_json(messages) -> dict:
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    return json.loads(tool_msgs[-1].content)


class FakeModel:
    """Scripted stand-in for the chat model."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


@pytest.fixture
def fake_llm(monkeypatch):
    holder: dict = {}

    def install(script):
        holder["model"] = FakeModel(script)
        monkeypatch.setattr(llm, "llm_enabled", lambda: True)
        monkeypatch.setattr(llm, "build_chat_model", lambda: holder["model"])
        return holder["model"]

    return install
