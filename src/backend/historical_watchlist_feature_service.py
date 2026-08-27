from __future__ import annotations

import hashlib
import json
import os
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Any, Callable
from threading import Lock

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from src.backend.historical_scanner_service import (
    historical_scanner_fundamental_projection,
    historical_scanner_reference_projection,
)
from src.backend.query_plans.historical_watchlist_feature_intervals_v1 import (
    MAX_CHANGE_CLOCKS,
    feature_change_clocks,
)


REFERENCE_FIELDS = {
    "reference.market_cap": "market_cap",
    "reference.float_shares": "float_shares",
    "reference.short_interest": "short_interest",
    "reference.short_interest_pct": "short_crowding_pct",
    "reference.days_to_cover": "days_to_cover",
    "event.ipo.days_to_event": "ipo_days_to_event",
    "event.split.days_to_event": "split_days_to_event",
}
FUNDAMENTAL_FIELDS = {
    "fundamental.trajectory_score": "financial_trajectory_score",
    "fundamental.quality_score": "xbrl_quality_score",
}
IDENTITY_FIELDS = (
    "symbol_id",
    "security_id",
    "issuer_id",
    "listing_id",
    "ibkr_conid",
)
_MATERIALIZATION_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()
_MATERIALIZATION_CACHE_LOCK = Lock()
_MATERIALIZATION_CACHE_LIMIT = 8
_DURABLE_CACHE_SCHEMA_VERSION = 1
_DURABLE_CACHE_MAX_ENTRIES = 64
_DURABLE_CACHE_MAX_FILE_BYTES = 256 * 1024 * 1024
_QMD_WATCHLIST_CALCULATION_REVISION = "canvas_historical_qmd_snapshot_v6"
_APPLICATION_WATCHLIST_PROJECTION_REVISION = 2


