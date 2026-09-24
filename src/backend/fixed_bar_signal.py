"""Read-only, completed-bar Early Squeeze admission for fixed Backtests.

The approved 100 ms impulse is evaluated in ClickHouse over the pinned bar
attempts. A sparse causal pass applies the live episode expiry rule to every
qualifying impulse; no event stream, producer job, or signal artifact is created.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import timedelta
from typing import Any, Mapping

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, _literal, _unit_map, assert_select_only,
    market_day_boundary,
)


CONTRACT = "arte-completed-100ms-squeeze-start-v1"
RULE_ID = "watchlist-squeeze-early-impulse-100ms"
STREAM_ID = "price-squeeze-early"
MAX_BACKTEST_EPISODES = 1_000_000
_CONDITIONS = {
    ("price_change_1_bar_pct", "greater_or_equal", 0.05),
    ("trade_count_change", "greater_than", 0.0),
    ("volume_change", "greater_than", 0.0),
}


def validate_stream(stream: Mapping[str, Any], activation: Mapping[str, Any]) -> None:
    """Fail closed if the saved scanner contract no longer matches this SQL."""
    if (stream.get("signal_stream_id") != STREAM_ID
            or stream.get("occurrence_source") != "qmd_squeeze_episode"
            or stream.get("episode_role") != "start"
            or int(stream.get("episode_ttl_ms") or 0) != 300_000
            or list(stream.get("inclusion_rule_sets") or []) != [RULE_ID]
            or stream.get("exclusion_rule_sets")
            or stream.get("trigger_policy") != "false_to_true"
            or stream.get("rearm_policy") != "after_false"
            or int(stream.get("cooldown_ms") or 0) != 0):
        raise ValueError("Fixed bar Early Squeeze stream differs from the supported saved contract")
    rules = [row for row in activation.get("rule_sets") or ()
             if row.get("rule_set_id") == RULE_ID]
    if len(rules) != 1 or rules[0].get("operator") != "all":
        raise ValueError("Fixed bar Early Squeeze rule set is missing or changed")
    actual = set()
    for condition in rules[0].get("conditions") or ():
        interval = condition.get("left_interval") or {}
        if (condition.get("enabled") is False or condition.get("right_source_id")
                or interval != {"value": 100, "unit": "milliseconds"}):
            raise ValueError("Fixed bar Early Squeeze condition uses unsupported evidence")
        actual.add((condition.get("left_source_id"), condition.get("comparator"),
                    float(condition.get("value"))))
    if actual != _CONDITIONS or len(rules[0].get("conditions") or ()) != len(_CONDITIONS):
        raise ValueError("Fixed bar Early Squeeze thresholds differ from the saved rule set")


def candidate_projection_tickers(
    configuration: Mapping[str, Any], occurrences: list[Mapping[str, Any]],
    *, has_core_signal_plans: bool = False,
) -> tuple[str, ...] | None:
    """Only the sole source-native stream may narrow later market computation.

    The scanner still evaluates the complete certified universe. This is a
    necessary-condition prune, never early activation or a trading decision.
    """
    run_plan = dict(configuration.get("run_plan") or {})
    streams = [row for row in dict(configuration.get("signal_activation") or {}).get("signal_streams") or ()
               if bool(row.get("enabled", True))]
    if (has_core_signal_plans or configuration.get("assignments")
            or len(streams) != 1 or streams[0].get("signal_stream_id") != STREAM_ID
            or list(run_plan.get("signal_stream_ids") or ()) != [STREAM_ID]
            or dict(run_plan.get("activation") or {}).get("watchlist_policy") != "not_required"):
        return None
    return tuple(sorted({str(row.get("ticker") or "").upper() for row in occurrences
                         if str(row.get("ticker") or "").strip()}))


def first_squeeze_sql(plan: CertifiedMarketDayPlan, *, through_boundary_ms: int) -> str:
    if (type(through_boundary_ms) is not int or not 0 < through_boundary_ms <= 57_600_000
            or through_boundary_ms % 100):
        raise ValueError("Squeeze boundary must be a positive completed 100ms clock")
    bars = _unit_map(plan, "bars")
    if not bars or set(bars) != {(day, ticker) for day in plan.sessions for ticker in plan.tickers}:
        raise ValueError("Squeeze scan requires every pinned ticker-day bar attempt")
    attempts = ",".join(
        f"(toDate({_literal(day)}),{_literal(ticker)},toUUID({_literal(unit.attempt_id)}))"
        for (day, ticker), unit in sorted(bars.items())
    )
    return assert_select_only(f"""
      WITH ordered AS (
        SELECT session_date,ticker,bucket_index,open_int,close_int,volume,trade_count,
          lagInFrame(close_int,1,toUInt64(0)) OVER w AS previous_close_int,
          lagInFrame(volume,1,0.) OVER w AS previous_volume,
          lagInFrame(trade_count,1,toUInt64(0)) OVER w AS previous_trade_count
        FROM arte.bars_v1
        WHERE build_id={_literal(plan.build_id)}
          AND (session_date,ticker,attempt_id) IN ({attempts})
          AND resolution_ms=100 AND price_valid=1
          AND bucket_index>={SESSION_OPEN_OFFSET_MS // 100}
          AND bucket_index<{(through_boundary_ms + SESSION_OPEN_OFFSET_MS) // 100}
        WINDOW w AS (PARTITION BY session_date,ticker ORDER BY bucket_index
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
      ), candidates AS (
        SELECT * FROM ordered
        WHERE previous_close_int>0
          AND (toFloat64(close_int)/previous_close_int-1)*100>=0.05
          AND trade_count>previous_trade_count AND volume>previous_volume
      )
      SELECT session_date,ticker,bucket_index,open_int,close_int,volume,trade_count,
        previous_close_int,previous_volume,previous_trade_count
      FROM candidates ORDER BY session_date,ticker,bucket_index FORMAT JSONEachRow
    """)


def load_first_squeeze_occurrences(
    plan: CertifiedMarketDayPlan, *, stream: Mapping[str, Any],
    activation: Mapping[str, Any], through_boundary_ms: int, client: Any,
) -> dict[str, Any]:
    validate_stream(stream, activation)
    query = first_squeeze_sql(plan, through_boundary_ms=through_boundary_ms)
    rows = list(client.iter_json_each_row(query))
    # maximum_events is a display/query setting in the saved stream, not an
    # all-session producer cap.  The live engine does not suppress new starts
    # after it, so Backtest must not truncate or reject at that threshold.
    last_bucket: dict[tuple[str, str], int] = {}
    episode_expires: dict[tuple[str, str], int] = {}
    occurrences: list[dict[str, Any]] = []
    for row in rows:
        day, ticker = str(row["session_date"]), str(row["ticker"])
        bucket = int(row["bucket_index"])
        key = (day, ticker)
        close_int, previous_int = int(row["close_int"]), int(row["previous_close_int"])
        volume, previous_volume = float(row["volume"]), float(row["previous_volume"])
        trades, previous_trades = int(row["trade_count"]), int(row["previous_trade_count"])
        if (day not in plan.sessions or ticker not in plan.tickers
                or not SESSION_OPEN_OFFSET_MS // 100 <= bucket
                       < (through_boundary_ms + SESSION_OPEN_OFFSET_MS) // 100
                or bucket <= last_bucket.get(key, -1)
                or close_int <= 0 or previous_int <= 0
                or not math.isfinite(volume) or not math.isfinite(previous_volume)
                or (close_int / previous_int - 1) * 100 < 0.05
                or volume <= previous_volume or trades <= previous_trades):
            raise ValueError("Completed-bar squeeze query returned invalid or unordered evidence")
        last_bucket[key] = bucket
        # The QMD episode engine removes an episode at its expiry before
        # checking the current impulse.  A qualifying bar inside an active
        # episode is not a second start, even if the rule remains true.
        if bucket < episode_expires.get(key, -1):
            continue
        episode_expires[key] = bucket + 3_000  # 300 seconds at 100 ms.
        at = market_day_boundary(day, (bucket + 1) * 100 - SESSION_OPEN_OFFSET_MS)
        price, anchor = close_int / 10_000, previous_int / 10_000
        move = (price / anchor - 1) * 100
        identity = f"{plan.token}:{STREAM_ID}:{day}:{ticker}:{bucket}"
        event_id = hashlib.sha256(identity.encode()).hexdigest()
        expires = at + timedelta(milliseconds=300_000)
        occurrences.append({
            "event_id": event_id, "signal_stream_id": STREAM_ID, "ticker": ticker,
            "event_time": at.isoformat(), "effective_at": at.isoformat(),
            "available_at": at.isoformat(), "last_price": price,
            "squeeze_episode_id": event_id, "squeeze_episode_role": "start",
            "squeeze_episode_started_at": at.isoformat(),
            "squeeze_expires_at": expires.isoformat(),
            "squeeze_anchor_price": anchor, "squeeze_move_pct": move,
            "squeeze_high_water_pct": max(0., move),
            "source_authority": CONTRACT,
            "market_plan_token": plan.token,
            "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
            "evidence": {
                "price_change_1_bar_pct": move,
                "trade_count_change": trades - previous_trades,
                "volume_change": volume - previous_volume,
            },
        })
        if len(occurrences) > MAX_BACKTEST_EPISODES:
            raise ValueError("Completed-bar squeeze occurrences exceed the Backtest safety bound")
    body = json.dumps(occurrences, sort_keys=True, separators=(",", ":"))
    return {
        "occurrences": occurrences,
        "authority": {
            "authority": CONTRACT, "market_plan_token": plan.token,
            "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
            "row_count": len(occurrences),
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        },
    }
