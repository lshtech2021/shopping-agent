"""Agent entry — Phase 0 echoes; Phase 1+ runs tool loop."""
from __future__ import annotations

from typing import Any

from app.store import Session, store


async def run_agent(session: Session, content: str) -> dict[str, Any]:
    reply = (
        f"[phase0-echo] 已收到：{content}。"
        "下一阶段将启用选品工具闭环。"
    )
    session.messages.append({"role": "assistant", "content": reply})
    session.status = "idle"
    session.interrupt_reason = None
    store.touch(session)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": reply,
        "recommendations": [],
        "interrupted": False,
        "interrupt_reason": None,
    }


async def resume_agent(session: Session, note: str) -> dict[str, Any]:
    reply = f"[phase0-echo] resume: {note or 'continue'}"
    session.messages.append({"role": "assistant", "content": reply})
    session.status = "idle"
    session.interrupt_reason = None
    store.touch(session)
    return {
        "session_id": session.id,
        "status": session.status,
        "reply": reply,
        "recommendations": [],
        "interrupted": False,
        "interrupt_reason": None,
    }
