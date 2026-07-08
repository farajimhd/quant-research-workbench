from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib import parse, request
from zoneinfo import ZoneInfo

import numpy as np

from pipelines.market_sip.events.clickhouse_build_unified_events import events_table_for_year
from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string
from research.mlops.data.contracts import BAR_FAMILY_KEYS
from research.mlops.rolling_loader.daily_index_dataset import (
    BAR_SOURCE_FEATURE_KEYS,
    DEFAULT_INTRADAY_LABEL_HORIZONS,
    LABEL_VALUE_DTYPES,
    SESSION_END_US,
    SESSION_START_SECOND,
    _intraday_horizon_specs,
)


EVENT_SOURCE_COLUMNS: tuple[str, ...] = (
    "ticker",
    "ordinal",
    "event_meta",
    "timestamp_us",
    "price_primary_int",
    "price_secondary_int",
    "size_primary",
    "size_secondary",
    "exchange_primary",
    "exchange_secondary",
    "condition_token_1",
    "condition_token_2",
    "condition_token_3",
    "condition_token_4",
    "condition_token_5",
)
NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class DailyIndexBatchAuditConfig:
    enabled: bool = True
    strict: bool = True
    max_batches: int = 2
    samples_per_batch: int = 10
    seed: int = 17
    report_path: Path | None = None
    summary_path: Path | None = None
    clickhouse_url: str = ""
    clickhouse_user: str = ""
    clickhouse_password: str = ""
    database: str = "market_sip_compact"
    events_table: str = "events"
    source_event_limit: int = 250_000
    compare_atol: float = 1e-4
    compare_rtol: float = 1e-5
    rest_samples: int = 0
    massive_base_url: str = "https://api.massive.com"
    massive_api_key_env: str = "MASSIVE_API_KEY"
    required_availability_keys: tuple[str, ...] = ()
    required_availability_min_fraction: float = 0.0


