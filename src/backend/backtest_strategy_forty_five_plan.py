"""Independent read-only Strategy 45 source preparation authority.

Both the producer and app preflight use the same complete market population,
reference pin and native first-episode scanner. No product is written here.
"""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from types import MappingProxyType

from .backtest_market_data import (
    certified_market_plan_from_arte, project_market_day_plan, market_day_boundary, _literal,
)
from .backtest_strategy_one_identity import certify_identity_plan, identity_content_hash
from .fixed_bar_signal import canonical_stream_activation, load_first_squeeze_occurrences
from .structural_v7_seed import certified_seed_plan
from .backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
from src.trading_runtime.historical_reference_identity import load_reference_pin, utc


def _rows(reader, query):
    return [json.loads(line) for line in reader.execute(query + " FORMAT JSONEachRow").splitlines()
            if line.strip()]


@dataclass(frozen=True, slots=True)
class FortyFiveSourcePlan:
    market: object
    identity: object
    seeds: object
    structure: object
    snapshot_hash: str
    signal_query_hash: str
    admissions: object
    listings: object
    session_end_ms: int

    @property
    def tickers(self):
        return tuple(self.admissions)


def certified_reference_members(reader, market, identity):
    """Hash every snapshot row before exclusion; join exact sealed identities."""
    pin = load_reference_pin(reader, market)
    where = (f"session_date=toDate({_literal(market.sessions[0])}) "
             f"AND snapshot_id={_literal(pin.snapshot_id)}")
    proof = _rows(reader, "SELECT sum(cityHash64(tuple(ticker,symbol_id,listing_id,"
        "security_id,is_tradable,exclusion_reason,source_run_id,captured_at_utc))) AS hash "
        "FROM q_live.feature_tradable_universe_snapshot_v2 WHERE " + where)
    if len(proof) != 1 or str(proof[0]["hash"]) != pin.population_source_hash:
        raise RuntimeError("Strategy 45 full reference snapshot differs from its market seal")
    members = _rows(reader, "SELECT ticker,symbol_id,listing_id,security_id,source_run_id,"
        "captured_at_utc AS inserted_at FROM q_live.feature_tradable_universe_snapshot_v2 WHERE "
        + where + " AND is_tradable=1 ORDER BY ticker,symbol_id,listing_id")
    digest = sha256(json.dumps(members, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    selected = {}
    for row in members:
        if row["ticker"] not in market.tickers:
            continue
        if row["ticker"] in selected or utc(row["inserted_at"]) > utc(pin.available_at):
            raise RuntimeError("Strategy 45 reference contains ambiguous or late selected listings")
        if any(not row[name] for name in ("listing_id", "symbol_id", "security_id", "source_run_id")):
            raise RuntimeError("Strategy 45 reference listing is incomplete")
        selected[row["ticker"]] = row
    if set(selected) != set(market.tickers):
        raise RuntimeError("Strategy 45 snapshot does not cover its complete tradable market")
    # The legacy and snapshot-linked versions are separate certified products.
    # Select ONLY the attempt and exact content hash already certified above.
    installed = _rows(reader, "SELECT name FROM system.tables WHERE database='arte' "
        "AND name IN ('strategy_one_identity_v1','strategy_one_identity_v2','strategy_one_identity_v3')")
    matches = []
    for table in installed:
        retained = _rows(reader, "SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,"
            f"source_run_id,source_inserted_at FROM arte.{table['name']} WHERE "
            f"source_build_id={_literal(market.build_id)} AND session_date=toDate({_literal(market.sessions[0])}) "
            f"AND identity_attempt_id=toUUID({_literal(identity.attempt_id)}) ORDER BY ticker")
        if retained and identity_content_hash(retained) == identity.content_hash:
            matches.append(retained)
    if len(matches) != 1:
        raise RuntimeError("Strategy 45 cannot resolve its exact certified identity children")
    for row in matches[0]:
        if row["ticker"] not in selected:
            continue
        expected = selected[row["ticker"]]
        if (any(row[name] != expected[name] for name in ("symbol_id", "listing_id", "security_id", "source_run_id"))
                or utc(row["source_inserted_at"]) != utc(expected["inserted_at"])
                or int(row["ibkr_conid"]) != identity.conid_for(row["ticker"])):
            raise RuntimeError("Strategy 45 selected listing differs from its certified broker identity")
    return digest, selected


def certify_source_plan(*, session, build, reader, session_end_ms=19_800_000):
    if session_end_ms != 19_800_000:
        raise ValueError("Strategy 45 comparison release supports premarket only")
    market = certified_market_plan_from_arte(sessions=(session,), tickers=(),
        configuration={"market_day_build_id": build, "strategy": {"execution_interval": "100ms"}})
    market = project_market_day_plan(market, tuple(t for t in market.tickers if t != "LGHL"))
    identity = certify_identity_plan(market, client=reader)
    snapshot_hash, listings = certified_reference_members(reader, market, identity)
    stream, activation = canonical_stream_activation()
    scan = load_first_squeeze_occurrences(market, stream=stream, activation=activation,
        through_boundary_ms=session_end_ms, client=reader)
    first = {}
    anchor = market_day_boundary(session, 0)
    for row in scan["occurrences"]:
        if not 1 <= row["last_price"] <= 50:
            continue
        delta = datetime.fromisoformat(row["available_at"]) - anchor
        micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        admission = ((micros + 999_999) // 1_000_000) * 1000
        if not 0 < admission <= session_end_ms:
            raise RuntimeError("Strategy 45 signal crossed its certified premarket window")
        first[row["ticker"]] = min(first.get(row["ticker"], admission), admission)
    tickers = tuple(sorted(first))
    if not tickers:
        raise RuntimeError("Strategy 45 has no certified squeeze candidates")
    seeds = certified_seed_plan(project_market_day_plan(market, tickers), reader)
    structure = certify_v7_interval_plan(market, seeds, session_date=session,
        candidate_tickers=tickers, client=reader)
    return FortyFiveSourcePlan(market, identity, seeds, structure, snapshot_hash,
        scan["authority"]["query_sha256"], MappingProxyType({t: first[t] for t in tickers}),
        MappingProxyType({t: MappingProxyType(listings[t]) for t in tickers}), session_end_ms)
