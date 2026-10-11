"""Shared normalized QMD history product; no strategy-owned persistence.

Completed market clocks retain their independent price/extrema masks. Feature
intervals encode identical Float32 values losslessly, including invalid runs.
The producer's certified scope establishes covered-empty clocks; this module
never infers missing source coverage or fetches a private research feature bank.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re
import math
from hashlib import sha256
from uuid import UUID

import numpy as np
import polars as pl

from src.trading_runtime.arte_journal_schema import TableContract
from .causal_market_history import SWING_WINDOWS, prepare_causal_history
from .causal_volume_history import elapsed_volume_history
from .opening_price_reference import content_hash, require_hash, NEW_YORK

REVISION = "qmd-causal-elapsed-history-v1"
PREFIX = (("source_build_id", "String"), ("session_date", "Date"),
    ("listing_id", "String"), ("ticker", "LowCardinality(String)"),
    ("derivation_attempt_id", "UUID"), ("product_revision", "FixedString(64)"),
    ("first_bucket", "UInt32"), ("stop_bucket", "UInt32"))
ORDER = "source_build_id,session_date,listing_id,derivation_attempt_id,product_revision,first_bucket,stop_bucket"
FIELD = TableContract("qmd_causal_history_field_v1", PREFIX + (
    ("field_id", "UInt32"), ("field_name", "String"), ("unit", "LowCardinality(String)")),
    "toYYYYMM(session_date)", ORDER + ",field_id")
CLOCK = TableContract("qmd_causal_history_clock_v1", PREFIX + (
    ("bucket_index", "UInt32"), ("source_known", "UInt8"),
    ("observed", "UInt8"), ("extremes_valid", "UInt8"),
    ("reference_close", "Nullable(Float64)"), ("share_volume", "Float64"),
    ("execution_notional", "Float64"), ("rolling_volume", "Float64")),
    "toYYYYMM(session_date)", ORDER + ",bucket_index")
VALUE = TableContract("qmd_causal_history_value_v1", PREFIX + (
    ("field_id", "UInt32"), ("first_ordinal", "UInt32"), ("stop_ordinal", "UInt32"),
    ("value", "Nullable(Float32)"), ("value_bits", "Nullable(UInt32)"), ("valid", "UInt8")),
    "toYYYYMM(session_date)", ORDER + ",field_id,first_ordinal")
COVERAGE = TableContract("qmd_causal_history_coverage_v1", PREFIX + (
    ("source_bar_attempt_id", "UUID"), ("source_bar_unit_hash", "FixedString(64)"),
    ("source_liquidity_attempt_id", "UUID"), ("source_liquidity_hash", "FixedString(64)"),
    ("opening_reference_hash", "FixedString(64)"), ("market_plan_hash", "FixedString(64)"),
    ("producer_code_hash", "FixedString(64)"), ("volume_window_seconds", "UInt32"),
    ("elapsed_clock_count", "UInt32"), ("field_count", "UInt32"),
    ("clock_row_count", "UInt32"), ("value_row_count", "UInt64"),
    ("field_content_hash", "FixedString(64)"), ("clock_content_hash", "FixedString(64)"),
    ("value_content_hash", "FixedString(64)"), ("content_hash", "FixedString(64)"),
    ("certified_at", "DateTime64(6, 'UTC')")),
    "toYYYYMM(session_date)", ORDER)
TABLES = (FIELD, CLOCK, VALUE, COVERAGE)


def restore_record(table, row):
    """Canonicalize scalar wire values before verifying persisted evidence."""
    if table not in TABLES or set(row) != {n for n, _ in table.columns}:
        raise ValueError("History record differs from its central scalar schema")
    result = {}
    for name, kind in table.columns:
        value = row[name]
        if kind.startswith("Nullable("):
            if value is None:
                result[name] = None
                continue
            kind = kind[9:-1]
        if kind.startswith("UInt"):
            if type(value) is str and re.fullmatch(r"0|[1-9][0-9]*", value):
                value = int(value)
            if type(value) is not int or not 0 <= value < 2 ** int(kind[4:]):
                raise ValueError("History unsigned scalar differs")
        elif kind in {"Float32", "Float64"}:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("History floating scalar differs")
            if kind == "Float32" and abs(value) > np.finfo(np.float32).max:
                raise ValueError("History value exceeds Float32")
            value = float(np.float32(value)) if kind == "Float32" else float(value)
        elif kind == "Date":
            value = date.fromisoformat(value) if type(value) is str else value
            if type(value) is not date:
                raise ValueError("History date differs")
        elif kind.startswith("DateTime64"):
            if type(value) is str:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
            if type(value) is not datetime or value.tzinfo is None:
                raise ValueError("History timestamp must be timezone-aware")
            value = value.astimezone(timezone.utc)
        elif kind == "UUID":
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError("History UUID differs")
        elif kind == "FixedString(64)":
            require_hash(value)
        elif kind in {"String", "LowCardinality(String)"}:
            if type(value) is not str or not 1 <= len(value) <= 256:
                raise ValueError("History text scalar differs")
        else:
            raise ValueError("Unsupported history scalar type")
        result[name] = value
    if table == VALUE:
        if result["valid"] not in (0, 1):
            raise ValueError("History value validity differs")
        if result["valid"] == 0:
            if result["value"] is not None or result["value_bits"] is not None:
                raise ValueError("Invalid history values must have no numeric or bit value")
        else:
            if result["value"] is None or result["value_bits"] is None:
                raise ValueError("Valid history values need exact typed bits")
            decoded = float(np.asarray(result["value_bits"], dtype=np.uint32).view(np.float32))
            if not math.isfinite(decoded) or decoded != result["value"]:
                raise ValueError("History numeric value differs from its typed Float32 bits")
            # ClickHouse's JSON wire can emit negative zero as 0; the explicit
            # typed scalar bit pattern is the lossless value authority.
            result["value"] = decoded
    return result


def records_hash(table, rows):
    """Ordered streaming scalar digest, independent of ClickHouse wire notation."""
    digest = sha256()
    for row in rows:
        digest.update(bytes.fromhex(content_hash(restore_record(table, row))))
    return digest.hexdigest()


@dataclass(frozen=True)
class HistoryField:
    field_id: int
    name: str
    unit: str


def history_catalog():
    """Stable source catalog IDs used by the original market-only search."""
    raw = []
    for family, windows in (("recent_high", range(1, 61)), ("momentum_close", range(1, 61)),
                            ("attention_mean", range(1, 61)), ("average_move", range(2, 33))):
        raw.extend((f"history.{family}.{w}", "dollars" if family == "attention_mean" else "price")
                   for w in windows)
    raw.extend((f"history.swing_low.{left}.{right}", "price")
               for left in SWING_WINDOWS for right in SWING_WINDOWS)
    relative = [(name + ".relative_to_close", "ratio") for name, unit in raw if unit == "price"]
    return tuple(HistoryField(149 + i, name, unit) for i, (name, unit) in enumerate(raw + relative))


CATALOG = history_catalog()
BY_NAME = {field.name: field for field in CATALOG}


def selected_catalog(fields):
    if (type(fields) is not tuple or not 1 <= len(fields) <= len(CATALOG)
            or any(type(f) is not HistoryField or BY_NAME.get(f.name) != f for f in fields)
            or tuple(sorted(fields, key=lambda f: f.field_id)) != fields
            or len({f.field_id for f in fields}) != len(fields)):
        raise ValueError("History product requires a canonical shared field catalog")
    return fields


def product_revision(fields, code_hash, *, volume_window_seconds):
    selected_catalog(fields)
    require_hash(code_hash)
    if type(volume_window_seconds) is not int or not 1 <= volume_window_seconds <= 3600:
        raise ValueError("History revision requires its explicit volume window")
    return content_hash(dict(algorithm=REVISION, producer_code_hash=code_hash,
        fields=[(f.field_id, f.name, f.unit) for f in fields],
        activity_basis="completed_execution_notional", rank_basis="completed_share_volume",
        history_basis="declared_elapsed_session_origin", value_type="Float32",
        volume_window_seconds=volume_window_seconds))


@dataclass(frozen=True)
class HistoryScope:
    build_id: str
    session_date: date
    listing_id: str
    ticker: str
    attempt_id: str
    product_revision: str
    first_bucket: int
    stop_bucket: int

    def __post_init__(self):
        if (type(self.session_date) is not date
                or any(not isinstance(v, str) or not 1 <= len(v) <= 128
                       for v in (self.build_id, self.listing_id))
                or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,31}", self.ticker or "") is None
                or str(UUID(self.attempt_id)) != self.attempt_id
                or type(self.first_bucket) is not int or type(self.stop_bucket) is not int
                or not 14400 <= self.first_bucket < self.stop_bucket <= 72000):
            raise ValueError("History product requires an explicit completed session-clock scope")
        require_hash(self.product_revision)

    @property
    def clock_count(self):
        return self.stop_bucket - self.first_bucket

    def prefix(self):
        return dict(source_build_id=self.build_id, session_date=self.session_date,
            listing_id=self.listing_id, ticker=self.ticker, derivation_attempt_id=self.attempt_id,
            product_revision=self.product_revision, first_bucket=self.first_bucket, stop_bucket=self.stop_bucket)


def completed_clock(scope, bucket):
    if not scope.first_bucket <= bucket < scope.stop_bucket:
        raise ValueError("History clock is outside its declared scope")
    return (datetime.combine(scope.session_date, datetime.min.time(), NEW_YORK)
            + timedelta(seconds=bucket + 1)).astimezone(timezone.utc)


def prepare_dense_inputs(scope, bars: pl.DataFrame, liquidity: pl.DataFrame):
    """Join separately certified sparse parents onto the declared dense clocks.

    Callers must verify both complete parent certificates before this transform.
    There is no carry from clocks preceding the declared origin, matching the
    original history bank. Quote-only liquidity makes source-known explicit;
    it never creates an observed candle or an executable price.
    """
    if type(scope) is not HistoryScope:
        raise TypeError("History input requires its typed source scope")
    bar_fields = {"bucket_index", "price_valid", "extremes_valid", "close_int",
                  "high_int", "low_int", "volume"}
    liquidity_fields = {"bucket_index", "execution_notional"}
    for frame, required in ((bars, bar_fields), (liquidity, liquidity_fields)):
        if not required <= set(frame.columns) or frame.height > scope.clock_count:
            raise ValueError("History input parent exceeds its completed scope")
        if frame.select(pl.any_horizontal(pl.col(c).is_null() for c in required).any()).item():
            raise ValueError("History input parent has missing scalar values")
        if (not frame.schema["bucket_index"].is_integer()
                or frame.select((pl.col("bucket_index").is_duplicated()
                    | ~pl.col("bucket_index").is_between(scope.first_bucket, scope.stop_bucket - 1)).any()).item()):
            raise ValueError("History input parent has duplicate or out-of-scope clocks")
    for flag in ("price_valid", "extremes_valid"):
        if not bars.schema[flag].is_integer() or bars.filter(~pl.col(flag).is_in([0, 1])).height:
            raise ValueError("History input eligibility flags differ")
    if not bars.schema["volume"].is_numeric() or not liquidity.schema["execution_notional"].is_numeric():
        raise ValueError("History activity parents must have numeric scalar columns")
    for name in ("close_int", "high_int", "low_int"):
        if not bars.schema[name].is_integer() or bars.filter(pl.col(name) < 0).height:
            raise ValueError("History input requires canonical integer prices")
    grid = pl.DataFrame({"bucket_index": pl.Series(range(scope.first_bucket, scope.stop_bucket), dtype=pl.UInt32)})
    frame = (grid.join(bars.select(*sorted(bar_fields)).with_columns(
                           pl.col("bucket_index").cast(pl.UInt32), pl.lit(True).alias("bar_present")),
                       on="bucket_index", how="left", validate="1:1")
        .join(liquidity.select(*sorted(liquidity_fields)).with_columns(
                  pl.col("bucket_index").cast(pl.UInt32), pl.lit(True).alias("liquidity_present")),
              on="bucket_index", how="left", validate="1:1")
        .sort("bucket_index")
        .with_columns((pl.col("price_valid").fill_null(0) == 1).alias("observed"),
            (pl.col("extremes_valid").fill_null(0) == 1).alias("extremes_valid"),
            (pl.col("bar_present").fill_null(False) | pl.col("liquidity_present").fill_null(False))
                .cast(pl.UInt32).cum_max().cast(pl.Boolean).alias("source_known"),
            pl.col("volume").fill_null(0.).cast(pl.Float64),
            pl.col("execution_notional").fill_null(0.).cast(pl.Float64).alias("notional"))
        .with_columns(pl.when(pl.col("observed")).then(pl.col("close_int") / 10000.).otherwise(None)
                .alias("close"),
            pl.when(pl.col("extremes_valid")).then(pl.col("high_int") / 10000.).otherwise(None).alias("high"),
            pl.when(pl.col("extremes_valid")).then(pl.col("low_int") / 10000.).otherwise(None).alias("low"))
        .with_columns(pl.col("close").forward_fill().alias("reference_close"),
            pl.lit(scope.listing_id).alias("listing_id"), pl.lit(scope.session_date).alias("session_date")))
    # Compute timestamps as a vector; the offset uses the explicit session's
    # timezone authority and includes the current completed second.
    midnight = datetime.combine(scope.session_date, datetime.min.time(), NEW_YORK)
    first_us = int(midnight.timestamp()) * 1000000
    return frame.with_columns(((pl.col("bucket_index").cast(pl.Int64) + 1) * 1000000 + first_us)
        .cast(pl.Datetime("us", "UTC")).alias("boundary_at")).with_columns(pl.col("boundary_at").alias("available_at"))


@dataclass(frozen=True)
class PreparedHistory:
    scope: HistoryScope
    fields: tuple[HistoryField, ...]
    volume_window_seconds: int
    clocks: pl.DataFrame
    values: pl.DataFrame


@dataclass(frozen=True)
class HistoryParents:
    source_bar_attempt_id: str
    source_bar_unit_hash: str
    source_liquidity_attempt_id: str
    source_liquidity_hash: str
    opening_reference_hash: str
    market_plan_hash: str
    producer_code_hash: str
    volume_window_seconds: int

    def __post_init__(self):
        for name in ("source_bar_attempt_id", "source_liquidity_attempt_id"):
            value = getattr(self, name)
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError("History parent needs a canonical attempt identity")
        for name in ("source_bar_unit_hash", "source_liquidity_hash", "opening_reference_hash",
                     "market_plan_hash", "producer_code_hash"):
            require_hash(getattr(self, name))
        if type(self.volume_window_seconds) is not int or not 1 <= self.volume_window_seconds <= 3600:
            raise ValueError("History parent requires an explicit elapsed volume window")

    def payload(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def child_records(prepared, table):
    """Stream centrally typed children without creating a second full row bank."""
    prefix = prepared.scope.prefix()
    if table == FIELD:
        source = ({"field_id": f.field_id, "field_name": f.name, "unit": f.unit} for f in prepared.fields)
    elif table == CLOCK:
        source = prepared.clocks.iter_rows(named=True)
    elif table == VALUE:
        source = prepared.values.sort("field_id", "first_ordinal").iter_rows(named=True)
    else:
        raise ValueError("History child table differs from the shared product")
    for row in source:
        yield restore_record(table, {**prefix, **row})


def history_seal(prepared, parents, *, certified_at):
    """Project coverage evidence; publication must separately prove these parents."""
    if type(prepared) is not PreparedHistory or type(parents) is not HistoryParents:
        raise TypeError("History seal requires typed inputs and source pins")
    if (prepared.volume_window_seconds != parents.volume_window_seconds
            or prepared.scope.product_revision != product_revision(prepared.fields, parents.producer_code_hash,
                volume_window_seconds=parents.volume_window_seconds)):
        raise ValueError("History seal input revision or window differs")
    expand_values(prepared, first_ordinal=0, stop_ordinal=1, evaluation_block="seal-validation")
    header = {**prepared.scope.prefix(), **parents.payload(),
        "elapsed_clock_count": prepared.scope.clock_count, "field_count": len(prepared.fields),
        "clock_row_count": prepared.clocks.height, "value_row_count": prepared.values.height,
        "field_content_hash": records_hash(FIELD, child_records(prepared, FIELD)),
        "clock_content_hash": records_hash(CLOCK, child_records(prepared, CLOCK)),
        "value_content_hash": records_hash(VALUE, child_records(prepared, VALUE))}
    header["content_hash"] = content_hash(header)
    header["certified_at"] = certified_at
    return restore_record(COVERAGE, header)


def prepare_product(scope, bars, liquidity, fields, *, volume_window_seconds, producer_code_hash):
    """Calculate once per listing and compress each field without row loops."""
    selected_catalog(fields)
    if scope.product_revision != product_revision(fields, producer_code_hash, volume_window_seconds=volume_window_seconds):
        raise ValueError("History catalog/code revision differs from the source scope")
    frame = elapsed_volume_history(prepare_dense_inputs(scope, bars, liquidity), volume_window_seconds)
    history = prepare_causal_history(frame, tuple(f.name for f in fields))
    values = []
    for field in fields:
        array = history[field.name].cast(pl.Float32).to_numpy()
        valid = history[field.name + "__valid"].to_numpy() & np.isfinite(array)
        bits = np.where(valid, array.view(np.uint32), 0)
        changed = np.concatenate(([True], (bits[1:] != bits[:-1]) | (valid[1:] != valid[:-1])))
        starts = np.flatnonzero(changed).astype(np.uint32)
        stops = np.concatenate((starts[1:], [scope.clock_count])).astype(np.uint32)
        selected = array[starts]
        selected_valid = valid[starts]
        values.append(pl.DataFrame({"field_id": pl.Series([field.field_id] * len(starts), dtype=pl.UInt32),
            "first_ordinal": starts, "stop_ordinal": stops,
            "value": pl.Series(selected, dtype=pl.Float32).set(pl.Series(~selected_valid), None),
            "value_bits": pl.Series(selected.view(np.uint32), dtype=pl.UInt32).set(pl.Series(~selected_valid), None),
            "valid": pl.Series(selected_valid, dtype=pl.UInt8)}))
    clocks = frame.select("bucket_index", pl.col("source_known").cast(pl.UInt8),
        pl.col("observed").cast(pl.UInt8), pl.col("extremes_valid").cast(pl.UInt8),
        "reference_close", pl.col("volume").alias("share_volume"),
        pl.col("notional").alias("execution_notional"), "rolling_volume")
    return PreparedHistory(scope, fields, volume_window_seconds, clocks, pl.concat(values))


def expand_values(prepared: PreparedHistory, *, first_ordinal: int, stop_ordinal: int,
                  evaluation_block: str):
    """Reconstruct a bounded columnar consumer window with explicit masks."""
    scope = prepared.scope
    if not isinstance(evaluation_block, str) or not 1 <= len(evaluation_block) <= 128:
        raise ValueError("History consumer requires its explicit rule evaluation block")
    if (type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 <= first_ordinal < stop_ordinal <= scope.clock_count
            or stop_ordinal - first_ordinal > 4096):
        raise ValueError("History consumer window must be bounded within certified coverage")
    ordinals = np.arange(first_ordinal, stop_ordinal, dtype=np.uint32)
    selected_catalog(prepared.fields)
    expected_ids = {f.field_id for f in prepared.fields}
    if set(prepared.values["field_id"].unique().to_list()) != expected_ids:
        raise ValueError("History interval fields differ from the declared catalog")
    clocks = prepared.clocks
    if (clocks.height != scope.clock_count or clocks["bucket_index"].null_count()
            or not np.array_equal(clocks["bucket_index"].to_numpy(),
                                  np.arange(scope.first_bucket, scope.stop_bucket))):
        raise ValueError("History completed clock coverage differs from the declared scope")
    for flag in ("source_known", "observed", "extremes_valid"):
        if clocks[flag].null_count() or clocks.filter(~pl.col(flag).is_in([0, 1])).height:
            raise ValueError("History clock eligibility flags differ")
    if clocks.filter((pl.col("observed") > pl.col("source_known"))
                     | (pl.col("extremes_valid") > pl.col("observed"))).height:
        raise ValueError("History clock eligibility masks are inconsistent")
    for name in ("share_volume", "execution_notional", "rolling_volume"):
        if clocks[name].null_count() or clocks.filter(~pl.col(name).is_finite() | (pl.col(name) < 0)).height:
            raise ValueError("History clock activity must be finite and nonnegative")
    if clocks.filter(pl.col("reference_close").is_not_null()
                     & (~pl.col("reference_close").is_finite() | (pl.col("reference_close") <= 0))).height:
        raise ValueError("History clock reference price must be finite and positive")
    output = clocks.slice(first_ordinal, stop_ordinal - first_ordinal).with_columns(
        pl.Series("observed_ordinal", ordinals))
    midnight_us = int(datetime.combine(scope.session_date, datetime.min.time(), NEW_YORK).timestamp()) * 1000000
    output = output.with_columns(((pl.col("bucket_index").cast(pl.Int64) + 1) * 1000000 + midnight_us)
        .cast(pl.Datetime("us", "UTC")).alias("boundary_at"))
    output = output.with_columns(pl.col("boundary_at").alias("available_at"))
    for field in prepared.fields:
        runs = prepared.values.filter(pl.col("field_id") == field.field_id).sort("first_ordinal")
        start = runs["first_ordinal"].to_numpy()
        stop = runs["stop_ordinal"].to_numpy()
        if (not len(start) or start[0] != 0 or stop[-1] != scope.clock_count
                or np.any(start >= stop) or not np.array_equal(start[1:], stop[:-1])):
            raise ValueError("History field interval coverage is missing, duplicate or discontinuous")
        indices = np.searchsorted(start, ordinals, side="right") - 1
        valid = runs["valid"].to_numpy()[indices]
        values = runs["value"].to_numpy()[indices]
        if "value_bits" not in runs.columns:
            raise ValueError("History intervals lack their typed Float32 bits")
        bits_valid = runs["value_bits"].is_not_null().to_numpy()
        all_values = runs["value"].to_numpy()
        encoded_bits = runs["value_bits"].fill_null(0).cast(pl.UInt32).to_numpy()
        if (np.any(bits_valid != (runs["valid"].to_numpy() == 1))
                or np.any(all_values[bits_valid].astype(np.float32).view(np.uint32) != encoded_bits[bits_valid])):
            raise ValueError("History interval Float32 bits differ from values or validity")
        if (runs["valid"].null_count() or runs.filter(~pl.col("valid").is_in([0, 1])).height
                or runs.filter((pl.col("valid") == 1) & ~pl.col("value").is_finite()).height
                or np.any((valid == 1) & ~np.isfinite(values))
                or runs.filter((pl.col("valid") == 0) != pl.col("value").is_null()).height):
            raise ValueError("History interval value/validity semantics differ")
        output = output.with_columns(pl.Series(field.name, values, dtype=pl.Float32),
                                      pl.Series(field.name + "__valid", valid.astype(bool)))
    return output.with_columns(pl.lit(scope.listing_id).alias("listing_id"),
        pl.lit(evaluation_block).alias("evaluation_block"))
