from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str
    content: str


class InferenceRequest(BaseModel):
    route: str
    idempotency_key: str = Field(min_length=8, max_length=256)
    messages: list[Message] = Field(min_length=1)
    response_schema: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class InferenceResponse(BaseModel):
    status: str
    route: str
    idempotency_key: str
    provider: str
    model: str
    result: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    cached: bool = False
