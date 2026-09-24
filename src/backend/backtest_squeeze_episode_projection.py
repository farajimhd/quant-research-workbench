"""Closed typed journal contract for completed-bar Early Squeeze starts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from src.backend.fixed_bar_signal import CONTRACT, STREAM_ID
from src.trading_runtime.journal_contract import JournalRecord


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KEYS = frozenset({
    "event_id", "signal_stream_id", "ticker", "event_time", "effective_at",
    "available_at", "last_price", "squeeze_episode_id", "squeeze_episode_role",
    "squeeze_episode_started_at", "squeeze_expires_at", "squeeze_anchor_price",
    "squeeze_move_pct", "squeeze_high_water_pct", "source_authority",
    "market_plan_token", "query_sha256", "evidence",
})
_EVIDENCE = frozenset({"price_change_1_bar_pct", "trade_count_change", "volume_change"})


def _clock(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Squeeze clock must be typed ISO text")
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("Squeeze clock lacks timezone")
    return at.astimezone(timezone.utc)


def _decimal(value: Any, *, positive: bool = False) -> str:
    if type(value) not in (int, float, Decimal):
        raise ValueError("Squeeze measure has invalid scalar type")
    try:
        number = Decimal(str(value))
        quantized = number.quantize(Decimal("0.000000000000000001"))
    except InvalidOperation as exc:
        raise ValueError("Squeeze measure exceeds typed precision") from exc
    if not number.is_finite() or number != quantized or (positive and number <= 0):
        raise ValueError("Squeeze measure is invalid or not lossless")
    if abs(number) >= Decimal(10) ** 20:
        raise ValueError("Squeeze measure exceeds Decimal(38,18)")
    return format(quantized, "f")


def project_fixed_squeeze_episode(
    record: JournalRecord, *, expected_market_plan_token: str,
    expected_query_sha256: str,
) -> dict[str, Any]:
    """Reject every non-completed-bar occurrence and every unmodeled field."""
    payload = dict(record.payload)
    if (record.category, record.entity_type) != ("market_discovery_signal", "signal_occurrence"):
        raise ValueError("Not a squeeze occurrence")
    lineage = {"correlation_id", "causation_id"}
    if (set(payload) - lineage != _KEYS
            or (set(payload) & lineage not in (set(), lineage))
            or any(not isinstance(payload[key], str) or not payload[key]
                   for key in set(payload) & lineage)
            or not isinstance(payload["evidence"], dict)
            or set(payload["evidence"]) != _EVIDENCE):
        raise ValueError("Squeeze occurrence has missing or unmodeled evidence")
    if (payload["signal_stream_id"] != STREAM_ID or payload["source_authority"] != CONTRACT
            or payload["squeeze_episode_role"] != "start"
            or payload["event_id"] != record.entity_id
            or payload["squeeze_episode_id"] != record.entity_id
            or not isinstance(record.entity_id, str) or _HEX.fullmatch(record.entity_id) is None
            or payload["ticker"] != str(payload["ticker"]).upper()
            or not payload["ticker"]
            or payload["market_plan_token"] != expected_market_plan_token
            or payload["query_sha256"] != expected_query_sha256
            or _HEX.fullmatch(str(expected_market_plan_token)) is None
            or _HEX.fullmatch(str(expected_query_sha256)) is None
            or record.account_id):
        raise ValueError("Squeeze occurrence lacks fixed source identity")
    at = _clock(payload["available_at"])
    if (record.event_time.tzinfo is None
            or at != record.event_time.astimezone(timezone.utc)
            or any(_clock(payload[key]) != at for key in (
                "event_time", "effective_at", "squeeze_episode_started_at"))
            or _clock(payload["squeeze_expires_at"]) != at + timedelta(seconds=300)):
        raise ValueError("Squeeze episode clocks differ from completed bar")
    evidence = payload["evidence"]
    move = _decimal(payload["squeeze_move_pct"], positive=True)
    if (_decimal(evidence["price_change_1_bar_pct"]) != move
            or type(evidence["trade_count_change"]) is not int
            or evidence["trade_count_change"] <= 0
            or evidence["trade_count_change"] >= 2**64):
        raise ValueError("Squeeze evidence differs from episode")
    high = _decimal(payload["squeeze_high_water_pct"], positive=True)
    if high != move:
        raise ValueError("Squeeze start high-water differs from move")
    return {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": at.date().replace(day=1).isoformat(),
        "account_id": "", "episode_id": record.entity_id,
        "signal_stream_id": STREAM_ID, "ticker": payload["ticker"],
        "market_plan_token": expected_market_plan_token,
        "query_sha256": expected_query_sha256,
        "source_authority": CONTRACT,
        "episode_started_at": at.isoformat(),
        "expires_at": _clock(payload["squeeze_expires_at"]).isoformat(),
        "last_price": _decimal(payload["last_price"], positive=True),
        "anchor_price": _decimal(payload["squeeze_anchor_price"], positive=True),
        "move_pct": move, "high_water_pct": high,
        "trade_count_change": evidence["trade_count_change"],
        "volume_change": _decimal(evidence["volume_change"], positive=True),
    }
