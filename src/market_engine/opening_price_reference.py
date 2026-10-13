"""Typed producer contract for opening-known prior regular-session prices.

This is an independent QMD reference product. Current price, all-session
closes, and an indicator's previous sample cannot substitute for its inputs.
No strategy installs or writes these centrally owned tables.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from hashlib import sha256
from functools import lru_cache
import json
import math
import re
from uuid import UUID
from zoneinfo import ZoneInfo

from src.trading_runtime.arte_journal_schema import TableContract

NEW_YORK = ZoneInfo("America/New_York")
REVISION = "qmd-opening-prior-regular-close-v1"
PREFIX = (("source_build_id", "String"), ("session_date", "Date"),
          ("reference_attempt_id", "UUID"))
REFERENCE = TableContract("qmd_opening_price_reference_v1", PREFIX + (
    ("listing_id", "String"), ("ticker", "LowCardinality(String)"),
    ("symbol_id", "String"), ("security_id", "String"),
    ("prior_source_build_id", "String"), ("prior_session_date", "Date"),
    ("prior_source_attempt_id", "Nullable(UUID)"), ("prior_market_unit_hash", "Nullable(FixedString(64))"),
    ("identity_interval_id", "String"), ("identity_source_hash", "FixedString(64)"),
    ("identity_valid_from", "Date"), ("identity_valid_to_exclusive", "Nullable(Date)"),
    ("prior_regular_open_at", "DateTime64(6, 'UTC')"),
    ("prior_regular_close_at", "DateTime64(6, 'UTC')"),
    ("available_at", "DateTime64(6, 'UTC')"),
    ("prior_close_int", "Nullable(UInt64)"), ("split_price_factor", "Float64"),
    ("adjusted_prior_close", "Nullable(Float64)"),
    ("split_count", "UInt32"), ("split_content_hash", "FixedString(64)"),
    ("status", "LowCardinality(String)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(session_date)", "source_build_id,session_date,reference_attempt_id,listing_id")
SPLIT = TableContract("qmd_opening_price_split_v1", PREFIX + (
    ("listing_id", "String"), ("source_ordinal", "UInt32"),
    ("symbol_id", "String"), ("security_id", "String"),
    ("execution_date", "Date"), ("split_from", "Float64"), ("split_to", "Float64"),
    ("observed_at", "DateTime64(6, 'UTC')"), ("source_content_hash", "FixedString(64)")),
    "toYYYYMM(session_date)", "source_build_id,session_date,reference_attempt_id,listing_id,source_ordinal")
COVERAGE = TableContract("qmd_opening_price_reference_coverage_v1", PREFIX + (
    ("reference_revision", "String"), ("market_plan_hash", "FixedString(64)"),
    ("prior_market_plan_hash", "FixedString(64)"),
    ("identity_plan_hash", "FixedString(64)"), ("population_reference_hash", "FixedString(64)"),
    ("producer_code_hash", "FixedString(64)"),
    ("listing_count", "UInt32"), ("resolved_count", "UInt32"),
    ("covered_empty_count", "UInt32"), ("excluded_identity_count", "UInt32"), ("split_count", "UInt32"),
    ("reference_content_hash", "FixedString(64)"), ("split_content_hash", "FixedString(64)"),
    ("content_hash", "FixedString(64)"), ("certified_at", "DateTime64(6, 'UTC')")),
    "toYYYYMM(session_date)", "source_build_id,session_date,reference_attempt_id")
REFERENCE, SPLIT, COVERAGE = tuple(replace(table, database="q_live") for table in (REFERENCE, SPLIT, COVERAGE))
TABLES = (REFERENCE, SPLIT, COVERAGE)


@dataclass(frozen=True)
class OpeningReferenceScope:
    build_id: str
    session_date: date
    market_plan_hash: str
    prior_market_plan_hash: str
    identity_plan_hash: str
    population_reference_hash: str
    producer_code_hash: str
    tickers: tuple[str, ...]

    def __post_init__(self):
        if (not isinstance(self.build_id, str) or not 1 <= len(self.build_id) <= 128
                or type(self.session_date) is not date or type(self.tickers) is not tuple
                or not 1 <= len(self.tickers) <= 25000
                or tuple(sorted(set(self.tickers))) != self.tickers
                or any(re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,31}", t or "") is None for t in self.tickers)):
            raise ValueError("Opening reference scope must be bounded and canonical")
        for value in (self.market_plan_hash, self.prior_market_plan_hash, self.identity_plan_hash,
                      self.population_reference_hash, self.producer_code_hash):
            require_hash(value)


def content_hash(value) -> str:
    def scalar(item):
        if type(item) is date:
            return item.isoformat()
        if isinstance(item, datetime) and item.tzinfo is not None:
            return item.astimezone(timezone.utc).isoformat(timespec="microseconds")
        raise TypeError("Opening reference hash requires typed dates and clocks")
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=scalar).encode()).hexdigest()


def require_hash(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Opening reference requires an exact source hash")
    return value


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Opening reference clocks must be timezone-aware")
    return value.astimezone(timezone.utc)


def restore_record(table, row):
    """Restore ClickHouse's scalar wire types before hashing or comparison."""
    if table not in TABLES or set(row) != {name for name, _ in table.columns}:
        raise ValueError("Opening reference record differs from its normalized schema")
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
                raise ValueError("Opening reference unsigned scalar is invalid")
        elif kind == "Float64":
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Opening reference numeric scalar is invalid")
            value = float(value)
        elif kind == "Date":
            value = date.fromisoformat(value) if type(value) is str else value
            if type(value) is not date:
                raise ValueError("Opening reference date is invalid")
        elif kind.startswith("DateTime64"):
            if type(value) is str:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
            value = utc(value)
        elif kind == "UUID":
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError("Opening reference UUID is invalid")
        elif kind == "FixedString(64)":
            require_hash(value)
        elif kind in ("String", "LowCardinality(String)"):
            if type(value) is not str or not 1 <= len(value) <= 256:
                raise ValueError("Opening reference string is invalid")
        else:
            raise ValueError("Opening reference scalar type is unsupported")
        result[name] = value
    return result


