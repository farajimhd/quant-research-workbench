from __future__ import annotations

import math
import statistics
from datetime import datetime, time
from typing import Any


MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
PNL_SEGMENTS = ("premarket", "market_open", "after_market")


def parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def duration_seconds(start, end) -> float:
    start_dt = parse_dt(start)
    end_dt = parse_dt(end)
    if start_dt is None or end_dt is None:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


def format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, sec = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


def safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def safe_std(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def max_consecutive(flags: list[bool], target: bool) -> int:
    best = current = 0
    for flag in flags:
        if flag == target:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def max_drawdown(equity_values: list[float]) -> tuple[float, float]:
    peak = None
    max_dd_pct = 0.0
    max_dd_abs = 0.0
    for equity in equity_values:
        if peak is None or equity > peak:
            peak = equity
        if peak and peak > 0:
            dd_abs = peak - equity
            dd_pct = dd_abs / peak
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
                max_dd_abs = dd_abs
    return max_dd_pct, max_dd_abs


def drawdown_recovery_periods(equity_values: list[float]) -> int:
    peak = None
    trough_index = None
    worst_dd = 0.0
    recovery = 0
    for index, equity in enumerate(equity_values):
        if peak is None or equity > peak:
            peak = equity
        if peak and peak > 0:
            dd = (peak - equity) / peak
            if dd > worst_dd:
                worst_dd = dd
                trough_index = index
                recovery = 0
        if trough_index is not None and equity >= peak:
            recovery = index - trough_index
    return recovery


def annualized_return(start_equity: float, end_equity: float, start, end) -> float:
    start_dt = parse_dt(start)
    end_dt = parse_dt(end)
    if start_equity <= 0 or start_dt is None or end_dt is None:
        return 0.0
    days = max((end_dt - start_dt).total_seconds() / 86400.0, 1.0)
    return (end_equity / start_equity) ** (365.0 / days) - 1.0


def return_series(portfolio_rows: list[dict]) -> list[float]:
    returns = []
    prior = None
    for row in portfolio_rows:
        equity = float(row.get("equity") or 0.0)
        if prior and prior > 0:
            returns.append((equity / prior) - 1.0)
        prior = equity
    return returns


def daily_return_series(daily_rows: list[dict]) -> list[float]:
    return [float(row.get("return_pct") or 0.0) for row in daily_rows]


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, int(math.floor(q * (len(sorted_values) - 1)))))
    return sorted_values[index]


def pnl_segment_for_timestamp(timestamp: datetime) -> str:
    local_time = timestamp.timetz().replace(tzinfo=None)
    if local_time < MARKET_OPEN:
        return "premarket"
    if local_time < MARKET_CLOSE:
        return "market_open"
    return "after_market"


def add_to_bucket(bucket: dict[str, float], key: str, value: float) -> None:
    bucket[key] = bucket.get(key, 0.0) + value


def portfolio_pnl_breakdown(initial_cash: float, portfolio_rows: list[dict], daily_rows: list[dict]) -> dict[str, float | int]:
    day_totals: dict[str, float] = {}
    month_totals: dict[str, float] = {}
    segment_totals = {segment: 0.0 for segment in PNL_SEGMENTS}

    prior_equity = float(initial_cash)
    for row in portfolio_rows:
        timestamp = parse_dt(row.get("timestamp"))
        equity = float(row.get("equity") or prior_equity)
        delta = equity - prior_equity
        prior_equity = equity
        if timestamp is None:
            continue
        day_key = timestamp.date().isoformat()
        month_key = timestamp.strftime("%Y-%m")
        segment = pnl_segment_for_timestamp(timestamp)
        add_to_bucket(day_totals, day_key, delta)
        add_to_bucket(month_totals, month_key, delta)
        segment_totals[segment] += delta

    for row in daily_rows:
        day_key = str(row.get("session_date") or "")
        if day_key and day_key not in day_totals:
            pnl = float(row.get("pnl") or 0.0)
            day_totals[day_key] = pnl
            month_key = day_key[:7]
            add_to_bucket(month_totals, month_key, pnl)

    day_count = len(day_totals)
    month_count = len(month_totals)
    breakdown: dict[str, float | int] = {
        "pnl_day_count": day_count,
        "pnl_month_count": month_count,
        "avg_daily_pnl": sum(day_totals.values()) / day_count if day_count else 0.0,
        "avg_monthly_pnl": sum(month_totals.values()) / month_count if month_count else 0.0,
    }
    for segment in PNL_SEGMENTS:
        total = segment_totals[segment]
        breakdown[f"{segment}_pnl"] = total
        breakdown[f"{segment}_avg_daily_pnl"] = total / day_count if day_count else 0.0
        breakdown[f"{segment}_avg_monthly_pnl"] = total / month_count if month_count else 0.0
    return breakdown


