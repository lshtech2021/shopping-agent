"""Shopping-domain state and output shapes."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ShoppingState(BaseModel):
    requirements: dict[str, Any] = Field(default_factory=dict)
    sites: list[str] = Field(default_factory=list)
    products: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ShoppingOutput(BaseModel):
    reply: str
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
