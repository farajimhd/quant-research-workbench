"""Shared source and representation contracts; no downstream training artifacts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
import json
import re

from pipelines.market_sip.events.trade_reporting_flags import REVISION as REPORTING_REVISION

VERSION = "certified-research-clickhouse-v1"
TRADE_POLICY = "verified-timely-conditions-v1"
STORAGE_POLICY = "live_market_ssd"
SOURCE_STORAGE_POLICY = "sip_raw_ssd"


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False, default=str).encode()).hexdigest()


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Invalid SQL identifier")
    return value


def literal(value) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def timely_trade(meta: int) -> bool:
    """Only evaluated timely trades; low condition/price bits are preserved."""
    if type(meta) is not int or not 0 <= meta <= 255:
        raise ValueError("event_meta must be UInt8")
    return bool(meta & 1) and meta & 0xC0 == 0x40


def timely_sql(meta: str = "event_meta") -> str:
    # Callers provide a column identifier, never arbitrary SQL or user input.
    identifier(meta)
    return f"(bitAnd({meta},1)=1 AND bitAnd({meta},192)=64)"


@dataclass(frozen=True)
class CampaignConfig:
    start: date
    end: date
    database: str = "arte"
    workers: int = 4
    query_threads: int = 2
    query_memory_bytes: int = 2 * 1024**3
    global_memory_bytes: int = 16 * 1024**3
    volume_windows_seconds: tuple[int, ...] = (10, 60)
    short_windows_sessions: tuple[int, ...] = (20,)
    resolutions_ms: tuple[int, ...] = (100, 1000, 5000, 10000, 30000, 60000, 300000, 3600000)
    checkpoints: tuple[str, ...] = ("PM", "REGULAR", "AH")

    def __post_init__(self):
        if type(self.start) is not date or type(self.end) is not date or self.start > self.end:
            raise ValueError("Invalid inclusive campaign dates")
        if self.start < date(2026, 1, 1):
            raise ValueError("Universe target starts in 2026; earlier data is context only")
        identifier(self.database)
        if self.database in ("default", "q_market_history", "market_sip_compact"):
            raise ValueError("Invalid derived-data database")
        if (type(self.workers) is not int or not 1 <= self.workers <= 32
                or type(self.query_threads) is not int or not 1 <= self.query_threads <= 8
                or type(self.query_memory_bytes) is not int or self.query_memory_bytes < 64 * 1024**2
                or type(self.global_memory_bytes) is not int
                or self.workers * self.query_memory_bytes > self.global_memory_bytes):
            raise ValueError("Worker/query resources exceed global budget")
        for values in (self.volume_windows_seconds, self.short_windows_sessions, self.resolutions_ms):
            if not values or tuple(sorted(set(values))) != values or any(type(v) is not int or v <= 0 for v in values):
                raise ValueError("Windows/resolutions must be sorted unique positive integers")
        if any(v not in (100,1000,5000,10000,30000,60000,300000,3600000) for v in self.resolutions_ms):
            raise ValueError("Unsupported market resolution")
        if 1000 not in self.resolutions_ms:
            raise ValueError('Causal V7 requires certified 1000ms bars')
        if self.checkpoints != ("PM", "REGULAR", "AH"):
            raise ValueError("All three causal session checkpoints are required")

    def identity(self):
        return dict(version=VERSION, config=asdict(self), trade_policy=TRADE_POLICY,
                    reporting_revision=REPORTING_REVISION, storage_policy=STORAGE_POLICY)

    @property
    def content_hash(self):
        return digest(self.identity())

    def calculation_identity(self):
        """Semantic feature selection, independent of campaign range/resources.

        This is one component of a state compatibility certificate. Algorithm,
        rule, calendar, source and predecessor versions remain independently
        required; this hash alone never authorizes checkpoint reuse.
        """
        return dict(version='research-calculation-selection-v1',trade_policy=TRADE_POLICY,
            reporting_revision=REPORTING_REVISION,volume_windows_seconds=self.volume_windows_seconds,
            short_windows_sessions=self.short_windows_sessions,resolutions_ms=self.resolutions_ms,
            checkpoints=self.checkpoints)

    @property
    def calculation_hash(self):
        return digest(self.calculation_identity())