def compute_summary(
    *,
    run_dir: str,
    strategy_name: str,
    run_name: str,
    initial_cash: float,
    trades: list[dict],
    orders: list[dict],
    portfolio_rows: list[dict],
    daily_rows: list[dict],
    fills: list[dict] | None = None,
) -> dict:
    final_equity = float(portfolio_rows[-1]["equity"]) if portfolio_rows else float(initial_cash)
    total_pnl = final_equity - float(initial_cash)
    total_return = (final_equity / initial_cash) - 1.0 if initial_cash else 0.0

    pnl_values = [float(trade.get("pnl") or 0.0) for trade in trades]
    returns = [float(trade.get("return_pct") or 0.0) for trade in trades]
    wins = [trade for trade in trades if float(trade.get("pnl") or 0.0) > 0]
    losses = [trade for trade in trades if float(trade.get("pnl") or 0.0) <= 0]
    win_pnls = [float(trade.get("pnl") or 0.0) for trade in wins]
    loss_pnls = [float(trade.get("pnl") or 0.0) for trade in losses]
    win_returns = [float(trade.get("return_pct") or 0.0) for trade in wins]
    loss_returns = [float(trade.get("return_pct") or 0.0) for trade in losses]
    durations = [duration_seconds(trade.get("entry_time"), trade.get("exit_time")) for trade in trades]
    win_durations = [duration_seconds(trade.get("entry_time"), trade.get("exit_time")) for trade in wins]
    loss_durations = [duration_seconds(trade.get("entry_time"), trade.get("exit_time")) for trade in losses]
    is_win_flags = [float(trade.get("pnl") or 0.0) > 0 for trade in trades]

    equity_values = [float(row.get("equity") or 0.0) for row in portfolio_rows]
    last_portfolio = portfolio_rows[-1] if portfolio_rows else {}
    unrealized_values = [float(row.get("open_unrealized_pnl") or 0.0) for row in portfolio_rows]
    open_unrealized_pnl = float(last_portfolio.get("open_unrealized_pnl") or 0.0)
    max_open_unrealized_pnl = max(unrealized_values, default=0.0)
    max_open_unrealized_loss = min(unrealized_values, default=0.0)
    realized_pnl = float(last_portfolio.get("realized_pnl") or (total_pnl - open_unrealized_pnl))
    gross_exposure = float(last_portfolio.get("gross_exposure") or 0.0)
    open_positions = int(last_portfolio.get("open_positions") or 0)
    drawdown_pct, drawdown_abs = max_drawdown(equity_values)
    daily_returns = daily_return_series(daily_rows)
    minute_returns = return_series(portfolio_rows)
    risk_returns = daily_returns if len(daily_returns) > 1 else minute_returns
    periods_per_year = 252.0 if len(daily_returns) > 1 else 252.0 * 390.0
    risk_std = safe_std(risk_returns)
    risk_downside = safe_std([value for value in risk_returns if value < 0])
    risk_mean = safe_mean(risk_returns)

    total_profit = sum(win_pnls)
    total_loss = sum(loss_pnls)
    avg_profit = safe_mean(win_pnls)
    avg_loss = safe_mean(loss_pnls)
    profit_loss_ratio = avg_profit / abs(avg_loss) if avg_loss else 0.0
    profit_factor = total_profit / abs(total_loss) if total_loss else 0.0
    win_rate = len(wins) / len(trades) if trades else 0.0
    loss_rate = len(losses) / len(trades) if trades else 0.0
    expectancy = (win_rate * profit_loss_ratio) - loss_rate if profit_loss_ratio else 0.0
    trade_sharpe = safe_mean(pnl_values) / safe_std(pnl_values) if safe_std(pnl_values) else 0.0
    downside_pnls = [value for value in pnl_values if value < 0]
    trade_sortino = safe_mean(pnl_values) / safe_std(downside_pnls) if safe_std(downside_pnls) else 0.0
    annual_std = risk_std * math.sqrt(periods_per_year)
    annual_variance = annual_std * annual_std
    annual_sharpe = (risk_mean / risk_std) * math.sqrt(periods_per_year) if risk_std else 0.0
    annual_sortino = (risk_mean / risk_downside) * math.sqrt(periods_per_year) if risk_downside else 0.0
    fills = fills or []
    filled_orders = [order for order in orders if order.get("status") == "FILLED"]
    total_commissions = sum(float(fill.get("commission") or 0.0) for fill in fills)
    total_regulatory_fees = sum(float(fill.get("regulatory_fee") or 0.0) for fill in fills)
    total_fee_tax = sum(float(fill.get("fee_tax") or 0.0) for fill in fills)
    total_fees = sum(float(fill.get("total_fee") or fill.get("fill_fee") or 0.0) for fill in fills)
    if not total_fees:
        total_fees = sum(float(order.get("fill_fee") or 0.0) for order in filled_orders)
    traded_value = (
        sum(abs(float(fill.get("quantity") or 0.0) * float(fill.get("fill_price") or 0.0)) for fill in fills)
        if fills
        else sum(abs(float(order.get("quantity") or 0.0) * float(order.get("fill_price") or 0.0)) for order in filled_orders)
    )
    avg_equity = safe_mean(equity_values)
    turnover = traded_value / avg_equity if avg_equity else 0.0

    start_time = trades[0].get("entry_time") if trades else None
    end_time = trades[-1].get("exit_time") if trades else None
    start_equity = float(initial_cash)

    trade_statistics = {
        "startDateTime": str(start_time) if start_time else None,
        "endDateTime": str(end_time) if end_time else None,
        "totalNumberOfTrades": len(trades),
        "numberOfWinningTrades": len(wins),
        "numberOfLosingTrades": len(losses),
        "totalProfitLoss": total_pnl,
        "totalProfit": total_profit,
        "totalLoss": total_loss,
        "largestProfit": max(win_pnls) if win_pnls else 0.0,
        "largestLoss": min(loss_pnls) if loss_pnls else 0.0,
        "averageProfitLoss": safe_mean(pnl_values),
        "averageProfit": avg_profit,
        "averageLoss": avg_loss,
        "averageTradeDuration": format_duration(safe_mean(durations)),
        "averageWinningTradeDuration": format_duration(safe_mean(win_durations)),
        "averageLosingTradeDuration": format_duration(safe_mean(loss_durations)),
        "medianTradeDuration": format_duration(statistics.median(durations) if durations else 0.0),
        "medianWinningTradeDuration": format_duration(statistics.median(win_durations) if win_durations else 0.0),
        "medianLosingTradeDuration": format_duration(statistics.median(loss_durations) if loss_durations else 0.0),
        "maxConsecutiveWinningTrades": max_consecutive(is_win_flags, True),
        "maxConsecutiveLosingTrades": max_consecutive(is_win_flags, False),
        "profitLossRatio": profit_loss_ratio,
        "winLossRatio": len(wins) / len(losses) if losses else 0.0,
        "winRate": win_rate,
        "lossRate": loss_rate,
        "averageMAE": safe_mean([float(trade.get("mae") or 0.0) for trade in trades]),
        "averageMFE": safe_mean([float(trade.get("mfe") or 0.0) for trade in trades]),
        "largestMAE": min([float(trade.get("mae") or 0.0) for trade in trades], default=0.0),
        "largestMFE": max([float(trade.get("mfe") or 0.0) for trade in trades], default=0.0),
        "maximumClosedTradeDrawdown": -drawdown_abs,
        "maximumIntraTradeDrawdown": min([float(trade.get("mae") or 0.0) for trade in trades], default=0.0),
        "profitLossStandardDeviation": safe_std(pnl_values),
        "profitLossDownsideDeviation": safe_std(downside_pnls),
        "profitFactor": profit_factor,
        "sharpeRatio": trade_sharpe,
        "sortinoRatio": trade_sortino,
        "profitToMaxDrawdownRatio": total_pnl / drawdown_abs if drawdown_abs else 0.0,
        "maximumEndTradeDrawdown": max([float(trade.get("end_trade_drawdown") or 0.0) for trade in trades], default=0.0),
        "averageEndTradeDrawdown": safe_mean([float(trade.get("end_trade_drawdown") or 0.0) for trade in trades]),
        "maximumDrawdownDuration": "not_available",
        "totalFees": total_fees,
    }

    portfolio_statistics = {
        "averageWinRate": safe_mean(win_returns),
        "averageLossRate": safe_mean(loss_returns),
        "profitLossRatio": profit_loss_ratio,
        "winRate": win_rate,
        "lossRate": loss_rate,
        "expectancy": expectancy,
        "startEquity": start_equity,
        "endEquity": final_equity,
        "compoundingAnnualReturn": annualized_return(start_equity, final_equity, daily_rows[0].get("session_date") if daily_rows else None, daily_rows[-1].get("session_date") if daily_rows else None),
        "drawdown": drawdown_pct,
        "totalNetProfit": total_return,
        "sharpeRatio": annual_sharpe,
        "probabilisticSharpeRatio": None,
        "sortinoRatio": annual_sortino,
        "alpha": None,
        "beta": None,
        "annualStandardDeviation": annual_std,
        "annualVariance": annual_variance,
        "informationRatio": None,
        "trackingError": None,
        "treynorRatio": None,
        "portfolioTurnover": turnover,
        "valueAtRisk99": percentile(daily_returns or minute_returns, 0.01),
        "valueAtRisk95": percentile(daily_returns or minute_returns, 0.05),
        "drawdownRecovery": drawdown_recovery_periods(equity_values),
    }

    runtime_statistics = {
        "Equity": final_equity,
        "Fees": total_fees,
        "Holdings": gross_exposure,
        "Net Profit": total_pnl,
        "Return": total_return,
        "Unrealized": open_unrealized_pnl,
        "Max Unrealized": max_open_unrealized_pnl,
        "Max Unrealized Loss": max_open_unrealized_loss,
        "Volume": traded_value,
    }

    pnl_breakdown = portfolio_pnl_breakdown(initial_cash, portfolio_rows, daily_rows)

    flat = {
        "run_dir": run_dir,
        "strategy_name": strategy_name,
        "run_name": run_name,
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "total_pnl": total_pnl,
        "realized_pnl": realized_pnl,
        "open_unrealized_pnl": open_unrealized_pnl,
        "max_open_unrealized_pnl": max_open_unrealized_pnl,
        "max_open_unrealized_loss": max_open_unrealized_loss,
        "return_pct": total_return,
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "avg_trade_pnl": safe_mean(pnl_values),
        "profit_factor": profit_factor,
        "max_drawdown_pct": drawdown_pct,
        "max_drawdown": drawdown_abs,
        "sharpe_ratio": annual_sharpe,
        "sortino_ratio": annual_sortino,
        "portfolio_turnover": turnover,
        "gross_exposure": gross_exposure,
        "open_positions": open_positions,
        "total_fees": total_fees,
        "total_commissions": total_commissions,
        "total_regulatory_fees": total_regulatory_fees,
        "total_fee_tax": total_fee_tax,
        "total_orders": len(orders),
        "total_fills": len(fills),
        **pnl_breakdown,
    }

    return {
        **flat,
        "tradeStatistics": trade_statistics,
        "portfolioStatistics": portfolio_statistics,
        "runtimeStatistics": runtime_statistics,
        "statistics": flat,
    }
