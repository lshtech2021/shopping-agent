from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=4000)
    sites: list[Literal["jd", "ebay"]] = Field(default_factory=lambda: ["jd"])


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str
    requirement: str
    sites: list[str]


class MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class RecommendationOut(BaseModel):
    product_id: str
    title: str
    reason: str
    url: Optional[str] = None
    price: Optional[float] = None
    currency: Optional[str] = None


class MessageResponse(BaseModel):
    session_id: str
    status: str
    reply: str
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    interrupted: bool = False
    interrupt_reason: Optional[str] = None


class SessionSummary(BaseModel):
    session_id: str
    requirement: str
    sites: list[str]
    status: str
    product_count: int = 0
    recommendation_count: int = 0
    updated_at: str
    created_at: str


class SessionDetail(BaseModel):
    session_id: str
    status: str
    requirement: str
    sites: list[str]
    requirements: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    products: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    searched_sites: list[str] = Field(default_factory=list)
    interrupt_reason: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ResumeRequest(BaseModel):
    note: str = ""