@dataclass(slots=True)
class DailyIndexBatchAuditor:
    config: DailyIndexBatchAuditConfig
    audited_batches: int = 0
    audited_samples: int = 0
    failed_checks: int = 0
    skipped_checks: int = 0
    rest_checked: int = 0
    last_status: str = "not_started"
    _client: ClickHouseHttpClient | None = field(default=None, init=False, repr=False)

    def audit_batch(self, batch: Any, *, batch_number: int, phase: str) -> dict[str, Any]:
        if not self.config.enabled or str(phase) != "measure":
            return {"audit_enabled": bool(self.config.enabled), "audit_checked": 0}
        if int(self.config.max_batches) <= 0 or self.audited_batches >= int(self.config.max_batches):
            return {"audit_enabled": True, "audit_checked": 0, "audit_skipped_reason": "batch_limit_reached"}
        started = time.perf_counter()
        coverage_failures = self._required_availability_failures(batch)
        if coverage_failures and self.config.strict:
            raise RuntimeError(f"Daily-index batch audit coverage failed for batch={batch_number}: {coverage_failures}")
        sample_indices = self._sample_indices(batch, batch_number=batch_number)
        records: list[dict[str, Any]] = []
        if coverage_failures:
            records.append(
                {
                    "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    "identity": {"batch": int(batch_number), "sample_index": -1},
                    "checks": [_check("required_modality_coverage", "fail", "required batch availability is missing", {"failures": coverage_failures})],
                    "summary": {"checked": 1, "failed": 1, "skipped": 0},
                }
            )
        for sample_index in sample_indices:
            record = self._audit_sample(batch, sample_index=int(sample_index), batch_number=int(batch_number))
            records.append(record)
            self.audited_samples += 1
            self.failed_checks += int(record["summary"]["failed"])
            self.skipped_checks += int(record["summary"]["skipped"])
            if self.config.report_path is not None:
                _append_jsonl(Path(self.config.report_path), record)
        self.audited_batches += 1
        failed = sum(int(record["summary"]["failed"]) for record in records)
        skipped = sum(int(record["summary"]["skipped"]) for record in records)
        checked = sum(int(record["summary"]["checked"]) for record in records)
        elapsed = time.perf_counter() - started
        summary = {
            "audit_enabled": True,
            "audit_batch_number": int(batch_number),
            "audit_samples": int(len(records)),
            "audit_checked": int(checked),
            "audit_failed": int(failed),
            "audit_skipped": int(skipped),
            "audit_seconds": float(elapsed),
        }
        self.last_status = "failed" if failed else "passed"
        if self.config.summary_path is not None:
            _write_json(Path(self.config.summary_path), self.summary())
        if failed and self.config.strict:
            preview = [record for record in records if int(record["summary"]["failed"]) > 0][:3]
            raise RuntimeError(f"Daily-index batch audit failed for batch={batch_number}: {json.dumps(preview, default=str)[:4000]}")
        return summary

    def _required_availability_failures(self, batch: Any) -> dict[str, float]:
        required = tuple(str(key) for key in self.config.required_availability_keys or ())
        if not required:
            return {}
        sample_count = max(1, int(getattr(batch, "sample_count", 0) or 0))
        minimum = max(0.0, min(1.0, float(self.config.required_availability_min_fraction)))
        availability = getattr(batch, "input_availability", {}) or {}
        failures: dict[str, float] = {}
        for key in required:
            arr = np.asarray(availability.get(key, []))
            if arr.size == 0:
                fraction = 0.0
            elif arr.shape[:1] == (sample_count,):
                fraction = float(np.mean(arr.reshape((sample_count, -1)).any(axis=1).astype(np.float32)))
            else:
                fraction = float(bool(np.any(arr)))
            if fraction < minimum:
                failures[key] = fraction
        return failures

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.config.enabled),
            "strict": bool(self.config.strict),
            "audited_batches": int(self.audited_batches),
            "audited_samples": int(self.audited_samples),
            "failed_checks": int(self.failed_checks),
            "skipped_checks": int(self.skipped_checks),
            "rest_checked": int(self.rest_checked),
            "last_status": str(self.last_status),
            "report_path": str(self.config.report_path or ""),
        }

    def _sample_indices(self, batch: Any, *, batch_number: int) -> np.ndarray:
        count = int(getattr(batch, "sample_count", 0) or 0)
        take = min(max(0, int(self.config.samples_per_batch)), count)
        if take <= 0:
            return np.asarray([], dtype=np.int64)
        seed = _stable_seed("daily_index_batch_audit", int(self.config.seed), int(batch_number), count)
        rng = np.random.default_rng(seed)
        return np.sort(rng.choice(count, size=take, replace=False).astype(np.int64, copy=False))

    def _audit_sample(self, batch: Any, *, sample_index: int, batch_number: int) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        ticker = str(batch.ticker[sample_index])
        origin_ordinal = int(batch.origin_ordinal[sample_index])
        origin_timestamp_us = int(batch.origin_timestamp_us[sample_index])
        source_part_key = str(batch.source_part_key[sample_index]) if getattr(batch, "source_part_key", np.asarray([])).shape[0] else ""
        identity = {
            "batch": int(batch_number),
            "sample_index": int(sample_index),
            "ticker": ticker,
            "origin_ordinal": int(origin_ordinal),
            "origin_timestamp_us": int(origin_timestamp_us),
            "source_part_key": source_part_key,
        }
        event_rows: list[dict[str, Any]] = []
        future_rows: list[dict[str, Any]] = []
        backward_rows: list[dict[str, Any]] = []
        self._check_identity_contract(batch, sample_index, identity, checks)
        try:
            stream_length = int(batch.raw_event_stream.shape[1]) if getattr(batch, "raw_event_stream", np.asarray([])).ndim == 3 else 0
            event_rows = self._query_event_window(
                ticker=ticker,
                start_ordinal=max(0, origin_ordinal - stream_length + 1),
                end_ordinal=origin_ordinal,
                source_part_key=source_part_key,
            )
            self._check_source_event_window(event_rows, origin_ordinal, origin_timestamp_us, stream_length, checks)
            self._check_raw_event_stream(batch, sample_index, event_rows, checks)
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("source_event_window", "fail", str(exc)))
        self._check_future_label_grid(batch, sample_index, origin_timestamp_us, checks)
        try:
            min_future_ts, max_future_ts = _future_label_bounds(batch, sample_index)
            if min_future_ts and max_future_ts:
                future_rows = self._query_event_time_range(
                    ticker=ticker,
                    start_timestamp_us=min_future_ts,
                    end_timestamp_us=max_future_ts,
                    source_part_key=source_part_key,
                    inclusive_start=True,
                    inclusive_end=False,
                )
                if len(future_rows) > int(self.config.source_event_limit):
                    checks.append(_check("future_intraday_labels", "skip", f"source event range exceeds limit={self.config.source_event_limit:,}"))
                else:
                    self._check_future_intraday_labels(batch, sample_index, future_rows, checks)
            else:
                checks.append(_check("future_intraday_labels", "skip", "batch has no future label grid timestamps"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("future_intraday_labels", "fail", str(exc)))
        try:
            min_back_ts, max_back_ts = _backward_bar_bounds(batch, sample_index)
            if min_back_ts and max_back_ts:
                backward_rows = self._query_event_time_range(
                    ticker=ticker,
                    start_timestamp_us=min_back_ts,
                    end_timestamp_us=max_back_ts,
                    source_part_key=source_part_key,
                    inclusive_start=True,
                    inclusive_end=False,
                )
                if len(backward_rows) > int(self.config.source_event_limit):
                    checks.append(_check("backward_intraday_bars", "skip", f"source event range exceeds limit={self.config.source_event_limit:,}"))
                else:
                    self._check_backward_intraday_bars(batch, sample_index, backward_rows, checks)
            else:
                checks.append(_check("backward_intraday_bars", "skip", "batch has no backward intraday bar timestamps"))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("backward_intraday_bars", "fail", str(exc)))
        self._check_sparse_context_asof(batch, sample_index, origin_timestamp_us, checks)
        self._check_daily_and_corporate_consistency(batch, sample_index, origin_timestamp_us, checks)
        self._check_massive_rest_spot(ticker, origin_timestamp_us, checks)
        return {
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "identity": identity,
            "checks": checks,
            "summary": _summarize_checks(checks),
        }

    def _client_or_raise(self) -> ClickHouseHttpClient:
        if self._client is None:
            if not self.config.clickhouse_url:
                raise RuntimeError("ClickHouse audit is enabled but clickhouse_url is empty.")
            self._client = ClickHouseHttpClient(self.config.clickhouse_url, self.config.clickhouse_user, self.config.clickhouse_password)
        return self._client

    def _query_event_window(self, *, ticker: str, start_ordinal: int, end_ordinal: int, source_part_key: str) -> list[dict[str, Any]]:
        years = _candidate_years_for_part(source_part_key, int(_timestamp_year_guess_from_part(source_part_key)))
        where = f"ticker = {sql_string(ticker)} AND ordinal BETWEEN {int(start_ordinal)} AND {int(end_ordinal)}"
        return self._query_events_union(years, where=where, order_by="ordinal", limit=max(1, int(end_ordinal) - int(start_ordinal) + 1))

    def _query_event_time_range(
        self,
        *,
        ticker: str,
        start_timestamp_us: int,
        end_timestamp_us: int,
        source_part_key: str,
        inclusive_start: bool,
        inclusive_end: bool,
    ) -> list[dict[str, Any]]:
        start_op = ">=" if inclusive_start else ">"
        end_op = "<=" if inclusive_end else "<"
        start_year = dt.datetime.fromtimestamp(int(start_timestamp_us) / 1_000_000, tz=dt.timezone.utc).year
        end_year = dt.datetime.fromtimestamp(max(int(start_timestamp_us), int(end_timestamp_us) - 1) / 1_000_000, tz=dt.timezone.utc).year
        years = tuple(range(start_year, end_year + 1))
        where = (
            f"ticker = {sql_string(ticker)} "
            f"AND sip_timestamp_us {start_op} {int(start_timestamp_us)} "
            f"AND sip_timestamp_us {end_op} {int(end_timestamp_us)}"
        )
        return self._query_events_union(years, where=where, order_by="timestamp_us, ordinal", limit=int(self.config.source_event_limit) + 1)

    def _query_events_union(self, years: Sequence[int], *, where: str, order_by: str, limit: int) -> list[dict[str, Any]]:
        selects = []
        db = quote_ident(self.config.database)
        for year in sorted({int(year) for year in years if int(year) >= 1900}):
            table = quote_ident(events_table_for_year(self.config.events_table, year))
            selects.append(
                "SELECT "
                "ticker, toUInt64(ordinal) AS ordinal, toUInt8(event_meta) AS event_meta, "
                "toInt64(sip_timestamp_us) AS timestamp_us, "
                "toUInt32(price_primary_int) AS price_primary_int, toUInt32(price_secondary_int) AS price_secondary_int, "
                "toFloat32(size_primary) AS size_primary, toFloat32(size_secondary) AS size_secondary, "
                "toUInt8(exchange_primary) AS exchange_primary, toUInt8(exchange_secondary) AS exchange_secondary, "
                "toUInt8(condition_token_1) AS condition_token_1, toUInt8(condition_token_2) AS condition_token_2, "
                "toUInt8(condition_token_3) AS condition_token_3, toUInt8(condition_token_4) AS condition_token_4, "
                f"toUInt8(condition_token_5) AS condition_token_5 FROM {db}.{table} WHERE {where}"
            )
        if not selects:
            return []
        sql = "\nUNION ALL\n".join(selects)
        text = self._client_or_raise().execute(f"SELECT * FROM ({sql}) ORDER BY {order_by} LIMIT {max(1, int(limit))}\nFORMAT JSONEachRow")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _check_identity_contract(self, batch: Any, sample_index: int, identity: Mapping[str, Any], checks: list[dict[str, Any]]) -> None:
        sample_count = int(getattr(batch, "sample_count", 0) or 0)
        ok = 0 <= int(sample_index) < sample_count
        ok = ok and int(batch.ticker.shape[0]) == sample_count and int(batch.origin_ordinal.shape[0]) == sample_count and int(batch.origin_timestamp_us.shape[0]) == sample_count
        source_part_key = str(identity.get("source_part_key", ""))
        if source_part_key:
            parts = source_part_key.split("|")
            ok = ok and len(parts) >= 3 and str(identity["ticker"]) == parts[1]
        checks.append(_check("identity_contract", "pass" if ok else "fail", "batch identity arrays align with sampled row"))

    def _check_source_event_window(self, rows: Sequence[Mapping[str, Any]], origin_ordinal: int, origin_timestamp_us: int, expected_length: int, checks: list[dict[str, Any]]) -> None:
        if expected_length <= 0:
            checks.append(_check("source_event_window", "skip", "raw_event_stream is empty"))
            return
        if len(rows) != expected_length:
            checks.append(_check("source_event_window", "fail", f"expected {expected_length:,} source rows, found {len(rows):,}"))
            return
        ordinals = np.asarray([int(row["ordinal"]) for row in rows], dtype=np.int64)
        timestamps = np.asarray([int(row["timestamp_us"]) for row in rows], dtype=np.int64)
        ok = bool(ordinals[-1] == int(origin_ordinal))
        ok = ok and bool(timestamps[-1] == int(origin_timestamp_us))
        ok = ok and bool(np.all(np.diff(ordinals) == 1))
        ok = ok and bool(np.all(np.diff(timestamps) >= 0))
        checks.append(_check("source_event_window", "pass" if ok else "fail", "source event window is contiguous, ordered, and ends at origin"))

    def _check_raw_event_stream(self, batch: Any, sample_index: int, rows: Sequence[Mapping[str, Any]], checks: list[dict[str, Any]]) -> None:
        stream = getattr(batch, "raw_event_stream", np.asarray([]))
        feature_names = tuple(str(name) for name in getattr(batch, "raw_event_stream_feature_names", ()) or ())
        if stream.ndim != 3 or not feature_names or not rows:
            checks.append(_check("raw_event_stream_tensor", "skip", "raw stream tensor or feature names are unavailable"))
            return
        if int(stream.shape[1]) != len(rows):
            checks.append(_check("raw_event_stream_tensor", "fail", f"tensor length={stream.shape[1]} source rows={len(rows)}"))
            return
        mismatches: list[str] = []
        values = stream[int(sample_index)]
        for column in EVENT_SOURCE_COLUMNS:
            if column == "ticker" or column not in feature_names:
                continue
            idx = feature_names.index(column)
            expected = np.asarray([row[column] for row in rows], dtype=np.float32)
            actual = values[:, idx].astype(np.float32, copy=False)
            if not bool(np.allclose(actual, expected, rtol=float(self.config.compare_rtol), atol=float(self.config.compare_atol), equal_nan=True)):
                mismatches.append(column)
        checks.append(_check("raw_event_stream_tensor", "pass" if not mismatches else "fail", "physical event columns match ClickHouse", {"mismatches": mismatches[:8]}))

    def _check_future_label_grid(self, batch: Any, sample_index: int, origin_timestamp_us: int, checks: list[dict[str, Any]]) -> None:
        labels = getattr(batch, "intraday_labels", {})
        start = np.asarray(labels.get("label_grid_start_timestamp_us", []))
        end = np.asarray(labels.get("label_grid_end_timestamp_us", []))
        available = np.asarray(labels.get("available", []))
        if start.ndim != 2 or end.ndim != 2:
            checks.append(_check("future_label_grid", "skip", "label grid timestamps are not materialized"))
            return
        starts = start[int(sample_index)].astype(np.int64, copy=False)
        ends = end[int(sample_index)].astype(np.int64, copy=False)
        avail = available[int(sample_index)].astype(bool, copy=False) if available.ndim == 2 else np.zeros_like(starts, dtype=bool)
        ok = bool(np.all(ends[starts > 0] > starts[starts > 0]))
        ok = ok and bool(np.all(starts[avail] > int(origin_timestamp_us)))
        ok = ok and bool(np.all(ends[avail] > int(origin_timestamp_us)))
        checks.append(_check("future_label_grid", "pass" if ok else "fail", "future label windows are strictly after origin and internally ordered"))

    def _check_future_intraday_labels(self, batch: Any, sample_index: int, rows: Sequence[Mapping[str, Any]], checks: list[dict[str, Any]]) -> None:
        labels = getattr(batch, "intraday_labels", {})
        start = np.asarray(labels.get("label_grid_start_timestamp_us", []))
        end = np.asarray(labels.get("label_grid_end_timestamp_us", []))
        if start.ndim != 2 or end.ndim != 2:
            checks.append(_check("future_intraday_labels", "skip", "label grid timestamps are not materialized"))
            return
        starts = start[int(sample_index)].astype(np.int64, copy=False)
        ends = end[int(sample_index)].astype(np.int64, copy=False)
        mismatches: list[str] = []
        for hidx, (left, right) in enumerate(zip(starts, ends)):
            if int(left) <= 0 or int(right) <= int(left):
                continue
            window = [row for row in rows if int(left) <= int(row["timestamp_us"]) < int(right)]
            expected_bars = _aggregate_event_rows_by_family(window)
            family_counts = [int(value.get("event_count", 0)) for value in expected_bars.values()]
            event_count = max(family_counts, default=0)
            if "event_count" in labels:
                actual = int(np.asarray(labels["event_count"])[int(sample_index), hidx])
                if actual != event_count:
                    mismatches.append(f"h{hidx}:event_count {actual}!={event_count}")
            if "last_event_timestamp_us" in labels and event_count:
                actual_ts = int(np.asarray(labels["last_event_timestamp_us"])[int(sample_index), hidx])
                expected_ts = max(int(value.get("last_event_timestamp_us", 0)) for value in expected_bars.values())
                if actual_ts != expected_ts:
                    mismatches.append(f"h{hidx}:last_ts {actual_ts}!={expected_ts}")
            for family in BAR_FAMILY_KEYS:
                tensor = getattr(batch, "future_bar_values", {}).get(family)
                mask = getattr(batch, "future_bar_masks", {}).get(family)
                if tensor is None or mask is None:
                    continue
                expected = expected_bars.get(family)
                actual_available = bool(mask[int(sample_index), hidx])
                expected_available = expected is not None and int(expected.get("event_count", 0)) > 0
                if actual_available != expected_available:
                    mismatches.append(f"h{hidx}:{family}_available {actual_available}!={expected_available}")
                    continue
                if expected is None or not actual_available:
                    continue
                fields = BAR_SOURCE_FEATURE_KEYS[family]
                for fidx, field_name in enumerate(fields):
                    actual = float(tensor[int(sample_index), hidx, fidx])
                    exp = float(expected.get(field_name, 0.0))
                    if not np.isclose(actual, exp, rtol=float(self.config.compare_rtol), atol=max(float(self.config.compare_atol), 1e-3)):
                        mismatches.append(f"h{hidx}:{family}_{field_name} {actual}!={exp}")
                        break
        checks.append(_check("future_intraday_labels", "pass" if not mismatches else "fail", "future bars and label counts match source event range", {"mismatches": mismatches[:12]}))

    def _check_backward_intraday_bars(self, batch: Any, sample_index: int, rows: Sequence[Mapping[str, Any]], checks: list[dict[str, Any]]) -> None:
        bars = getattr(batch, "bar_inputs", {}).get("ticker_intraday_bars")
        if not bars:
            checks.append(_check("backward_intraday_bars", "skip", "ticker_intraday_bars is not materialized"))
            return
        time_features = np.asarray(bars.get("time_features", []))
        masks = {family: np.asarray(bars.get(f"{family}_mask", [])) for family in BAR_FAMILY_KEYS}
        values = {family: np.asarray(bars.get(f"{family}_values", [])) for family in BAR_FAMILY_KEYS}
        if not values["trade"].size:
            checks.append(_check("backward_intraday_bars", "skip", "family bar tensors are empty"))
            return
        labels = getattr(batch, "intraday_labels", {})
        horizons = tuple(str(item) for item in getattr(batch, "future_intraday_bar_horizons", ()) or DEFAULT_INTRADAY_LABEL_HORIZONS)
        specs = _intraday_horizon_specs(horizons)
        origin_ts = int(batch.origin_timestamp_us[int(sample_index)])
        origin_local = dt.datetime.fromtimestamp(origin_ts / 1_000_000, tz=dt.timezone.utc).astimezone(NY_TZ)
        session_us = (origin_local.hour * 3600 + origin_local.minute * 60 + origin_local.second) * 1_000_000 + origin_local.microsecond
        local_midnight_us = origin_ts - session_us
        mismatches: list[str] = []
        for hidx, (_name, _horizon_us, resolution_us, bucket_count, is_eod) in enumerate(specs):
            origin_bucket = session_us // int(resolution_us)
            last_bucket = origin_bucket - 1
            first_bucket = 0 if is_eod else max(0, last_bucket - int(bucket_count) + 1)
            if last_bucket < first_bucket:
                expected_by_family: dict[str, dict[str, float]] = {}
            else:
                left = local_midnight_us + first_bucket * int(resolution_us)
                right = local_midnight_us + (last_bucket + 1) * int(resolution_us)
                expected_by_family = _aggregate_event_rows_by_family([row for row in rows if int(left) <= int(row["timestamp_us"]) < int(right)])
            for family in BAR_FAMILY_KEYS:
                expected = expected_by_family.get(family)
                actual_available = bool(masks[family][int(sample_index), hidx]) if masks[family].ndim == 2 else False
                expected_available = expected is not None and int(expected.get("event_count", 0)) > 0
                if actual_available != expected_available:
                    mismatches.append(f"h{hidx}:{family}_available {actual_available}!={expected_available}")
                    continue
                if expected is None or not actual_available:
                    continue
                fields = BAR_SOURCE_FEATURE_KEYS[family]
                for fidx, field_name in enumerate(fields):
                    actual = float(values[family][int(sample_index), hidx, fidx])
                    exp = float(expected.get(field_name, 0.0))
                    if not np.isclose(actual, exp, rtol=float(self.config.compare_rtol), atol=max(float(self.config.compare_atol), 1e-3)):
                        mismatches.append(f"h{hidx}:{family}_{field_name} {actual}!={exp}")
                        break
        del time_features, labels
        checks.append(_check("backward_intraday_bars", "pass" if not mismatches else "fail", "backward intraday bars match source event range", {"mismatches": mismatches[:12]}))

    def _check_sparse_context_asof(self, batch: Any, sample_index: int, origin_timestamp_us: int, checks: list[dict[str, Any]]) -> None:
        failures: list[str] = []
        for name, payload in getattr(batch, "text_inputs", {}).items():
            timestamps = np.asarray(payload.get("item_timestamp_us", []))
            mask = np.asarray(payload.get("item_mask", []))
            chunk_mask = np.asarray(payload.get("chunk_mask", []))
            if timestamps.ndim != 2:
                continue
            ts = timestamps[int(sample_index)]
            item_mask = mask[int(sample_index)].astype(bool, copy=False) if mask.ndim == 2 else ts > 0
            if bool(np.any(ts[item_mask] > int(origin_timestamp_us))):
                failures.append(f"{name}:future_timestamp")
            if bool(np.any(ts[~item_mask] != 0)):
                failures.append(f"{name}:padded_timestamp_nonzero")
            if chunk_mask.ndim == 3:
                cm = chunk_mask[int(sample_index)].astype(bool, copy=False)
                if bool(np.any(cm[~item_mask])):
                    failures.append(f"{name}:padded_chunk_mask_true")
        xbrl = getattr(batch, "xbrl_inputs", {})
        if xbrl:
            self._check_timestamp_mask_payload("xbrl", xbrl, sample_index, origin_timestamp_us, failures, timestamp_key=None)
        corp = getattr(batch, "corporate_action_inputs", {})
        if corp:
            self._check_timestamp_mask_payload("corporate_actions", corp, sample_index, origin_timestamp_us, failures, timestamp_key="available_timestamp_us")
        checks.append(_check("sparse_context_asof", "pass" if not failures else "fail", "sparse contexts are latest-as-of or padded zero", {"failures": failures[:12]}))

    def _check_timestamp_mask_payload(self, name: str, payload: Mapping[str, Any], sample_index: int, origin_timestamp_us: int, failures: list[str], *, timestamp_key: str | None) -> None:
        mask = np.asarray(payload.get("mask", []))
        if mask.ndim != 2:
            return
        item_mask = mask[int(sample_index)].astype(bool, copy=False)
        if timestamp_key:
            timestamps = np.asarray(payload.get(timestamp_key, []))
            if timestamps.ndim == 2:
                ts = timestamps[int(sample_index)]
                if bool(np.any(ts[item_mask] > int(origin_timestamp_us))):
                    failures.append(f"{name}:future_timestamp")
                if bool(np.any(ts[~item_mask] != 0)):
                    failures.append(f"{name}:padded_timestamp_nonzero")
        for key, value in payload.items():
            arr = np.asarray(value)
            if arr.dtype == object or arr.shape[:1] == (0,):
                continue
            if arr.ndim >= 2 and arr.shape[0] > int(sample_index) and arr.shape[1] == item_mask.shape[0] and np.issubdtype(arr.dtype, np.floating):
                if not bool(np.isfinite(arr[int(sample_index)][item_mask]).all()):
                    failures.append(f"{name}:{key}:nonfinite")

    def _check_daily_and_corporate_consistency(self, batch: Any, sample_index: int, origin_timestamp_us: int, checks: list[dict[str, Any]]) -> None:
        failures: list[str] = []
        for name, payload in getattr(batch, "bar_inputs", {}).items():
            mask = np.asarray(payload.get("mask", []))
            values = np.asarray(payload.get("values", []))
            if mask.size and values.size and np.issubdtype(values.dtype, np.floating):
                selected = values[int(sample_index)][mask[int(sample_index)].astype(bool, copy=False)]
                if selected.size and not bool(np.isfinite(selected).all()):
                    failures.append(f"{name}:nonfinite_values")
        corporate_inputs = getattr(batch, "corporate_action_inputs", {})
        corporate_labels = getattr(batch, "corporate_action_labels", {})
        days = tuple(int(day) for day in getattr(batch, "corporate_action_label_days", ()) or ())
        if corporate_inputs and corporate_labels and days:
            derived = _derive_corporate_labels_from_batch_inputs(corporate_inputs, sample_index, int(origin_timestamp_us), days)
            for key, expected in derived.items():
                actual = np.asarray(corporate_labels.get(key, []))
                if actual.ndim == 2 and not bool(np.array_equal(actual[int(sample_index)].astype(bool, copy=False), expected)):
                    failures.append(f"corporate_label:{key}")
        checks.append(_check("daily_global_corporate_consistency", "pass" if not failures else "fail", "daily/global bars are finite and corporate labels match context-derived horizons", {"failures": failures[:12]}))

    def _check_massive_rest_spot(self, ticker: str, origin_timestamp_us: int, checks: list[dict[str, Any]]) -> None:
        if int(self.config.rest_samples) <= 0 or self.rest_checked >= int(self.config.rest_samples):
            checks.append(_check("massive_rest_spot_check", "skip", "REST spot check disabled or sample limit reached"))
            return
        api_key = os.environ.get(str(self.config.massive_api_key_env), "").strip()
        if not api_key:
            checks.append(_check("massive_rest_spot_check", "skip", f"{self.config.massive_api_key_env} is not set"))
            return
        try:
            window_ns = 1_000_000_000
            timestamp_ns = int(origin_timestamp_us) * 1000
            params = {
                "timestamp.gte": str(max(0, timestamp_ns - window_ns)),
                "timestamp.lte": str(timestamp_ns + window_ns),
                "limit": "1",
                "apiKey": api_key,
            }
            base = self.config.massive_base_url.rstrip("/")
            responses: dict[str, dict[str, Any]] = {}
            for endpoint in ("trades", "quotes"):
                url = f"{base}/v3/{endpoint}/{parse.quote(ticker)}?{parse.urlencode(params)}"
                req = request.Request(url, method="GET")
                with request.urlopen(req, timeout=10) as response:
                    responses[endpoint] = json.loads(response.read().decode("utf-8", errors="replace"))
            self.rest_checked += 1
            statuses = {endpoint: str(payload.get("status", "")).upper() for endpoint, payload in responses.items()}
            ok = bool(statuses) and all(status in {"OK", "DELAYED"} for status in statuses.values())
            counts = {endpoint: payload.get("resultsCount") for endpoint, payload in responses.items()}
            checks.append(_check("massive_rest_spot_check", "pass" if ok else "fail", "Massive quote and trade endpoints returned parseable responses near origin", {"statuses": statuses, "resultsCount": counts}))
        except Exception as exc:  # noqa: BLE001
            checks.append(_check("massive_rest_spot_check", "fail" if self.config.strict else "skip", f"Massive REST check error: {exc!r}"))


