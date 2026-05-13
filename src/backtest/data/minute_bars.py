from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

from src.backtest.config import BacktestConfig
from src.backtest.models import DataRequirements
from src.data_provider.calendar import market_sessions
from src.data_provider.config import DataProviderConfig
from src.data_provider.manifest import artifact_key, read_manifest
from src.data_provider.provider import MarketDataProvider


@dataclass(slots=True)
class DayFrames:
    session_date: date
    event_frame: pl.DataFrame
    daily_context: pl.DataFrame


def provider_for_config(config: BacktestConfig) -> MarketDataProvider:
    return MarketDataProvider(
        DataProviderConfig(
            raw_root=config.data_root,
            processed_root=config.processed_data_root,
            exchange_timezone="America/New_York",
        )
    )


def available_session_dates(config: BacktestConfig, requirements: DataRequirements | None = None) -> list[date]:
    requirements = requirements or DataRequirements()
    sessions = market_sessions(config.start_date, config.end_date)
    validate_provider_artifacts(config, requirements, sessions)
    return sessions


def validate_provider_artifacts(
    config: BacktestConfig,
    requirements: DataRequirements,
    sessions: list[date],
) -> None:
    manifest = read_manifest(config.processed_data_root)
    artifacts = manifest.get("artifacts", {})
    missing = []
    column_errors = []

    def check(group: str, timeframe: str, session: date, required_columns: tuple[str, ...] = ()) -> None:
        key = artifact_key(group, timeframe, session.isoformat())
        record = artifacts.get(key)
        if not record:
            missing.append(key)
            return
        columns = set(record.get("columns") or [])
        for column in required_columns:
            if column not in columns:
                column_errors.append(f"{key}:{column}")

    for session in sessions:
        check("bars", requirements.event_timeframe, session, requirements.required_columns)
        for group in requirements.feature_groups:
            check(f"features_{group}", requirements.event_timeframe, session)
        for timeframe, groups in (requirements.context_feature_groups or {}).items():
            check("bars", timeframe, session)
            for group in groups:
                check(f"features_{group}", timeframe, session)

    daily_context_start = None
    daily_context_end = None
    daily_context_missing = False
    if sessions and requirements.daily_lookback_days > 0:
        daily_context_start = sessions[0] - timedelta(days=requirements.daily_lookback_days)
        daily_context_end = sessions[-1] - timedelta(days=1)
        for session in market_sessions(daily_context_start, daily_context_end):
            before = len(missing) + len(column_errors)
            check("bars", "1d", session)
            for group in requirements.daily_feature_groups:
                check(f"features_{group}", "1d", session)
            daily_context_missing = daily_context_missing or (len(missing) + len(column_errors) > before)

    if missing or column_errors:
        parts = []
        if daily_context_missing and daily_context_start and daily_context_end:
            groups = ", ".join(requirements.daily_feature_groups) or "none"
            parts.append(
                "daily context required: build provider 1d bars"
                f" and feature groups [{groups}] for {daily_context_start.isoformat()}..{daily_context_end.isoformat()}"
            )
        if missing:
            shown = ", ".join(missing[:12])
            suffix = " ..." if len(missing) > 12 else ""
            parts.append(f"missing artifacts: {shown}{suffix}")
        if column_errors:
            shown = ", ".join(column_errors[:12])
            suffix = " ..." if len(column_errors) > 12 else ""
            parts.append(f"missing columns: {shown}{suffix}")
        raise FileNotFoundError("Provider data is not built for this backtest request; " + "; ".join(parts))


def load_provider_bars(
    config: BacktestConfig,
    session_date: date,
    timeframe: str,
    feature_groups: list[str] | tuple[str, ...] = (),
) -> pl.DataFrame:
    provider = provider_for_config(config)
    bars = provider.load_bars(
        start_date=session_date,
        end_date=session_date,
        timeframe=timeframe,
        feature_groups=list(feature_groups),
    )
    if bars.is_empty():
        raise FileNotFoundError(
            f"Processed provider bars not found for {session_date.isoformat()} "
            f"timeframe={timeframe} under {config.processed_data_root}."
        )
    return bars


