"""Shopping Agent API."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import llm
from app.config import settings
from app.logging_setup import setup_logging
from app.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    MessageRequest,
    MessageResponse,
    ResumeRequest,
    SessionDetail,
    SessionSummary,
)
from app.store import store
from app.trace import log_event
from app.ws_hub import hub, origin_allowed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    store.reset(settings.database_url)
    yield


app = FastAPI(title=settings.app_name, version="0.5.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.exists():
    app.mount("/console", StaticFiles(directory=str(WEB_DIR), html=True), name="console")

# One agent turn at a time per session: concurrent turns would interleave tool
# calls and overwrite each other's state.
_session_locks: dict[str, asyncio.Lock] = {}


def _lock_for(session_id: str) -> asyncio.Lock:
    return _session_locks.setdefault(session_id, asyncio.Lock())


def _require_session(session_id: str):
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/console/")


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "extension_online": hub.online_count,
        "mock_browser": settings.mock_browser,
        "llm_enabled": llm.llm_enabled(),
        "persistent": store.persistent,
        "session_count": store.count(),
    }


@app.get("/api/v1/sessions", response_model=list[SessionSummary])
def list_sessions(limit: int = Query(default=50, ge=1, le=200)) -> list[SessionSummary]:
    return [SessionSummary(**item) for item in store.list_summaries(limit=limit)]


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    if not payload.sites:
        raise HTTPException(status_code=422, detail="至少选择一个站点")
    session = store.create(payload.requirement, list(payload.sites))
    log_event(
        session,
        "创建会话",
        stage="api.create_session",
        source="api",
        data={"requirement": payload.requirement, "sites": list(payload.sites)},
        persist=True,
    )
    return CreateSessionResponse(
        session_id=session.id,
        status=session.status,
        requirement=session.requirement,
        sites=session.sites,
    )


@app.get("/api/v1/sessions/{session_id}", response_model=SessionDetail)
def get_session(session_id: str) -> SessionDetail:
    session = _require_session(session_id)
    return SessionDetail(
        session_id=session.id,
        status=session.status,
        requirement=session.requirement,
        sites=session.sites,
        requirements=session.requirements,
        messages=session.messages,
        products=list(session.products.values()),
        recommendations=session.recommendations,
        searched_sites=session.searched_sites,
        interrupt_reason=session.interrupt_reason,
        pending_questions=session.pending_questions,
        blocked_sites=session.blocked_sites,
        execution_logs=session.execution_logs,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@app.post("/api/v1/sessions/{session_id}/messages", response_model=MessageResponse)
async def post_message(session_id: str, payload: MessageRequest) -> MessageResponse:
    session = _require_session(session_id)
    lock = _lock_for(session_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="上一轮仍在执行，请稍候")
    async with lock:
        from app.agent_runner import run_agent

        content = payload.content.strip()
        log_event(
            session,
            "收到用户消息",
            stage="api.message",
            source="api",
            data={"content": content[:200], "answers": payload.answers},
            persist=False,
        )
        if content:
            session.messages.append(
                {
                    "role": "user",
                    "content": content,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
        result = await run_agent(session, content, payload.answers)
        return MessageResponse(**result)


@app.post("/api/v1/sessions/{session_id}/resume", response_model=MessageResponse)
async def resume_session(session_id: str, payload: ResumeRequest) -> MessageResponse:
    session = _require_session(session_id)
    lock = _lock_for(session_id)
    if lock.locked():
        raise HTTPException(status_code=409, detail="上一轮仍在执行，请稍候")
    async with lock:
        from app.agent_runner import resume_agent

        result = await resume_agent(session, payload.note or "continue")
        return MessageResponse(**result)


@app.websocket("/ws/extension")
async def extension_ws(websocket: WebSocket) -> None:
    origin = websocket.headers.get("origin")
    if not origin_allowed(origin):
        await websocket.close(code=4403)
        return
    conn_id = await hub.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                await websocket.send_json({"type": "error", "detail": "expected object"})
                continue
            reply = await hub.handle_message(conn_id, data)
            if reply is not None:
                await websocket.send_json(reply)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(conn_id)