def historical_watchlist_external_feature_intervals(
    plan: dict[str, Any],
    *,
    client: ClickHouseHttpClient | None = None,
    reference_projection: Callable[..., dict[str, dict[str, Any]]] = historical_scanner_reference_projection,
    fundamental_projection: Callable[..., dict[str, dict[str, Any]]] = historical_scanner_fundamental_projection,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Materialize causal external values only at registered source changes."""
    bundle = historical_watchlist_external_feature_bundle(
        plan,
        client=client,
        reference_projection=reference_projection,
        fundamental_projection=fundamental_projection,
    )
    return bundle["external_feature_revisions"], bundle["external_feature_intervals"]


def historical_watchlist_external_feature_bundle(
    plan: dict[str, Any],
    *,
    client: ClickHouseHttpClient | None = None,
    reference_projection: Callable[..., dict[str, dict[str, Any]]] = historical_scanner_reference_projection,
    fundamental_projection: Callable[..., dict[str, dict[str, Any]]] = historical_scanner_fundamental_projection,
) -> dict[str, Any]:
    """Materialize causal filter values and required point-in-time identity."""
    start = _clock(plan.get("start"), "start")
    end = _clock(plan.get("end"), "end")
    if end <= start:
        raise ValueError("historical Watchlist external feature end must follow start")
    contracts = {
        str(row.get("field_id") or ""): dict(row)
        for row in plan.get("external_features") or []
    }
    if "" in contracts or len(contracts) != len(plan.get("external_features") or []):
        raise ValueError("historical Watchlist external feature contracts are invalid")
    unknown = sorted(set(contracts) - set(REFERENCE_FIELDS) - set(FUNDAMENTAL_FIELDS))
    if unknown:
        raise ValueError(
            f"historical Watchlist interval provider does not support: {', '.join(unknown)}"
        )
    active_client = client or ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    )
    # Identity is control metadata required by Backtest even when no rule uses a
    # Reference field. It is not admitted as Watchlist filter/rank evidence.
    include_reference = True
    include_fundamentals = bool(set(contracts) & set(FUNDAMENTAL_FIELDS))
    rows = _json_rows(
        active_client.execute(
            feature_change_clocks(
                cadence_ms=max(1, int(plan.get("cadence_ms") or 0)),
                include_reference=include_reference,
                include_fundamentals=include_fundamentals,
                start=start,
                end=end,
            )
        )
    )
    if len(rows) > MAX_CHANGE_CLOCKS:
        raise RuntimeError(
            f"historical Watchlist external feature clocks exceed limit={MAX_CHANGE_CLOCKS}"
        )
    clocks = {start}
    clocks.update(_clock(row.get("available_at"), "available_at") for row in rows)
    for window in plan.get("evaluation_windows") or []:
        clocks.add(_clock(dict(window).get("start"), "evaluation window start"))
    ordered_clocks = sorted(clock for clock in clocks if start <= clock < end)

    open_values: dict[str, dict[str, dict[str, Any]]] = {
        field_id: {} for field_id in contracts
    }
    open_identity: dict[str, dict[str, Any]] = {}
    intervals: list[dict[str, Any]] = []
    identity_intervals: list[dict[str, Any]] = []
    for clock in ordered_clocks:
        reference = (
            reference_projection(clock, client=active_client) if include_reference else {}
        )
        fundamentals = (
            fundamental_projection(clock, client=active_client)
            if include_fundamentals
            else {}
        )
        current_identity = {
            str(ticker).strip().upper(): identity
            for ticker, row in reference.items()
            if str(ticker).strip()
            and (identity := _identity(dict(row))) is not None
        }
        for ticker in sorted(set(open_identity) | set(current_identity)):
            prior = open_identity.get(ticker)
            identity = current_identity.get(ticker)
            if prior is not None and prior["identity"] == identity:
                continue
            if prior is not None:
                identity_intervals.append(
                    {
                        "end": clock.astimezone(UTC).isoformat(),
                        "identity": prior["identity"],
                        "start": prior["start"],
                        "ticker": ticker,
                    }
                )
                open_identity.pop(ticker, None)
            if identity is not None:
                open_identity[ticker] = {
                    "identity": identity,
                    "start": clock.astimezone(UTC).isoformat(),
                }
        for field_id in sorted(contracts):
            source_key = REFERENCE_FIELDS.get(field_id) or FUNDAMENTAL_FIELDS.get(field_id)
            projection = reference if field_id in REFERENCE_FIELDS else fundamentals
            current = {
                str(ticker).strip().upper(): value
                for ticker, row in projection.items()
                if (value := _value(dict(row).get(str(source_key)))) is not None
                and str(ticker).strip()
            }
            active = open_values[field_id]
            for ticker in sorted(set(active) | set(current)):
                prior = active.get(ticker)
                value = current.get(ticker)
                if prior is not None and prior["value"] == value:
                    continue
                if prior is not None:
                    intervals.append(
                        {
                            "end": clock.astimezone(UTC).isoformat(),
                            "field_id": field_id,
                            "start": prior["start"],
                            "ticker": ticker,
                            "value": prior["value"],
                        }
                    )
                    active.pop(ticker, None)
                if value is not None:
                    active[ticker] = {
                        "start": clock.astimezone(UTC).isoformat(),
                        "value": value,
                    }
    for field_id, by_ticker in open_values.items():
        for ticker, open_value in by_ticker.items():
            intervals.append(
                {
                    "end": end.astimezone(UTC).isoformat(),
                    "field_id": field_id,
                    "start": open_value["start"],
                    "ticker": ticker,
                    "value": open_value["value"],
                }
            )
    for ticker, open_value in open_identity.items():
        identity_intervals.append(
            {
                "end": end.astimezone(UTC).isoformat(),
                "identity": open_value["identity"],
                "start": open_value["start"],
                "ticker": ticker,
            }
        )
    intervals.sort(key=lambda row: (row["field_id"], row["ticker"], row["start"]))
    identity_intervals.sort(key=lambda row: (row["ticker"], row["start"]))

    revisions = []
    for field_id, contract in sorted(contracts.items()):
        field_intervals = [row for row in intervals if row["field_id"] == field_id]
        encoded = json.dumps(
            {
                "field_id": field_id,
                "intervals": field_intervals,
                "query_plan_id": contract.get("query_plan_id"),
                "query_plan_version": contract.get("query_plan_version"),
                "schema_version": contract.get("schema_version"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        revisions.append(
            {
                "complete": True,
                "field_id": field_id,
                "query_plan_id": str(contract.get("query_plan_id") or ""),
                "query_plan_version": int(contract.get("query_plan_version") or 0),
                "schema_version": int(contract.get("schema_version") or 0),
                "source_revision": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
            }
        )
    identity_revision = {
        "complete": True,
        "query_plan_id": "reference.scanner_asof.v1",
        "query_plan_version": 2,
        "source_revision": _content_hash(identity_intervals),
    }
    return {
        "external_feature_intervals": intervals,
        "external_feature_revisions": revisions,
        "identity_intervals": identity_intervals,
        "identity_revision": identity_revision,
    }


def materialize_historical_watchlist_plan(plan: dict[str, Any]) -> dict[str, Any]:
    from src.backend.qmd_gateway_client import (
        qmd_historical_source_revision,
        qmd_materialize_historical_watchlist_timeline,
    )

    bundle = historical_watchlist_external_feature_bundle(plan)
    revisions = bundle["external_feature_revisions"]
    intervals = bundle["external_feature_intervals"]
    source_revision = qmd_historical_source_revision(
        start=str(plan.get("start") or ""), end=str(plan.get("end") or "")
    )
    dependency_bounds = _dependency_source_bounds([plan])
    dependency_source_revision = (
        qmd_historical_source_revision(**dependency_bounds)
        if dependency_bounds["start"] != str(plan.get("start") or "")
        else source_revision
    )
    cache_key = _content_hash(
        {
            "calculation_revision": _QMD_WATCHLIST_CALCULATION_REVISION,
            "application_projection_revision": _APPLICATION_WATCHLIST_PROJECTION_REVISION,
            "external_feature_revisions": revisions,
            "identity_revision": bundle["identity_revision"],
            "plan_hash": plan.get("plan_hash"),
            "source_revision": source_revision,
            "dependency_source_revision": dependency_source_revision,
        }
    )
    with _MATERIALIZATION_CACHE_LOCK:
        cached = _MATERIALIZATION_CACHE.get(cache_key)
        if cached is not None:
            _MATERIALIZATION_CACHE.move_to_end(cache_key)
            return json.loads(json.dumps(cached))
    durable = _durable_cache_read(cache_key, source_revision=source_revision)
    if durable is not None:
        _remember_materialization(cache_key, durable)
        return durable
    materialized = qmd_materialize_historical_watchlist_timeline(
        plan,
        external_feature_revisions=revisions,
        external_feature_intervals=intervals,
    )
    _enrich_materialized_identity(
        materialized,
        identity_intervals=bundle["identity_intervals"],
        identity_revision=bundle["identity_revision"],
    )
    _assert_source_revision(materialized.get("source_revision"), source_revision)
    _assert_dependency_revision_stable(
        dependency_source_revision,
        qmd_historical_source_revision(**dependency_bounds),
    )
    materialized["dependency_source_revision"] = dependency_source_revision
    _durable_cache_write(cache_key, materialized, source_revision=source_revision)
    _remember_materialization(cache_key, materialized)
    return materialized


def materialize_historical_watchlist_plans(
    plans: list[dict[str, Any]],
) -> dict[str, Any]:
    """Materialize several Watchlists through one shared QMD market replay."""
    if not plans:
        return {
            "application_batch_materialization_id": _content_hash([]),
            "materializations": [],
        }
    from src.backend.qmd_gateway_client import (
        qmd_historical_source_revision,
        qmd_materialize_historical_watchlist_timelines,
    )

    bundles = [historical_watchlist_external_feature_bundle(plan) for plan in plans]
    requests = [
        {
            "external_feature_intervals": bundle["external_feature_intervals"],
            "external_feature_revisions": bundle["external_feature_revisions"],
            "plan": plan,
        }
        for plan, bundle in zip(plans, bundles, strict=True)
    ]
    bounds = {
        (str(plan.get("start") or ""), str(plan.get("end") or "")) for plan in plans
    }
    if len(bounds) != 1:
        raise ValueError("historical Watchlist batch plans must share exact bounds")
    start, end = next(iter(bounds))
    source_revision = qmd_historical_source_revision(start=start, end=end)
    dependency_bounds = _dependency_source_bounds(plans)
    dependency_source_revision = (
        qmd_historical_source_revision(**dependency_bounds)
        if dependency_bounds["start"] != start
        else source_revision
    )
    cache_key = _content_hash(
        {
            "calculation_revision": _QMD_WATCHLIST_CALCULATION_REVISION,
            "application_projection_revision": _APPLICATION_WATCHLIST_PROJECTION_REVISION,
            "batch": [
                {
                    "external_feature_revisions": bundle["external_feature_revisions"],
                    "identity_revision": bundle["identity_revision"],
                    "plan_hash": plan.get("plan_hash"),
                }
                for plan, bundle in zip(plans, bundles, strict=True)
            ],
            "source_revision": source_revision,
            "dependency_source_revision": dependency_source_revision,
        }
    )
    with _MATERIALIZATION_CACHE_LOCK:
        cached = _MATERIALIZATION_CACHE.get(cache_key)
        if cached is not None:
            _MATERIALIZATION_CACHE.move_to_end(cache_key)
            return json.loads(json.dumps(cached))
    durable = _durable_cache_read(cache_key, source_revision=source_revision)
    if durable is not None:
        _remember_materialization(cache_key, durable)
        return durable
    batch = qmd_materialize_historical_watchlist_timelines(requests)
    _assert_source_revision(batch.get("source_revision"), source_revision)
    _assert_dependency_revision_stable(
        dependency_source_revision,
        qmd_historical_source_revision(**dependency_bounds),
    )
    by_watchlist = {
        str(row.get("watchlist_id") or ""): row
        for row in batch.get("materializations") or []
    }
    ordered = []
    for plan, bundle in zip(plans, bundles, strict=True):
        watchlist_id = str(plan.get("watchlist_id") or "")
        materialized = by_watchlist.get(watchlist_id)
        if materialized is None:
            raise RuntimeError(
                f"QMD History batch omitted historical Watchlist {watchlist_id}"
            )
        _enrich_materialized_identity(
            materialized,
            identity_intervals=bundle["identity_intervals"],
            identity_revision=bundle["identity_revision"],
        )
        ordered.append(materialized)
    batch["materializations"] = ordered
    batch["application_batch_materialization_id"] = _content_hash(
        {
            "qmd_batch_materialization_id": batch.get("batch_materialization_id"),
            "materializations": [
                row.get("application_materialization_id") for row in ordered
            ],
        }
    )
    batch["dependency_source_revision"] = dependency_source_revision
    _durable_cache_write(cache_key, batch, source_revision=source_revision)
    _remember_materialization(cache_key, batch)
    return batch


def _remember_materialization(cache_key: str, payload: dict[str, Any]) -> None:
    with _MATERIALIZATION_CACHE_LOCK:
        _MATERIALIZATION_CACHE[cache_key] = json.loads(json.dumps(payload))
        _MATERIALIZATION_CACHE.move_to_end(cache_key)
        while len(_MATERIALIZATION_CACHE) > _MATERIALIZATION_CACHE_LIMIT:
            _MATERIALIZATION_CACHE.popitem(last=False)


def _durable_cache_root() -> Path:
    configured = os.environ.get("QMD_WATCHLIST_TIMELINE_CACHE_DIR", "").strip()
    return Path(
        configured
        or r"D:\TradingML\runtimes\qmd_history\watchlist_timelines"
    )


def _durable_cache_path(cache_key: str) -> Path:
    digest = cache_key.removeprefix("sha256:")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("historical Watchlist cache key is invalid")
    return _durable_cache_root() / f"watchlist-{digest}.json"


def _durable_cache_read(
    cache_key: str, *, source_revision: dict[str, Any]
) -> dict[str, Any] | None:
    path = _durable_cache_path(cache_key)
    try:
        if not path.is_file() or path.stat().st_size > _DURABLE_CACHE_MAX_FILE_BYTES:
            return None
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        int(envelope.get("schema_version") or 0) != _DURABLE_CACHE_SCHEMA_VERSION
        or str(envelope.get("cache_key") or "") != cache_key
        or dict(envelope.get("source_revision") or {}) != source_revision
    ):
        return None
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or str(envelope.get("payload_hash") or "") != _content_hash(payload):
        return None
    return json.loads(json.dumps(payload))


def _durable_cache_write(
    cache_key: str,
    payload: dict[str, Any],
    *,
    source_revision: dict[str, Any],
) -> None:
    path = _durable_cache_path(cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "cache_key": cache_key,
        "payload": payload,
        "payload_hash": _content_hash(payload),
        "schema_version": _DURABLE_CACHE_SCHEMA_VERSION,
        "source_revision": source_revision,
    }
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _DURABLE_CACHE_MAX_FILE_BYTES:
        raise RuntimeError("historical Watchlist durable materialization exceeds cache file limit")
    temporary = path.with_suffix(f".{os.getpid()}.{id(payload)}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    candidates = sorted(
        path.parent.glob("watchlist-*.json"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    for expired in candidates[_DURABLE_CACHE_MAX_ENTRIES:]:
        try:
            expired.unlink()
        except OSError:
            pass


def _assert_source_revision(actual: Any, expected: dict[str, Any]) -> None:
    actual_revision = dict(actual or {})
    for field in ("source_plan_hash", "token"):
        if str(actual_revision.get(field) or "") != str(expected.get(field) or ""):
            raise RuntimeError(
                "QMD History source revision changed during Watchlist materialization; retry"
            )


def _assert_dependency_revision_stable(
    before: dict[str, Any], after: dict[str, Any]
) -> None:
    for field in ("source_plan_hash", "token"):
        if str(before.get(field) or "") != str(after.get(field) or ""):
            raise RuntimeError(
                "QMD History dependency revision changed during Watchlist materialization; retry"
            )


def _dependency_source_bounds(plans: list[dict[str, Any]]) -> dict[str, str]:
    start = min(_clock(plan.get("start"), "start") for plan in plans)
    end = max(_clock(plan.get("end"), "end") for plan in plans)
    if any(
        "market.relative_volume" in set(plan.get("qmd_sources") or []) for plan in plans
    ):
        # Forty-five calendar days conservatively covers 20 completed US market
        # sessions plus weekends and common holiday clusters. QMD records the
        # exact 20-session ticker-filtered revisions in the materialization.
        start -= timedelta(days=45)
    return {
        "start": start.astimezone(UTC).isoformat(),
        "end": end.astimezone(UTC).isoformat(),
    }


def _clock(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"historical Watchlist {label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"historical Watchlist {label} must be timezone-aware")
    return parsed.astimezone(UTC)


def _value(value: Any) -> float | bool | None:
    if isinstance(value, bool):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _identity(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        conid = int(row.get("ibkr_conid") or 0)
    except (TypeError, ValueError):
        conid = 0
    if conid <= 0:
        return None
    identity = {"ibkr_conid": conid}
    for field in IDENTITY_FIELDS[:-1]:
        value = str(row.get(field) or "").strip()
        if value:
            identity[field] = value
    return identity


def _enrich_materialized_identity(
    materialized: dict[str, Any],
    *,
    identity_intervals: list[dict[str, Any]],
    identity_revision: dict[str, Any],
) -> None:
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for interval in identity_intervals:
        by_ticker.setdefault(str(interval["ticker"]), []).append(interval)
    active_identity: dict[str, dict[str, Any]] = {}
    suppressed: set[str] = set()
    rejections: list[dict[str, Any]] = []
    for chunk in materialized.get("chunks") or []:
        retained: list[dict[str, Any]] = []
        for transition in dict(chunk).get("transitions") or []:
            ticker = str(transition.get("ticker") or "").strip().upper()
            clock = _clock(transition.get("effective_at"), "transition effective_at")
            matches = [
                interval
                for interval in by_ticker.get(ticker, [])
                if _clock(interval["start"], "identity start") <= clock
                < _clock(interval["end"], "identity end")
            ]
            event = str(transition.get("event") or "")
            identity = dict(matches[0]["identity"]) if len(matches) == 1 else None
            if event == "removed":
                prior = active_identity.pop(ticker, None)
                suppressed.discard(ticker)
                if prior is None:
                    continue
                transition["identity"] = prior
                retained.append(transition)
                continue
            if identity is None:
                rejections.append(
                    {
                        "effective_at": clock.astimezone(UTC).isoformat(),
                        "event": event,
                        "reason": "point_in_time_identity_unavailable",
                        "ticker": ticker,
                    }
                )
                prior = active_identity.pop(ticker, None)
                suppressed.add(ticker)
                if prior is not None:
                    transition.update(
                        {
                            "event": "removed",
                            "identity": prior,
                            "prior_rank": transition.get("prior_rank")
                            or transition.get("rank"),
                            "rank": None,
                            "reason": "point-in-time identity became unavailable",
                        }
                    )
                    retained.append(transition)
                continue
            if ticker in suppressed:
                transition.update(
                    {
                        "event": "added",
                        "prior_rank": None,
                        "reason": "point-in-time identity became available",
                    }
                )
                suppressed.discard(ticker)
            transition["identity"] = identity
            active_identity[ticker] = identity
            retained.append(transition)
        chunk["transitions"] = retained
    materialized["identity_revision"] = dict(identity_revision)
    materialized["qmd_transition_count"] = int(materialized.get("transition_count") or 0)
    materialized["application_transition_count"] = sum(
        len(dict(chunk).get("transitions") or [])
        for chunk in materialized.get("chunks") or []
    )
    materialized["identity_rejection_count"] = len(rejections)
    materialized["identity_rejections"] = rejections
    materialized["application_materialization_id"] = _content_hash(
        {
            "identity_revision": identity_revision,
            "identity_rejections": rejections,
            "projection_revision": _APPLICATION_WATCHLIST_PROJECTION_REVISION,
            "qmd_materialization_id": materialized.get("materialization_id"),
        }
    )


def _json_rows(payload: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def _content_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
