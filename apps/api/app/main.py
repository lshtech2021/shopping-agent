"""Shopping Agent API."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import llm, metrics
from app.api.actions import router as actions_router
from app.auth import (
    extension_auth_enabled,
    require_extension_token_header,
    tokens_match,
)
from app.config import settings
from app.currency import is_supported_currency, normalize_currency
from app.logging_setup import setup_logging
from app.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    MessageRequest,
    MessageResponse,
    PatchSessionSettingsRequest,
    PatchSessionSettingsResponse,
    ResumeRequest,
    RunEventList,
    RunEventOut,
    SessionDetail,
    SessionSummary,
)
from app.store import ALLOWED_SITES, store
from app.trace import log_event
from app.ws_hub import hub, origin_allowed

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    store.reset(settings.database_url)
    if settings.log_retention_days > 0:
        try:
            pruned = store.prune_logs(settings.log_retention_days)
            logger.info(
                "pruned old audit logs retention_days=%s run_events=%s artifacts=%s",
                settings.log_retention_days,
                pruned["run_events"],
                pruned["artifacts"],
            )
        except Exception:  # noqa: BLE001 — retention is best effort
            logger.exception("failed to prune old audit logs")
    from app.agent.supervisor import RunSupervisor

    supervisor = RunSupervisor(store)
    supervisor.reconcile_startup()
    sweep = asyncio.create_task(_sweep_loop(supervisor))
    try:
        yield
    finally:
        sweep.cancel()


async def _sweep_loop(supervisor) -> None:
    try:
        while True:
            await asyncio.sleep(30)
            supervisor.sweep_expired()
    except asyncio.CancelledError:
        return


app = FastAPI(title=settings.app_name, version="0.5.0", lifespan=lifespan)
app.include_router(actions_router)
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


@asynccontextmanager
async def _turn_slot(session_id: str):
    from app.agent.supervisor import RunSupervisor

    supervisor = RunSupervisor(store)
    owner = supervisor.acquire(session_id, ttl_seconds=120)
    if owner is None:
        raise HTTPException(status_code=409, detail="上一轮仍在执行，请稍候")
    lock = _lock_for(session_id)
    if lock.locked():
        supervisor.release(session_id, owner)
        raise HTTPException(status_code=409, detail="上一轮仍在执行，请稍候")
    if not store.try_turn_lock(session_id):
        supervisor.release(session_id, owner)
        raise HTTPException(status_code=409, detail="上一轮仍在执行，请稍候")
    heartbeat = asyncio.create_task(_heartbeat_loop(session_id, owner, supervisor))
    try:
        async with lock:
            yield
    finally:
        heartbeat.cancel()
        store.release_turn_lock(session_id)
        supervisor.release(session_id, owner)


async def _heartbeat_loop(session_id: str, owner: str, supervisor) -> None:
    try:
        while True:
            await asyncio.sleep(30)
            supervisor.heartbeat(session_id, owner)
    except asyncio.CancelledError:
        return


def _receipt_key(
    endpoint: str,
    session_id: str,
    header_key: str | None,
    client_id: str | None,
) -> str | None:
    value = (header_key or client_id or "").strip()
    return f"{endpoint}:{session_id}:{value}" if value else None


def _require_session(session_id: str):
    session = store.refresh(session_id)
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


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics() -> PlainTextResponse:
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="metrics disabled")
    return PlainTextResponse(
        metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4",
    )


@app.get("/api/v1/sessions", response_model=list[SessionSummary])
def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    _token: None = Depends(require_extension_token_header),
) -> list[SessionSummary]:
    return [SessionSummary(**item) for item in store.list_summaries(limit=limit)]


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
def create_session(
    payload: CreateSessionRequest,
    _token: None = Depends(require_extension_token_header),
) -> CreateSessionResponse:
    currency = None
    currency_source = None
    if payload.currency is not None:
        currency = normalize_currency(payload.currency)
        if not currency or not is_supported_currency(currency):
            raise HTTPException(status_code=422, detail="invalid currency")
        currency_source = payload.currency_source or "manual"
    session = store.create(
        payload.requirement,
        payload.site,
        currency=currency,
        currency_source=currency_source,
    )
    shipping_changed = False
    for field in ("max_shipping_ratio", "max_shipping_cost"):
        if field in payload.model_fields_set:
            session.settings[field] = getattr(payload, field)
            shipping_changed = True
    if shipping_changed:
        store.touch(session, durable=True)
    log_event(
        session,
        "创建会话",
        stage="api.create_session",
        source="api",
        data={
            "requirement": payload.requirement,
            "site": payload.site,
            "currency": session.settings.get("currency"),
            "max_shipping_ratio": session.settings.get("max_shipping_ratio"),
            "max_shipping_cost": session.settings.get("max_shipping_cost"),
        },
        persist=True,
    )
    return CreateSessionResponse(
        session_id=session.id,
        status=session.status,
        requirement=session.requirement,
        sites=session.sites,
        settings=session.settings,
    )


@app.get("/api/v1/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str,
    _token: None = Depends(require_extension_token_header),
) -> dict:
    record = store.get_artifact(artifact_id)
    if record is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return record


@app.get("/api/v1/sessions/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    _token: None = Depends(require_extension_token_header),
) -> SessionDetail:
    session = store.snapshot(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionDetail(
        session_id=session.id,
        status=session.status,
        requirement=session.requirement,
        sites=session.sites,
        settings=session.settings,
        requirements=session.requirements,
        plan=session.plan,
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


@app.patch(
    "/api/v1/sessions/{session_id}/settings",
    response_model=PatchSessionSettingsResponse,
)
def patch_session_settings(
    session_id: str,
    payload: PatchSessionSettingsRequest,
    _token: None = Depends(require_extension_token_header),
) -> PatchSessionSettingsResponse:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    settings_before = dict(session.settings)
    budget_cleared = False
    recommendations_cleared = False
    if payload.site is not None:
        if payload.site not in ALLOWED_SITES:
            raise HTTPException(status_code=422, detail="invalid site")
        site_changed = payload.site != session.settings.get("site")
        session.settings["site"] = payload.site
        session.sites = [payload.site]
        if site_changed:
            session.plan = None
            session.searched_sites.clear()
            session.recommendations.clear()
    if payload.currency is not None:
        currency = normalize_currency(payload.currency)
        if not currency or not is_supported_currency(currency):
            raise HTTPException(status_code=422, detail="invalid currency")
        old_currency = normalize_currency(session.settings.get("currency"))
        if currency != old_currency:
            requirements = dict(session.requirements or {})
            if (
                requirements.get("budget") is not None
                or requirements.get("budget_min") is not None
            ):
                requirements["budget"] = None
                requirements["budget_min"] = None
                requirements["budget_currency"] = None
                session.requirements = requirements
                budget_cleared = True
            session.plan = None
            session.searched_sites.clear()
            session.recommendations.clear()
        session.settings["currency"] = currency
        session.settings["currency_source"] = payload.currency_source or "manual"
    shipping_changed = False
    for field in ("max_shipping_ratio", "max_shipping_cost"):
        if field in payload.model_fields_set:
            session.settings[field] = getattr(payload, field)
            shipping_changed = True
    if shipping_changed and session.recommendations:
        session.recommendations.clear()
        recommendations_cleared = True
    if payload.clear_products:
        session.products.clear()
        session.recommendations.clear()
    settings_changes = {
        key: {
            "before": settings_before.get(key),
            "after": session.settings.get(key),
        }
        for key in set(settings_before) | set(session.settings)
        if settings_before.get(key) != session.settings.get(key)
    }
    store.touch(session, durable=True)
    log_event(
        session,
        "更新会话设置",
        stage="api.patch_session_settings",
        source="api",
        data={
            "changes": settings_changes,
            "site": session.settings.get("site"),
            "currency": session.settings.get("currency"),
            "currency_source": session.settings.get("currency_source"),
            "max_shipping_ratio": session.settings.get("max_shipping_ratio"),
            "max_shipping_cost": session.settings.get("max_shipping_cost"),
            "clear_products": payload.clear_products,
            "budget_cleared": budget_cleared,
            "recommendations_cleared": recommendations_cleared,
        },
        persist=True,
    )
    return PatchSessionSettingsResponse(
        session_id=session.id,
        status=session.status,
        sites=session.sites,
        settings=session.settings,
        budget_cleared=budget_cleared,
        recommendations_cleared=recommendations_cleared,
    )


@app.get(
    "/api/v1/sessions/{session_id}/events",
    response_model=RunEventList,
)
async def list_session_events(
    session_id: str,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    _token: None = Depends(require_extension_token_header),
) -> RunEventList:
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="session not found")
    rows = store.list_run_events(session_id, after_id=after, limit=limit)
    events = [
        RunEventOut(
            id=int(row["id"]),
            run_id=str(row["run_id"]),
            event=dict(row.get("event") or {}),
            created_at=str(row.get("created_at") or ""),
        )
        for row in rows
    ]
    next_after = events[-1].id if events else after
    return RunEventList(
        session_id=session_id, events=events, next_after=next_after
    )


@app.post("/api/v1/sessions/{session_id}/messages", response_model=MessageResponse)
async def post_message(
    session_id: str,
    payload: MessageRequest,
    _token: None = Depends(require_extension_token_header),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
) -> MessageResponse:
    session = _require_session(session_id)
    key = _receipt_key(
        "message", session_id, idempotency_key, payload.client_message_id
    )
    if key:
        cached = store.get_receipt(key)
        if cached is not None:
            return MessageResponse(**cached["response"])
    async with _turn_slot(session_id):
        from app.agent_runner import run_agent

        session = _require_session(session_id)
        content = payload.content.strip()
        log_event(
            session,
            "收到用户消息",
            stage="api.message",
            source="api",
            data={"content": content[:200]},
            persist=False,
        )
        session.messages.append(
            {
                "role": "user",
                "content": content,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        result = await run_agent(session, content)
        if key:
            store.save_receipt(key, session_id, "message", result)
        return MessageResponse(**result)


@app.post("/api/v1/sessions/{session_id}/resume", response_model=MessageResponse)
async def resume_session(
    session_id: str,
    payload: ResumeRequest,
    _token: None = Depends(require_extension_token_header),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key"
    ),
) -> MessageResponse:
    session = _require_session(session_id)
    key = _receipt_key(
        "resume", session_id, idempotency_key, payload.client_message_id
    )
    if key:
        cached = store.get_receipt(key)
        if cached is not None:
            return MessageResponse(**cached["response"])
    if session.status not in {"interrupted", "blocked"}:
        raise HTTPException(
            status_code=409,
            detail="session is not interrupted; resume is not allowed",
        )
    async with _turn_slot(session_id):
        from app.agent_runner import resume_agent

        session = _require_session(session_id)
        result = await resume_agent(session, payload.note or "continue")
        if key:
            store.save_receipt(key, session_id, "resume", result)
        return MessageResponse(**result)


@app.websocket("/ws/extension")
async def extension_ws(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if extension_auth_enabled() and (
        not settings.extension_token
        or not tokens_match(token, settings.extension_token)
    ):
        await websocket.close(code=4401)
        return
    origin = websocket.headers.get("origin")
    if not origin_allowed(origin, settings.extension_id or None):
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
