from __future__ import annotations

from typing import Any, Iterable

from src.backend.application_registry import (
    DISCOVERY_FIELD_PRESENTATIONS,
    DISCOVERY_RUNTIME_FIELDS,
)


_PRESENTATION_BY_COLUMN = {
    presentation.column_id: presentation
    for presentation in DISCOVERY_FIELD_PRESENTATIONS
    if presentation.column_id
}


def discovery_runtime_field(source_id: str) -> str:
    """Resolve one semantic source ID to its canonical flat Scanner key."""

    return DISCOVERY_RUNTIME_FIELDS.get(source_id, source_id)


def project_discovery_columns(
    rows: Iterable[dict[str, Any]],
    *,
    column_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Materialize stable configured column IDs from producer-owned row keys."""

    selected = (
        {str(value) for value in column_ids if str(value)}
        if column_ids is not None
        else set(_PRESENTATION_BY_COLUMN)
    )
    projected: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        for column_id in selected:
            presentation = _PRESENTATION_BY_COLUMN.get(column_id)
            if presentation is None or result.get(column_id) not in (None, ""):
                continue
            runtime_field = discovery_runtime_field(presentation.source_id)
            if runtime_field in row:
                result[column_id] = row.get(runtime_field)
                for suffix in ("_available_at", "_published_at", "_source_date", "_null_reason"):
                    runtime_key = f"{runtime_field}{suffix}"
                    if runtime_key in row:
                        result[f"{column_id}{suffix}"] = row.get(runtime_key)
        projected.append(result)
    return projected


def configured_discovery_column_ids(configuration: dict[str, Any]) -> set[str]:
    discovery = dict(configuration.get("market_discovery") or {})
    selected = {
        str(value)
        for value in dict(discovery.get("core_scan") or {}).get("columns") or []
        if str(value)
    }
    for key in ("watchlists", "signal_streams"):
        for composition in discovery.get(key) or []:
            if not bool(composition.get("enabled", True)):
                continue
            if key == "watchlists" and str(composition.get("availability") or "available") != "available":
                continue
            selected.update(str(value) for value in composition.get("columns") or [] if str(value))
    return selected


def configured_discovery_technical_windows(configuration: dict[str, Any]) -> tuple[str, ...]:
    """Compile technical demand from every configured discovery composition."""

    discovery = dict(configuration.get("market_discovery") or {})
    compiled = dict(discovery.get("data_field_plan") or {})
    compiled_windows = {
        str(value)
        for value in compiled.get("technical_timeframes") or []
        if str(value) in {"1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"}
    }
    if compiled_windows:
        return tuple(sorted(compiled_windows))
    selected_rule_ids: set[str] = set()
    windows: set[str] = set()
    selected_columns = configured_discovery_column_ids(configuration)
    for composition in [
        dict(discovery.get("core_scan") or {}),
        *list(discovery.get("watchlists") or []),
        *list(discovery.get("signal_streams") or []),
    ]:
        if composition.get("enabled") is False:
            continue
        selected_rule_ids.update(
            str(value)
            for key in ("inclusion_rule_sets", "exclusion_rule_sets")
            for value in composition.get(key) or []
            if str(value)
        )
        windows.update(
            str(value)
            for value in dict(composition.get("column_intervals") or {}).values()
            if str(value) in {"1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"}
        )
        ranking_interval = str(composition.get("ranking_interval") or "")
        if ranking_interval in {"1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"}:
            windows.add(ranking_interval)
    for rule_set in discovery.get("rule_sets") or []:
        if str(rule_set.get("rule_set_id") or "") not in selected_rule_ids:
            continue
        for condition in rule_set.get("conditions") or []:
            if not bool(condition.get("enabled", True)):
                continue
            for key in ("left_interval", "right_interval"):
                value = str(condition.get(key) or "")
                if value in {"1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"}:
                    windows.add(value)
    for column_id in selected_columns:
        presentation = _PRESENTATION_BY_COLUMN.get(column_id)
        if presentation is None or presentation.semantic_type != "indicator":
            continue
        windows.update(
            value
            for value in presentation.timeframes
            if value in {"1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"}
        )
    return tuple(sorted(windows))