@dataclass(frozen=True)
class RegularSession:
    day: date
    opened_at: datetime
    closed_at: datetime

    def __post_init__(self):
        if (type(self.day) is not date or utc(self.opened_at) >= utc(self.closed_at)
                or self.opened_at.astimezone(NEW_YORK).date() != self.day
                or self.closed_at.astimezone(NEW_YORK).date() != self.day
                or self.opened_at.microsecond or self.closed_at.microsecond):
            raise ValueError("Invalid exchange regular-session bounds")


@lru_cache(maxsize=512)
def previous_regular_session(day: date) -> RegularSession:
    """Use the existing exchange-calendar authority, including early closes."""
    from datetime import timedelta
    import pandas_market_calendars as mcal
    if type(day) is not date:
        raise ValueError("Opening reference needs a session date")
    schedule = mcal.get_calendar("XNYS").schedule(start_date=day - timedelta(days=32), end_date=day)
    if day not in set(schedule.index.date):
        raise ValueError("Opening reference day is not an exchange session")
    previous = schedule[schedule.index.date < day]
    if previous.empty:
        raise ValueError("Previous regular session is unavailable")
    row = previous.iloc[-1]
    return RegularSession(previous.index[-1].date(), row["market_open"].to_pydatetime(),
                          row["market_close"].to_pydatetime())


@dataclass(frozen=True)
class OpeningListing:
    listing_id: str
    ticker: str
    symbol_id: str
    security_id: str
    identity_interval_id: str
    identity_source_hash: str
    valid_from: date
    valid_to_exclusive: date | None

    def __post_init__(self):
        if (any(not isinstance(v, str) or not 1 <= len(v) <= 256 for v in (
                self.listing_id, self.symbol_id, self.security_id, self.identity_interval_id))
                or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,31}", self.ticker or "") is None
                or type(self.valid_from) is not date
                or (self.valid_to_exclusive is not None and (
                    type(self.valid_to_exclusive) is not date
                    or self.valid_to_exclusive <= self.valid_from))):
            raise ValueError("Opening reference needs an effective listing identity")
        require_hash(self.identity_source_hash)


@dataclass(frozen=True)
class OpeningSplit:
    symbol_id: str
    listing_id: str
    security_id: str
    execution_date: date
    split_from: float
    split_to: float
    observed_at: datetime
    source_content_hash: str

    def __post_init__(self):
        if (any(not isinstance(v, str) or not 1 <= len(v) <= 256
                for v in (self.symbol_id, self.listing_id, self.security_id))
                or type(self.execution_date) is not date
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                       for v in (self.split_from, self.split_to))):
            raise ValueError("Opening reference split is invalid")
        utc(self.observed_at)
        require_hash(self.source_content_hash)


