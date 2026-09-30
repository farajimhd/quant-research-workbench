"""Broker identities linked to the market build's immutable population proof.

The population already belongs to Reference Gateway. This module resolves its
sealed pin, never an independently chosen latest/exact-day universe. The V2
identity publication adds broker IDs without changing that population or V1.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from hashlib import sha256
import json

from src.trading_runtime.arte_journal_schema import TableContract, storage_preflight
from src.trading_runtime.strategy_one_identity_schema import IDENTITY_COLUMNS


REFERENCE_COLUMNS = (
    ("snapshot_id", "String"), ("reference_revision", "String"),
    ("population_source_hash", "String"),
    ("available_at", "DateTime64(6, 'UTC')"), ("cutoff_at", "DateTime64(6, 'UTC')"),
)
IDENTITIES = TableContract("strategy_one_identity_v2",
    tuple((name, kind.replace(",", ", ")) for name, kind in IDENTITY_COLUMNS),
    "toYYYYMM(session_date)", "source_build_id,session_date,identity_attempt_id,ticker")
COVERAGE = TableContract("strategy_one_identity_coverage_v2", (
    ("source_build_id", "String"), ("session_date", "Date"),
    ("identity_attempt_id", "UUID"), ("source_universe_date", "Date"),
    *REFERENCE_COLUMNS, ("ticker_count", "UInt32"),
    ("content_hash", "FixedString(64)"), ("reference_hash", "FixedString(64)"),
    ("certified_at", "DateTime64(6, 'UTC')"),
), "toYYYYMM(session_date)", "source_build_id,session_date,identity_attempt_id")
TABLES = (IDENTITIES, COVERAGE)


class ReferenceIdentityError(RuntimeError):
    """Public, credential-free description of a reference contract failure."""


def rows(client, query):
    return [json.loads(line) for line in client.execute(query + " FORMAT JSONEachRow").splitlines()
            if line.strip()]


def utc(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReferencePin:
    snapshot_id: str
    reference_revision: str
    population_source_hash: str
    available_at: str
    cutoff_at: str

    def __post_init__(self):
        for key in ('available_at', 'cutoff_at'):
            object.__setattr__(self, key, utc(getattr(self, key)).strftime('%Y-%m-%d %H:%M:%S.%f'))

    def digest(self, source_date: str) -> str:
        return sha256(json.dumps([asdict(self), source_date], sort_keys=True,
                                 separators=(",", ":")).encode()).hexdigest()


def pin_from_scopes(scopes, market):
    """Resolve one reference pin only from already Keeper-verified scope rows."""
    if len(market.sessions) != 1 or not scopes:
        raise ReferenceIdentityError("Reference identity requires one sealed session")
    if any(r['build_id'] != market.build_id or r['session_date'] != market.sessions[0]
           for r in scopes):
        raise ReferenceIdentityError("Reference scope differs from market session")
    symbols = [r['ticker'] for r in scopes]
    if len(set(symbols)) != len(symbols) or not set(market.tickers) <= set(symbols):
        raise ReferenceIdentityError("Reference scope has duplicate or missing tickers")
    pins = {ReferencePin(r['population_snapshot_id'], r['population_revision'],
                        str(r['population_source_hash']), r['population_available_at'],
                        r['population_cutoff_at']) for r in scopes}
    if len(pins) != 1:
        raise ReferenceIdentityError("Market session has mixed reference snapshots")
    pin = pins.pop()
    if pin.reference_revision not in {'preopen-tradable-snapshot-v3',
                                       'preopen-tradable-carry-forward-v1'}:
        raise ReferenceIdentityError("Unsupported historical reference revision")
    if utc(pin.available_at) >= utc(pin.cutoff_at):
        raise ReferenceIdentityError("Reference publication was not available before cutoff")
    return pin


def load_reference_pin(client, market):
    """Recheck the immutable selected-session scope against its Keeper receipt."""
    from datetime import date
    from src.backend.backtest_market_keeper_pool import MARKET_CERTIFICATE_KEEPER_POOL
    from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
    from src.trading_runtime.arte_market_day_session_seal import (
        load_session_seal, read_sealed_session_families, session_seal_receipt,
    )
    with MARKET_CERTIFICATE_KEEPER_POOL.borrow() as session:
        keeper = MarketDayKeeperReader(session.client)
        proof = keeper.load(market.build_id)
        seal = load_session_seal(client, keeper, proof, date.fromisoformat(market.sessions[0]))
        families = read_sealed_session_families(client, proof, seal, session_seal_receipt(seal))
        if keeper.load(market.build_id) != proof:
            raise ReferenceIdentityError("Reference build proof changed during read")
    return pin_from_scopes(families['market_day_planned_scope_v1'], market)


def verify_tables(client):
    storage_preflight(client, tables=TABLES)


def install_tables(client):
    # Verify the required policy before any DDL; no fallback disk is permitted.
    policy = rows(client, "SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if policy != [{'disks': ['live_market_ssd']}]:
        raise ReferenceIdentityError("Historical identities require SSD-only storage")
    for table in TABLES:
        client.execute(table.ddl())
    verify_tables(client)
