"""Shopping Agent API — Phase 0 skeleton."""
from __future__ import annotations

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    MessageRequest,
    MessageResponse,
    ResumeRequest,
    SessionDetail,
)
from app.store import store
from app.ws_hub import hub

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "extension_online": hub.online_count,
        "mock_browser": settings.mock_browser,
    }


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    if not payload.sites:
        from fastapi import HTTPException

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
        from fastapi import HTTPException

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
    )


@app.post("/api/v1/sessions/{session_id}/messages", response_model=MessageResponse)
async def post_message(session_id: str, payload: MessageRequest) -> MessageResponse:
    session = store.get(session_id)
    if session is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="session not found")

    content = payload.content.strip()
    session.messages.append({"role": "user", "content": content, "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()})
    store.touch(session)

    # Phase 0: echo. Phase 1+ replaces with agent.run
    from app.agent_runner import run_agent

    result = await run_agent(session, content)
    return MessageResponse(**result)


@app.post("/api/v1/sessions/{session_id}/resume", response_model=MessageResponse)
async def resume_session(session_id: str, payload: ResumeRequest) -> MessageResponse:
    session = store.get(session_id)
    if session is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="session not found")
    from app.agent_runner import resume_agent

    result = await resume_agent(session, payload.note or "continue")
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
