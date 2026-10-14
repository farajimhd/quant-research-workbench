"""QMD History transport for cold-certified columnar market inputs.

This adapter reconstructs typed intervals, not market features. It consumes a
verified producer pin and returns dense Polars columns to scanner/rule stages.
Historical HTTP I/O runs off the app event loop using the existing transport.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json

import numpy as np
import polars as pl

from src.market_engine import columnar_history_product as c
from .qmd_columnar_history import CertifiedHistoryPin
from .qmd_gateway_client import qmd_history_post_json, qmd_history_get_json

CONTRACT = "qmd-causal-history-window-v1"
PATH = "/features/columnar-history-window"
CLOCK_FIELDS = ("bucket_index", "source_known", "observed", "extremes_valid", "reference_close",
                "share_volume", "execution_notional", "rolling_volume")
RESPONSE_FIELDS = {"contract", "coverage", "first_ordinal", "stop_ordinal", "observed_ordinal", "fields", *CLOCK_FIELDS}


def verify_history_service(*, expected_source_fingerprint, history_get=None):
    """Require the actual running source version before admitting a new run."""
    c.require_hash(expected_source_fingerprint)
    health = (history_get or qmd_history_get_json)("/health", timeout=15.)
    if (type(health) is not dict or health.get("running") is not True or health.get("status") != "ready"
            or health.get("service") != "qmd_history_gateway" or health.get("host_role") != "historical"
            or health.get("columnar_history_contract") != CONTRACT
            or health.get("columnar_history_cold_certification_required") is not True
            or health.get("source_fingerprint") != expected_source_fingerprint):
        raise ValueError("QMD history running source or columnar capability differs from the approved run")
    return expected_source_fingerprint


async def verify_history_service_async(**authority):
    return await _owned_worker(verify_history_service, **authority)


def selected_history_fields(pin, field_names=None):
    if type(pin) is not CertifiedHistoryPin:
        raise TypeError("QMD history transport requires its cold-certified pin")
    if field_names is None:
        return pin.fields
    if (type(field_names) is not tuple or any(type(name) is not str for name in field_names)
            or len(set(field_names)) != len(field_names)):
        raise ValueError("QMD history field selection must be explicit and unique")
    names = set(field_names)
    selected = tuple(field for field in pin.fields if field.name in names)
    if len(selected) != len(names):
        raise ValueError("QMD history field selection is outside its certified catalogue")
    return selected


def window_request(pin, *, first_ordinal, stop_ordinal, as_of, field_names=None):
    if type(pin) is not CertifiedHistoryPin:
        raise TypeError("QMD history transport requires its cold-certified pin")
    fields = selected_history_fields(pin, field_names)
    if (type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 <= first_ordinal < stop_ordinal <= pin.scope.clock_count
            or stop_ordinal - first_ordinal > 4096
            or len(fields) * (stop_ordinal - first_ordinal) > 65536
            or type(as_of) is not datetime or as_of.tzinfo is None or as_of.utcoffset() is None):
        raise ValueError("QMD history transport needs a bounded completed window")
    if c.completed_clock(pin.scope, pin.scope.first_bucket + stop_ordinal - 1) > as_of:
        raise ValueError("QMD history window extends beyond its as-of clock")
    scope = pin.scope.prefix()
    scope["session_date"] = scope["session_date"].isoformat()
    return dict(scope=scope, expected_content_hash=pin.header["content_hash"],
        field_ids=[f.field_id for f in fields], first_ordinal=first_ordinal, stop_ordinal=stop_ordinal,
        as_of=as_of.astimezone(timezone.utc).isoformat(timespec="microseconds"))


def decode_window(pin, response, *, first_ordinal, stop_ordinal, evaluation_block, field_names=None):
    if type(pin) is not CertifiedHistoryPin:
        raise TypeError("QMD history response requires its cold-certified pin")
    fields = selected_history_fields(pin, field_names)
    if type(first_ordinal) is not int or type(stop_ordinal) is not int:
        raise ValueError("QMD history window ordinals must be typed integers")
    if not isinstance(evaluation_block, str) or not 1 <= len(evaluation_block) <= 128:
        raise ValueError("QMD history needs an explicit rule evaluation-block identity")
    if (type(response) is not dict or set(response) != RESPONSE_FIELDS or response["contract"] != CONTRACT
            or type(response["first_ordinal"]) is not int or type(response["stop_ordinal"]) is not int
            or response["first_ordinal"] != first_ordinal or response["stop_ordinal"] != stop_ordinal
            or type(response["coverage"]) is not dict
            or c.restore_record(c.COVERAGE, response["coverage"]) != dict(pin.header)):
        raise ValueError("QMD history response differs from the exact pinned product or window")
    if len(json.dumps(response, allow_nan=False).encode()) > 8 * 1024 * 1024:
        raise ValueError("QMD history response exceeds its byte bound")
    n = stop_ordinal - first_ordinal
    if (not 0 <= first_ordinal < stop_ordinal <= pin.scope.clock_count or n > 4096
            or len(fields) * n > 65536
            or any(type(response[name]) is not list or len(response[name]) != n for name in (*CLOCK_FIELDS, "observed_ordinal"))):
        raise ValueError("QMD history response column lengths differ")
    for name in ("source_known", "observed", "extremes_valid"):
        if any(type(v) is not bool for v in response[name]):
            raise ValueError("QMD history clock validity must be Boolean")
    for name in ("bucket_index", "observed_ordinal"):
        if any(type(v) is not int for v in response[name]):
            raise ValueError("QMD history clocks must be typed integers")
    if (response["observed_ordinal"] != list(range(first_ordinal, stop_ordinal))
            or response["bucket_index"] != list(range(pin.scope.first_bucket + first_ordinal, pin.scope.first_bucket + stop_ordinal))):
        raise ValueError("QMD history clocks are missing, duplicate or reordered")
    for name in ("reference_close", "share_volume", "execution_notional", "rolling_volume"):
        if any(type(v) not in (int, float) and not (name == "reference_close" and v is None) for v in response[name]):
            raise ValueError("QMD history activity or mark must be a typed numeric scalar")
    frame = pl.DataFrame({name: pl.Series(name, response[name], dtype=(pl.UInt32 if name == "bucket_index" else
        pl.Boolean if name in {"source_known", "observed", "extremes_valid"} else pl.Float64)) for name in CLOCK_FIELDS})
    frame = frame.with_columns(*(pl.col(name).cast(pl.UInt8) for name in ("source_known", "observed", "extremes_valid")))
    if frame.filter((pl.col("observed") > pl.col("source_known")) | (pl.col("extremes_valid") > pl.col("observed"))
            | pl.any_horizontal(~pl.col(name).is_finite() | (pl.col(name) < 0) for name in
                ("share_volume", "execution_notional", "rolling_volume"))
            | (pl.col("share_volume") > 2 ** 53) | (pl.col("rolling_volume") > 2 ** 53)
            | (pl.col("reference_close").is_not_null() & (~pl.col("reference_close").is_finite() | (pl.col("reference_close") <= 0)))
            | ((pl.col("observed") == 1) & pl.col("reference_close").is_null())).height:
        raise ValueError("QMD history clock market validity differs")
    frame = frame.with_columns(pl.Series("observed_ordinal", response["observed_ordinal"], dtype=pl.UInt32))
    frame = frame.with_columns(((pl.col("bucket_index").cast(pl.Int64) + 1) * 1000000 + c.session_clock_anchor_us(pin.scope))
        .cast(pl.Datetime("us", "UTC")).alias("boundary_at")).with_columns(pl.col("boundary_at").alias("available_at"))
    if type(response["fields"]) is not list or len(response["fields"]) != len(fields):
        raise ValueError("QMD history field population differs")
    ordinals = frame["observed_ordinal"].to_numpy()
    for field, runs in zip(fields, response["fields"], strict=True):
        if (type(runs) is not dict or set(runs) != {"field_id", "field_name", "unit", "first_ordinal", "stop_ordinal", "value_bits", "valid"}
                or type(runs["field_id"]) is not int or (runs["field_id"], runs["field_name"], runs["unit"]) != (field.field_id, field.name, field.unit)
                or type(runs["first_ordinal"]) is not list or not 1 <= len(runs["first_ordinal"]) <= n
                or any(type(runs[name]) is not list or len(runs[name]) != len(runs["first_ordinal"]) for name in ("stop_ordinal", "value_bits", "valid"))
                or any(type(v) is not bool for v in runs["valid"])
                or any(type(v) is not int or not 0 <= v < 2 ** 32 for name in ("first_ordinal", "stop_ordinal") for v in runs[name])
                or any(v is not None and (type(v) is not int or not 0 <= v < 2 ** 32) for v in runs["value_bits"])):
            raise ValueError("QMD history field intervals differ from the typed catalog")
        starts = np.asarray(runs["first_ordinal"], dtype=np.uint32)
        stops = np.asarray(runs["stop_ordinal"], dtype=np.uint32)
        validity = np.asarray(runs["valid"], dtype=bool)
        bits = pl.Series(runs["value_bits"], dtype=pl.UInt32)
        values = bits.fill_null(0).to_numpy().view(np.float32)
        if (starts[0] > first_ordinal or stops[0] <= first_ordinal or stops[-1] < stop_ordinal
                or np.any(starts >= stops) or np.any(stops > pin.scope.clock_count)
                or not np.array_equal(starts[1:], stops[:-1])
                or not np.array_equal(validity, bits.is_not_null().to_numpy())
                or np.any(validity & ~np.isfinite(values))):
            raise ValueError("QMD history interval coverage, bits or validity differs")
        indices = np.searchsorted(starts, ordinals, side="right") - 1
        selected_valid = validity[indices]
        frame = frame.with_columns(pl.Series(field.name, values[indices], dtype=pl.Float32)
            .set(pl.Series(~selected_valid), None), pl.Series(field.name + "__valid", selected_valid))
    return frame.with_columns(pl.lit(pin.scope.listing_id).alias("listing_id"), pl.lit(evaluation_block).alias("evaluation_block"))


def read_history_window(pin, *, first_ordinal, stop_ordinal, evaluation_block, as_of, history_post=None, field_names=None):
    if not isinstance(evaluation_block, str) or not 1 <= len(evaluation_block) <= 128:
        raise ValueError("QMD history needs an explicit rule evaluation-block identity")
    request = window_request(pin, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal, as_of=as_of, field_names=field_names)
    response = (history_post or qmd_history_post_json)(PATH, request, timeout=45.)
    return decode_window(pin, response, first_ordinal=first_ordinal, stop_ordinal=stop_ordinal, evaluation_block=evaluation_block, field_names=field_names)


async def read_history_window_async(pin, **window):
    return await _owned_worker(read_history_window, pin, **window)


async def _owned_worker(function, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()
        raise
