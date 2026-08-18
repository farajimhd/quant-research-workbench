from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.trading_runtime.strategy_activation import run_plan_accepts_signal


def dispatchable_strategy_signals(
    configuration: Mapping[str, Any],
    occurrences: Iterable[Mapping[str, Any]],
    *,
    watchlist_runtime: Mapping[str, Any] | None = None,
    mode: str,
) -> list[dict[str, Any]]:
    """Compile new immutable occurrences into deterministic Run Plan deliveries."""

    membership = _watchlist_membership(watchlist_runtime or {})
    deliveries: list[dict[str, Any]] = []
    plans = list(dict(configuration.get("run_plans") or {}).get("plans") or [])
    for plan in plans:
        if mode not in set(plan.get("allowed_environments") or []):
            continue
        selected_watchlists = {
            str(value) for value in plan.get("watchlist_ids") or [] if str(value)
        }
        watchlist_policy = str(
            dict(plan.get("activation") or {}).get("watchlist_policy")
            or "any_selected"
        )
        eligible = (
            None
            if watchlist_policy == "not_required"
            else
            set.intersection(
                *(membership.get(watchlist_id, set()) for watchlist_id in selected_watchlists)
            )
            if selected_watchlists
            and watchlist_policy == "all_selected"
            else set().union(
                *(membership.get(watchlist_id, set()) for watchlist_id in selected_watchlists)
            )
            if selected_watchlists
            else None
        )
        for occurrence in occurrences:
            if not run_plan_accepts_signal(plan, occurrence, eligible_tickers=eligible):
                continue
            deliveries.append({
                "delivery_id": (
                    f"{plan.get('run_plan_id')}:{occurrence.get('event_id') or occurrence.get('signal_id')}"
                ),
                "run_plan_id": str(plan.get("run_plan_id") or ""),
                "profile_id": str(plan.get("profile_id") or ""),
                "book_id": str(plan.get("book_id") or "default"),
                "ticker": str(occurrence.get("ticker") or "").upper(),
                "signal_stream_id": str(occurrence.get("signal_stream_id") or ""),
                "event_id": str(occurrence.get("event_id") or occurrence.get("signal_id") or ""),
                "event_time": str(occurrence.get("effective_at") or occurrence.get("event_time") or ""),
                "occurrence": dict(occurrence),
            })
    deliveries.sort(key=lambda row: (row["event_time"], row["run_plan_id"], row["event_id"]))
    return deliveries


def _watchlist_membership(runtime: Mapping[str, Any]) -> dict[str, set[str]]:
    return {
        str(snapshot.get("watchlist_id") or ""): {
            str(row.get("ticker") or row.get("symbol") or "").strip().upper()
            for row in dict(snapshot).get("members") or []
            if str(row.get("ticker") or row.get("symbol") or "").strip()
        }
        for snapshot in runtime.get("watchlists") or []
        if str(snapshot.get("watchlist_id") or "")
    }
