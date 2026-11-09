from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CreateSessionRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=4000)
    sites: list[Literal["jd", "ebay"]] = Field(default_factory=lambda: ["jd"])


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str
    requirement: str
    sites: list[str]


class ClarifyQuestion(BaseModel):
    id: str = Field(description="问题标识，如 budget / category / budget_ram_conflict")
    field: Literal[
        "budget",
        "category",
        "use_case",
        "must_have",
        "preferences",
        "other",
    ] = "other"
    question: str
    options: list[str] = Field(default_factory=list, description="候选项；用户始终可自由输入")


class MessageRequest(BaseModel):
    content: str = Field(default="", max_length=4000)
    answers: Optional[dict[str, str]] = Field(
        None, description="对上一轮 clarify 问题的回答：question.id -> 用户选择/输入"
    )
    client_message_id: Optional[str] = Field(
        None, max_length=200, description="客户端幂等键；重复请求返回原始响应"
    )

    @model_validator(mode="after")
    def _require_content_or_answers(self) -> "MessageRequest":
        if not (self.content.strip() or (self.answers or {})):
            raise ValueError("content 或 answers 至少提供一个")
        return self


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
    clarify: list[ClarifyQuestion] = Field(default_factory=list)
    plan: Optional[dict[str, Any]] = None
    execution_logs: list[dict[str, Any]] = Field(default_factory=list)


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
    plan: Optional[dict[str, Any]] = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    products: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    searched_sites: list[str] = Field(default_factory=list)
    interrupt_reason: Optional[str] = None
    pending_questions: list[dict[str, Any]] = Field(default_factory=list)
    blocked_sites: dict[str, str] = Field(default_factory=dict)
    execution_logs: list[dict[str, Any]] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ResumeRequest(BaseModel):
    note: str = ""
    client_message_id: Optional[str] = Field(None, max_length=200)
