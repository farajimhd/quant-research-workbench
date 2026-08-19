from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


RuntimeMode = Literal["live", "paper", "replay", "backtest", "backtest_debug"]
TriggerMode = Literal["auto", "manual"]


class ScopeRequest(BaseModel):
    mode: RuntimeMode
    trigger_mode: TriggerMode = "auto"
    tickers: list[str] = Field(default_factory=list, max_length=25_000)
    watchlist_ids: list[str] = Field(default_factory=list)
    clock_us: int | None = Field(default=None, ge=1)
    revision: int = Field(default=1, ge=1)
    ttl_ms: int = Field(default=30_000, ge=1_000, le=86_400_000)
    source: str = "application"

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: list[str]) -> list[str]:
        return sorted({str(value).strip().upper() for value in values if str(value).strip()})


class RawBarInput(BaseModel):
    ticker: str
    view: str
    bar_start_us: int = Field(ge=1)
    bar_end_us: int = Field(ge=1)
    available_at_us: int = Field(ge=1)
    values: list[float]
    revision: int = Field(default=1, ge=1)
    source: str
    source_revision: str = ""

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        result = value.strip().upper()
        if not result:
            raise ValueError("ticker cannot be empty")
        return result


class BarBatchRequest(BaseModel):
    scope_id: str = "live"
    bars: list[RawBarInput] = Field(min_length=1, max_length=100_000)


class InferenceRequest(BaseModel):
    scope_id: str = ""
    tickers: list[str] = Field(default_factory=list, max_length=25_000)
    model_ids: list[str] = Field(default_factory=list)
    origin_us: int | None = Field(default=None, ge=1)
    request_id: str = ""

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: list[str]) -> list[str]:
        return sorted({str(value).strip().upper() for value in values if str(value).strip()})


class FeatureUpdate(BaseModel):
    schema_version: int = 1
    producer: str = "bar_gpt"
    ticker: str
    event_at_us: int
    available_at_us: int
    model_id: str
    model_version: str
    checkpoint_hash: str
    prediction_id: str
    fields: dict[str, float | int | str | bool | None]
    raw: dict[str, Any]