def _aggregate_event_rows_by_family(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[tuple[float, float, int]]] = {family: [] for family in BAR_FAMILY_KEYS}
    for row in rows:
        meta = int(row["event_meta"])
        event_type = meta & 1
        primary_scale = 10000.0 if ((meta // 2) % 2) > 0 else 100.0
        secondary_scale = 10000.0 if ((meta // 4) % 2) > 0 else 100.0
        if event_type == 1:
            price = float(row["price_primary_int"]) / primary_scale
            size = float(row["size_primary"])
            family = "trade"
            if price > 0:
                grouped[family].append((price, size, int(row["timestamp_us"])))
        else:
            bid = float(row["price_secondary_int"]) / secondary_scale
            bid_size = float(row["size_secondary"])
            ask = float(row["price_primary_int"]) / primary_scale
            ask_size = float(row["size_primary"])
            if bid > 0:
                grouped["quote_bid"].append((bid, bid_size, int(row["timestamp_us"])))
            if ask > 0:
                grouped["quote_ask"].append((ask, ask_size, int(row["timestamp_us"])))
    out: dict[str, dict[str, float]] = {}
    for family, values in grouped.items():
        if not values:
            continue
        prices = np.asarray([item[0] for item in values], dtype=np.float64)
        sizes = np.asarray([item[1] for item in values], dtype=np.float64)
        timestamps = np.asarray([item[2] for item in values], dtype=np.int64)
        payload = {
            "open": float(prices[0]),
            "close": float(prices[-1]),
            "high": float(np.max(prices)),
            "low": float(np.min(prices)),
            "event_count": float(len(values)),
            "last_event_timestamp_us": float(timestamps[-1]),
        }
        if family == "trade":
            payload["size_sum"] = float(np.sum(sizes))
        else:
            payload.update(
                {
                    "size_open": float(sizes[0]),
                    "size_close": float(sizes[-1]),
                    "size_high": float(np.max(sizes)),
                    "size_low": float(np.min(sizes)),
                }
            )
        out[family] = payload
    return out


def _derive_corporate_labels_from_batch_inputs(inputs: Mapping[str, Any], sample_index: int, origin_timestamp_us: int, days: Sequence[int]) -> dict[str, np.ndarray]:
    mask = np.asarray(inputs.get("mask", []))
    effective = np.asarray(inputs.get("effective_timestamp_us", []))
    numeric = np.asarray(inputs.get("numeric_features", []))
    names = tuple(str(name) for name in np.asarray(inputs.get("numeric_feature_names", []), dtype=object).tolist())
    out = {
        "future_split_flag": np.zeros((len(days),), dtype=np.bool_),
        "future_reverse_split_flag": np.zeros((len(days),), dtype=np.bool_),
        "future_forward_split_flag": np.zeros((len(days),), dtype=np.bool_),
        "future_dividend_ex_flag": np.zeros((len(days),), dtype=np.bool_),
        "future_special_dividend_ex_flag": np.zeros((len(days),), dtype=np.bool_),
        "future_any_corporate_action_flag": np.zeros((len(days),), dtype=np.bool_),
    }
    if mask.ndim != 2 or effective.ndim != 2 or numeric.ndim != 3:
        return out
    item_mask = mask[int(sample_index)].astype(bool, copy=False)
    eff = effective[int(sample_index)].astype(np.int64, copy=False)
    num = numeric[int(sample_index)]
    flag_source = {
        "future_split_flag": "is_split",
        "future_reverse_split_flag": "is_reverse_split",
        "future_forward_split_flag": "is_forward_split",
        "future_dividend_ex_flag": "is_dividend",
        "future_special_dividend_ex_flag": "is_special_dividend",
    }
    for didx, day in enumerate(days):
        horizon_end = int(origin_timestamp_us) + int(day) * 86_400_000_000
        future = item_mask & (eff > int(origin_timestamp_us)) & (eff <= horizon_end)
        out["future_any_corporate_action_flag"][didx] = bool(np.any(future))
        for label, source_name in flag_source.items():
            if source_name in names:
                out[label][didx] = bool(np.any(future & (num[:, names.index(source_name)] > 0.5)))
    return out


def _future_label_bounds(batch: Any, sample_index: int) -> tuple[int, int]:
    labels = getattr(batch, "intraday_labels", {})
    start = np.asarray(labels.get("label_grid_start_timestamp_us", []))
    end = np.asarray(labels.get("label_grid_end_timestamp_us", []))
    if start.ndim != 2 or end.ndim != 2:
        return 0, 0
    starts = start[int(sample_index)].astype(np.int64, copy=False)
    ends = end[int(sample_index)].astype(np.int64, copy=False)
    valid = (starts > 0) & (ends > starts)
    if not bool(valid.any()):
        return 0, 0
    return int(np.min(starts[valid])), int(np.max(ends[valid]))


def _backward_bar_bounds(batch: Any, sample_index: int) -> tuple[int, int]:
    bars = getattr(batch, "bar_inputs", {}).get("ticker_intraday_bars") if hasattr(batch, "bar_inputs") else None
    if not bars:
        return 0, 0
    origin_ts = int(batch.origin_timestamp_us[int(sample_index)])
    origin_local = dt.datetime.fromtimestamp(origin_ts / 1_000_000, tz=dt.timezone.utc).astimezone(NY_TZ)
    session_us = (origin_local.hour * 3600 + origin_local.minute * 60 + origin_local.second) * 1_000_000 + origin_local.microsecond
    local_midnight_us = origin_ts - session_us
    horizons = tuple(str(item) for item in getattr(batch, "future_intraday_bar_horizons", ()) or DEFAULT_INTRADAY_LABEL_HORIZONS)
    starts: list[int] = []
    ends: list[int] = []
    for _name, _horizon_us, resolution_us, bucket_count, is_eod in _intraday_horizon_specs(horizons):
        origin_bucket = session_us // int(resolution_us)
        last_bucket = origin_bucket - 1
        first_bucket = 0 if is_eod else max(0, last_bucket - int(bucket_count) + 1)
        if last_bucket < first_bucket:
            continue
        starts.append(int(local_midnight_us + first_bucket * int(resolution_us)))
        ends.append(int(local_midnight_us + (last_bucket + 1) * int(resolution_us)))
    if not starts or not ends:
        return 0, 0
    return min(starts), max(ends)


def _candidate_years_for_part(source_part_key: str, fallback_year: int) -> tuple[int, ...]:
    year = int(fallback_year)
    try:
        month = str(source_part_key).split("|", 1)[0]
        year = int(month[:4])
        if month.endswith("-01") and year > 2019:
            return (year - 1, year)
    except Exception:
        pass
    return (year,)


def _timestamp_year_guess_from_part(source_part_key: str) -> int:
    try:
        return int(str(source_part_key).split("|", 1)[0][:4])
    except Exception:
        return dt.datetime.now(dt.timezone.utc).year


def _check(name: str, status: str, message: str, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": str(name), "status": str(status), "message": str(message), "details": dict(details or {})}


def _summarize_checks(checks: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    passed = sum(1 for check in checks if check.get("status") == "pass")
    failed = sum(1 for check in checks if check.get("status") == "fail")
    skipped = sum(1 for check in checks if check.get("status") == "skip")
    rest_checked = sum(1 for check in checks if check.get("name") == "massive_rest_spot_check" and check.get("status") == "pass")
    return {"checked": int(passed + failed), "passed": int(passed), "failed": int(failed), "skipped": int(skipped), "rest_checked": int(rest_checked)}


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True, default=str) + "\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")


def _stable_seed(*items: object) -> int:
    import hashlib

    digest = hashlib.blake2b("|".join(str(item) for item in items).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)
