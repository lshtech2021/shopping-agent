"""Action Broker and tool-schema HTTP surface."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.browser.action_broker import Action, ActionBroker
from app.config import settings
from app.domains.shopping.tools import build_shopping_tools
from app.store import store
from app.tools import ToolBox

router = APIRouter(prefix="/api/v1")
broker = ActionBroker(settings.database_url or "memory")


class CreateActionRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ObservationRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


def _check_token(token: str | None) -> None:
    if settings.extension_token and token != settings.extension_token:
        raise HTTPException(status_code=403, detail="extension token required")


@router.post("/runs/{run_id}/actions", response_model=Action)
async def create_action(
    run_id: str,
    payload: CreateActionRequest,
    x_extension_token: str | None = Header(default=None),
) -> Action:
    _check_token(x_extension_token)
    return await broker.create_action(run_id, payload.tool, payload.args)


@router.get("/runs/{run_id}/actions/next", response_model=Action | None)
async def next_action(
    run_id: str,
    x_extension_token: str | None = Header(default=None),
) -> Action | None:
    _check_token(x_extension_token)
    return await broker.next_action(run_id)


@router.post("/actions/{action_id}/observations")
async def submit_observation(
    action_id: str,
    payload: ObservationRequest,
    x_extension_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_token(x_extension_token)
    try:
        return await broker.submit_observation(action_id, payload.payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown action") from exc


@router.get("/sessions/{session_id}/tools")
def session_tools(
    session_id: str,
    x_extension_token: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    _check_token(x_extension_token)
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    specs = build_shopping_tools(ToolBox(session), {})
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.args_model.model_json_schema(),
        }
        for spec in specs
    ]