def attach_context_timeframe(event_frame: pl.DataFrame, context_frame: pl.DataFrame, timeframe: str) -> pl.DataFrame:
    if event_frame.is_empty() or context_frame.is_empty():
        return event_frame

    available_column = f"indicator_available_time_{timeframe}"
    market_time_dtype = context_frame.schema["bar_time_market"]
    context = context_frame.with_columns(
        (pl.col("bar_time_market") + pl.duration(minutes=timeframe_minutes(timeframe)))
        .cast(market_time_dtype)
        .alias(available_column)
    )

    join_columns = {"ticker", available_column}
    rename_map = {
        column: f"{column}_{timeframe}"
        for column in context.columns
        if column not in join_columns and column != "bar_id"
    }
    context = context.rename(rename_map)
    if "bar_id" in context.columns:
        context = context.drop("bar_id")

    left = event_frame.sort(["ticker", "bar_time_market"])
    right = context.sort(["ticker", available_column])
    return left.join_asof(
        right,
        left_on="bar_time_market",
        right_on=available_column,
        by="ticker",
        strategy="backward",
        check_sortedness=False,
    )


def timeframe_minutes(timeframe: str) -> int:
    if timeframe.endswith("m"):
        return int(timeframe[:-1])
    if timeframe.endswith("h"):
        return int(timeframe[:-1]) * 60
    if timeframe == "1d":
        return 390
    raise ValueError(f"Unsupported intraday context timeframe: {timeframe}")


def load_daily_context(config: BacktestConfig, session_date: date, requirements: DataRequirements) -> pl.DataFrame:
    if requirements.daily_lookback_days <= 0:
        return pl.DataFrame()

    provider = provider_for_config(config)
    start = session_date - timedelta(days=requirements.daily_lookback_days)
    end = session_date - timedelta(days=1)
    daily = provider.load_bars(
        start_date=start,
        end_date=end,
        timeframe="1d",
        feature_groups=list(requirements.daily_feature_groups),
    )
    if daily.is_empty():
        raise FileNotFoundError(
            f"Provider daily context is missing for {session_date.isoformat()} "
            f"lookback_days={requirements.daily_lookback_days} under {config.processed_data_root}."
        )

    if "atr14" not in daily.columns:
        daily = daily.with_columns((pl.col("high") - pl.col("low")).alias("atr14"))

    return (
        daily.sort(["ticker", "session_date"])
        .group_by("ticker")
        .agg(
            pl.col("volume").tail(14).mean().alias("avg_daily_volume_14"),
            pl.col("atr14").tail(14).mean().alias("atr_14"),
            pl.col("close").last().alias("previous_close"),
            pl.len().alias("daily_rows"),
        )
    )


def attach_daily_context(event_frame: pl.DataFrame, daily_context: pl.DataFrame) -> pl.DataFrame:
    if event_frame.is_empty() or daily_context.is_empty():
        return event_frame
    duplicate_columns = [column for column in daily_context.columns if column != "ticker" and column in event_frame.columns]
    context = daily_context.drop(duplicate_columns) if duplicate_columns else daily_context
    return event_frame.join(context, on="ticker", how="left")


def load_day_frames(
    config: BacktestConfig,
    session_date: date,
    requirements: DataRequirements | None = None,
) -> DayFrames:
    requirements = requirements or DataRequirements()
    event_frame = load_provider_bars(
        config,
        session_date,
        requirements.event_timeframe,
        requirements.feature_groups,
    )
    for timeframe, groups in (requirements.context_feature_groups or {}).items():
        context = load_provider_bars(config, session_date, timeframe, groups)
        event_frame = attach_context_timeframe(event_frame, context, timeframe)
    daily_context = load_daily_context(config, session_date, requirements)
    event_frame = attach_daily_context(event_frame, daily_context)
    return DayFrames(
        session_date=session_date,
        event_frame=event_frame.sort(["bar_time_market", "ticker"]),
        daily_context=daily_context,
    )