def project_opening_reference(*, build_id: str, day: date, attempt_id: str,
        listing: OpeningListing, regular: RegularSession, prior_build_id: str,
        prior_attempt_id: str | None, prior_unit_hash: str | None, prior_close_int: int | None,
        splits: tuple[OpeningSplit, ...]):
    """Project one fully covered listing; a no-trade prior session is explicit.

    Missing source coverage must fail upstream. ``None`` denotes a certified
    empty regular-session close, not an unavailable query or ambiguous identity.
    Split rows are supplied already restricted to the opening as-of query;
    receiving even one future observation fails instead of silently dropping it.
    """
    if type(listing) is not OpeningListing or type(regular) is not RegularSession or type(day) is not date:
        raise TypeError("Opening reference requires typed identity and calendar inputs")
    for value in (build_id, prior_build_id):
        if not isinstance(value, str) or not 1 <= len(value) <= 128:
            raise ValueError("Opening reference source build is invalid")
    for value in (attempt_id, prior_attempt_id):
        if value is not None and str(UUID(value)) != value:
            raise ValueError("Opening reference attempt UUID must be canonical")
    if attempt_id is None:
        raise ValueError("Opening reference requires a publication attempt")
    opening = datetime.combine(day, time(4), NEW_YORK).astimezone(timezone.utc)
    if (regular.day >= day or utc(regular.closed_at) >= opening
            or listing.valid_from > day
            or (listing.valid_to_exclusive is not None and listing.valid_to_exclusive <= day)):
        raise ValueError("Opening reference identity does not cover the current session")
    continuous = listing.valid_from <= regular.day
    if continuous:
        if prior_attempt_id is None:
            raise ValueError("Continuous opening identity requires certified prior coverage")
        require_hash(prior_unit_hash)
    elif prior_attempt_id is not None or prior_unit_hash is not None or prior_close_int is not None or splits:
        raise ValueError("Noncontinuous identity cannot supply prior price or split evidence")
    if prior_close_int is not None and (type(prior_close_int) is not int
                                       or not 0 < prior_close_int < 2 ** 64):
        raise ValueError("Prior close requires a lossless positive canonical price")
    if type(splits) is not tuple or len(splits) > 4096 or any(type(s) is not OpeningSplit for s in splits):
        raise ValueError("Opening split inputs must be bounded and typed")
    factors, children = {}, []
    ordered = sorted(splits, key=lambda s: (s.execution_date, utc(s.observed_at), s.source_content_hash))
    seen = set()
    for ordinal, split in enumerate(ordered):
        if ((split.symbol_id, split.listing_id, split.security_id) != (
                listing.symbol_id, listing.listing_id, listing.security_id)
                or not regular.day < split.execution_date <= day or utc(split.observed_at) > opening):
            raise ValueError("Split identity, effective date or opening availability differs")
        identity = (split.execution_date, split.observed_at, split.source_content_hash)
        if identity in seen:
            raise ValueError("Duplicate opening split evidence")
        seen.add(identity)
        factor = split.split_from / split.split_to
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("Opening split factor is invalid")
        if split.execution_date in factors and not math.isclose(
                factors[split.execution_date], factor, rel_tol=1e-12, abs_tol=0):
            raise ValueError("Conflicting opening split ratios")
        factors[split.execution_date] = factor
        children.append(dict(source_build_id=build_id, session_date=day,
            reference_attempt_id=attempt_id, listing_id=listing.listing_id, source_ordinal=ordinal,
            symbol_id=split.symbol_id, security_id=split.security_id,
            execution_date=split.execution_date, split_from=float(split.split_from),
            split_to=float(split.split_to), observed_at=utc(split.observed_at),
            source_content_hash=split.source_content_hash))
    factor = math.prod(factors.values())
    adjusted = None if prior_close_int is None else prior_close_int / 10_000. * factor
    if not math.isfinite(factor) or factor <= 0 or (adjusted is not None and (
            not math.isfinite(adjusted) or adjusted <= 0)):
        raise ValueError("Opening adjusted prior close is invalid")
    row = dict(source_build_id=build_id, session_date=day, reference_attempt_id=attempt_id,
        listing_id=listing.listing_id, ticker=listing.ticker, symbol_id=listing.symbol_id,
        security_id=listing.security_id, prior_source_build_id=prior_build_id,
        prior_session_date=regular.day, prior_source_attempt_id=prior_attempt_id,
        prior_market_unit_hash=prior_unit_hash, identity_interval_id=listing.identity_interval_id,
        identity_source_hash=listing.identity_source_hash, identity_valid_from=listing.valid_from,
        identity_valid_to_exclusive=listing.valid_to_exclusive, prior_regular_open_at=utc(regular.opened_at),
        prior_regular_close_at=utc(regular.closed_at), available_at=opening,
        prior_close_int=prior_close_int, split_price_factor=factor, adjusted_prior_close=adjusted,
        split_count=len(children), split_content_hash=content_hash(children),
        status=("excluded_identity" if not continuous else
                "resolved" if adjusted is not None else "covered_empty"))
    row["content_hash"] = content_hash(row)
    return row, tuple(children)
