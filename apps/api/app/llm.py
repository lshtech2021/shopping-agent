"""LLM access point (OpenAI-compatible). Isolated so tests can inject a fake model."""
from __future__ import annotations

from typing import Any

from app.config import settings


def llm_enabled() -> bool:
    return bool(settings.llm_api_key)


def build_chat_model() -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        temperature=0,
        timeout=60,
        max_retries=2,
    )
