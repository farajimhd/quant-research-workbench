from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from src.backend.real_live_market_data.models import QuoteEvent, SymbolState, TradeEvent, UniverseRecord, utc_now


def apply_trade(state: SymbolState, event: TradeEvent) -> None:
    state.last_trade = event
    state.last_price = event.price
    state.recent_trades.append(event)
    state.day_volume += max(0.0, event.size)
    state.day_dollar_volume += max(0.0, event.size) * event.price
    state.day_trade_count += 1
    state.bar_1m.update(event.price, event.size)


def apply_quote(state: SymbolState, event: QuoteEvent) -> None:
    state.last_quote = event


def market_status_row(record: UniverseRecord, state: SymbolState, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    quote = state.last_quote
    trade = state.last_trade
    bid = quote.bid_price if quote else 0.0
    ask = quote.ask_price if quote else 0.0
    last_price = state.last_price or record.last_price
    spread = ask - bid if ask > 0 and bid > 0 and ask >= bid else 0.0
    spread_bps = spread / last_price * 10_000 if last_price > 0 and spread > 0 else 0.0
    trades_10s = trades_in_window(state, now, 10)
    trades_60s = trades_in_window(state, now, 60)
    notional_10s = sum(trade_item.price * trade_item.size for trade_item in trades_10s)
    volume_10s = sum(trade_item.size for trade_item in trades_10s)
    trade_rate_10s = len(trades_10s) / 10
    trade_rate_60s = len(trades_60s) / 60
    trade_accel = trade_rate_10s / trade_rate_60s if trade_rate_60s > 0 else (trade_rate_10s * 10 if trade_rate_10s > 0 else 0.0)
    buy_pressure, sell_pressure, tape_imbalance = tape_pressure(trades_10s, quote)
    float_rotation = state.day_volume / record.float_shares if record.float_shares > 0 else 0.0
    price_vs_vwap = last_price / state.bar_1m.vwap - 1 if state.bar_1m.vwap > 0 and last_price > 0 else 0.0
    short_setup = short_setup_label(record, float_rotation, trade_accel, tape_imbalance, price_vs_vwap)
    float_profile = float_profile_label(record.float_shares)
    scanner_score = scanner_score_value(
        trade_accel=trade_accel,
        tape_imbalance=tape_imbalance,
        notional_rate_10s=notional_10s / 10,
        spread_bps=spread_bps,
        float_rotation=float_rotation,
        price_vs_vwap=price_vs_vwap,
        short_setup=short_setup,
    )
    return {
        "ticker": record.ticker,
        "conid": record.conid,
        "bar_time_market": format_market_time(now),
        "current_open": last_price,
        "bid": bid,
        "ask": ask,
        "spread_bps_abs": spread_bps,
        "last_day_current_change_pct": 0.0,
        "last_day_volume_so_far": state.day_volume,
        "last_day_dollar_volume_so_far": state.day_dollar_volume,
        "last_transactions": state.day_trade_count,
        "trade_count_10s": len(trades_10s),
        "trade_count_60s": len(trades_60s),
        "trade_rate_10s": trade_rate_10s,
        "trade_rate_60s": trade_rate_60s,
        "trade_accel_10s_60s": trade_accel,
        "volume_rate_10s": volume_10s / 10,
        "notional_rate_10s": notional_10s / 10,
        "buy_pressure": buy_pressure,
        "sell_pressure": sell_pressure,
        "tape_imbalance": tape_imbalance,
        "quote_pressure": quote_pressure(quote),
        "last_vwap": state.bar_1m.vwap,
        "price_vs_vwap_pct": price_vs_vwap,
        "float_profile": float_profile,
        "float_rotation": float_rotation,
        "short_setup": short_setup,
        "scanner_score": scanner_score,
        "market_state": market_state_label(scanner_score, trade_accel, tape_imbalance, spread_bps),
        "provider": "massive_ws",
        "live_priority": scanner_score,
    }


def signal_row_from_market(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    now = now or utc_now()
    scanner_score = float(row.get("scanner_score") or 0)
    trade_accel = float(row.get("trade_accel_10s_60s") or 0)
    tape_imbalance = float(row.get("tape_imbalance") or 0)
    price_vs_vwap = float(row.get("price_vs_vwap_pct") or 0)
    short_setup = str(row.get("short_setup") or "normal")
    if scanner_score < 45:
        return None
    if trade_accel < 2.0 and tape_imbalance < 0.25 and short_setup not in {"squeeze_watch", "crowded_short"}:
        return None
    signal_type = "trade_acceleration_breakout"
    if short_setup == "squeeze_watch":
        signal_type = "low_float_squeeze_watch"
    elif price_vs_vwap > 0.01 and tape_imbalance > 0.2:
        signal_type = "vwap_reclaim_with_tape"
    return {
        **row,
        "live_signal_id": f"{row.get('ticker')}|{int(now.timestamp())}|{signal_type}",
        "live_signal_query": "Backend Live Scanner",
        "live_signal_time": format_market_time(now),
        "signal_type": signal_type,
        "reason": signal_reason(row),
        "risk_flags": risk_flags(row),
    }


def trades_in_window(state: SymbolState, now: datetime, seconds: int) -> list[TradeEvent]:
    cutoff = now - timedelta(seconds=seconds)
    return [event for event in state.recent_trades if event.ts >= cutoff]


def tape_pressure(trades: list[TradeEvent], quote: QuoteEvent | None) -> tuple[float, float, float]:
    if not trades:
        return 0.0, 0.0, 0.0
    bid = quote.bid_price if quote else 0.0
    ask = quote.ask_price if quote else 0.0
    midpoint = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
    buy_notional = 0.0
    sell_notional = 0.0
    neutral_notional = 0.0
    for trade in trades:
        notional = trade.price * trade.size
        if ask > 0 and trade.price >= ask:
            buy_notional += notional
        elif bid > 0 and trade.price <= bid:
            sell_notional += notional
        elif midpoint > 0 and trade.price >= midpoint:
            buy_notional += notional
        elif midpoint > 0:
            sell_notional += notional
        else:
            neutral_notional += notional
    total = buy_notional + sell_notional + neutral_notional
    if total <= 0:
        return 0.0, 0.0, 0.0
    return buy_notional / total, sell_notional / total, (buy_notional - sell_notional) / total


def quote_pressure(quote: QuoteEvent | None) -> float:
    if not quote:
        return 0.0
    total = quote.bid_size + quote.ask_size
    if total <= 0:
        return 0.0
    return (quote.bid_size - quote.ask_size) / total


def short_setup_label(record: UniverseRecord, float_rotation: float, trade_accel: float, tape_imbalance: float, price_vs_vwap: float) -> str:
    if record.short_interest <= 0 and record.short_volume <= 0:
        return "unknown"
    short_interest_pct = record.short_interest / record.float_shares if record.float_shares > 0 else 0.0
    if short_interest_pct >= 0.12 and float_rotation >= 0.03 and trade_accel >= 2.0 and tape_imbalance > 0.15 and price_vs_vwap >= 0:
        return "squeeze_watch"
    if short_interest_pct >= 0.12:
        return "crowded_short"
    if record.short_volume > 0 and tape_imbalance < -0.15 and price_vs_vwap < 0:
        return "short_resistance"
    return "normal"


def float_profile_label(float_shares: float) -> str:
    if float_shares <= 0:
        return "unknown"
    if float_shares < 10_000_000:
        return "micro_float"
    if float_shares < 50_000_000:
        return "low_float"
    if float_shares < 250_000_000:
        return "mid_float"
    return "large_float"


def scanner_score_value(*, trade_accel: float, tape_imbalance: float, notional_rate_10s: float, spread_bps: float, float_rotation: float, price_vs_vwap: float, short_setup: str) -> float:
    score = 10.0
    score += min(30.0, trade_accel * 8)
    score += min(20.0, max(0.0, tape_imbalance) * 35)
    score += min(20.0, notional_rate_10s / 50_000)
    score += min(15.0, float_rotation * 120)
    score += min(10.0, max(0.0, price_vs_vwap) * 400)
    if short_setup == "squeeze_watch":
        score += 15
    elif short_setup == "crowded_short":
        score += 6
    if spread_bps > 250:
        score -= 25
    elif spread_bps > 100:
        score -= 10
    return round(max(0.0, score), 3)


def market_state_label(scanner_score: float, trade_accel: float, tape_imbalance: float, spread_bps: float) -> str:
    if spread_bps > 250:
        return "wide_spread"
    if scanner_score >= 70:
        return "ignition"
    if scanner_score >= 45 and trade_accel >= 2:
        return "warming"
    if tape_imbalance < -0.25:
        return "selling_pressure"
    return "watch"


def signal_reason(row: dict[str, Any]) -> str:
    return " | ".join(
        part
        for part in [
            f"score {row.get('scanner_score')}",
            f"accel {row.get('trade_accel_10s_60s')}",
            f"tape {row.get('tape_imbalance')}",
            str(row.get("short_setup") or ""),
        ]
        if part
    )


def risk_flags(row: dict[str, Any]) -> str:
    flags: list[str] = []
    if float(row.get("spread_bps_abs") or 0) > 100:
        flags.append("wide_spread")
    if str(row.get("float_profile") or "") in {"micro_float", "unknown"}:
        flags.append("float_risk")
    return ",".join(flags)


def format_market_time(value: datetime) -> str:
    return value.astimezone().strftime("%H:%M:%S")
