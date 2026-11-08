"""Shopping Agent API."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
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
from app.ws_hub import hub

app = FastAPI(title=settings.app_name, version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.exists():
    app.mount("/console", StaticFiles(directory=str(WEB_DIR), html=True), name="console")


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/console/")


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "extension_online": hub.online_count,
        "mock_browser": settings.mock_browser,
        "session_count": len(store.list_ids()),
    }


@app.get("/api/v1/sessions", response_model=list[SessionSummary])
def list_sessions(limit: int = Query(default=50, ge=1, le=200)) -> list[SessionSummary]:
    return [SessionSummary(**item) for item in store.list_summaries(limit=limit)]


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    if not payload.sites:
        raise HTTPException(status_code=422, detail="至少选择一个站点")
    session = store.create(payload.requirement, list(payload.sites))
    return CreateSessionResponse(
        session_id=session.id,
        status=session.status,
        requirement=session.requirement,
        sites=session.sites,
    )


@app.get("/api/v1/sessions/{session_id}", response_model=SessionDetail)
def get_session(session_id: str) -> SessionDetail:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
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
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@app.post("/api/v1/sessions/{session_id}/messages", response_model=MessageResponse)
async def post_message(session_id: str, payload: MessageRequest) -> MessageResponse:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    content = payload.content.strip()
    session.messages.append(
        {
            "role": "user",
            "content": content,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    store.touch(session)

    from app.agent_runner import run_agent

    result = await run_agent(session, content)
    store.touch(session)
    return MessageResponse(**result)


@app.post("/api/v1/sessions/{session_id}/resume", response_model=MessageResponse)
async def resume_session(session_id: str, payload: ResumeRequest) -> MessageResponse:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    from app.agent_runner import resume_agent

    result = await resume_agent(session, payload.note or "continue")
    store.touch(session)
    return MessageResponse(**result)


@app.websocket("/ws/extension")
async def extension_ws(websocket: WebSocket) -> None:
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
        await hub.disconnect(conn_id)
    except Exception:
        await hub.disconnect(conn_id)
        raise
