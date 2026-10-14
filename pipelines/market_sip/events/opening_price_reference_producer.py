"""Coverage-last QMD opening-reference publication from certified database bars.

Operator-owned only: missing prior coverage is an actionable failure, never a
Backtest-triggered rebuild or a fallback to flatfiles. Connections are confined
to a worker; callers await the bounded unit without blocking their event loop.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import json
import re
from uuid import uuid4

from .market_day_sql import literal
from src.backend.backtest_market_data import CertifiedMarketDayPlan, _verify_market_batch
from src.backend.backtest_strategy_one_identity import (
    CertifiedIdentityPlan, certify_identity_plan, identity_content_hash,
)
from src.market_engine import opening_price_reference as contract
from src.market_engine.regular_close_history import CertifiedPriorClosePlan
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.historical_reference_identity import load_reference_pin
from research.mlops.clickhouse import insert_json_each_row


def _rows(reader, query, *, bound=25001):
    response = reader.execute(query + " FORMAT JSONEachRow")
    if not isinstance(response, str) or len(response.encode()) > 16 * 1024 * 1024:
        raise ValueError("Opening reference read exceeds its bounded response contract")
    rows = [json.loads(line) for line in response.splitlines() if line.strip()]
    if len(rows) > bound:
        raise ValueError("Opening reference read exceeds its bounded row contract")
    return rows


def _time(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # ClickHouse DateTime64 UTC text is normally emitted without an offset.
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


@dataclass(frozen=True)
class CertifiedOpeningInput:
    market: CertifiedMarketDayPlan
    prior_market: CertifiedMarketDayPlan | CertifiedPriorClosePlan
    identity: CertifiedIdentityPlan
    listings: tuple[contract.OpeningListing, ...]
    population_reference_hash: str

    def __post_init__(self):
        if (type(self.market) is not CertifiedMarketDayPlan
                or type(self.prior_market) not in (CertifiedMarketDayPlan, CertifiedPriorClosePlan)
                or type(self.identity) is not CertifiedIdentityPlan
                or len(self.market.sessions) != 1 or len(self.prior_market.sessions) != 1
                or type(self.listings) is not tuple or not 1 <= len(self.listings) <= 25000
                or any(type(v) is not contract.OpeningListing for v in self.listings)):
            raise ValueError("Opening reference needs complete typed certified source plans")
        regular = contract.previous_regular_session(date.fromisoformat(self.market.sessions[0]))
        if (self.prior_market.sessions != (regular.day.isoformat(),)
                or self.identity.source_build_id != self.market.build_id
                or self.identity.session_date != self.market.sessions[0]
                or self.identity.market_token != self.market.token
                or self.identity.tickers != self.market.tickers
                or {v.ticker for v in self.listings} != set(self.market.tickers)
                or len({v.ticker for v in self.listings}) != len(self.listings)
                or len({v.listing_id for v in self.listings}) != len(self.listings)):
            raise ValueError("Opening reference source scope or previous exchange session differs")
        required = {v.ticker for v in self.listings if v.valid_from <= regular.day}
        if not required <= set(self.prior_market.tickers):
            raise ValueError("Prior certified bars do not cover the complete opening population")
        if type(self.prior_market) is CertifiedPriorClosePlan:
            self.prior_market.verify_consumer(self.market)
            if (self.prior_market.header['consumer_identity_plan_hash'] != self.identity.token
                    or self.prior_market.header['consumer_population_reference_hash'] != self.population_reference_hash):
                raise ValueError('Prior-close history differs from the opening identity/reference pins')
        elif 1000 not in self.prior_market.required_resolutions_ms:
            raise ValueError("Opening prior-close inputs require certified one-second bars")
        contract.require_hash(self.population_reference_hash)
        contract.require_hash(self.identity.token)
        contract.require_hash(self.identity.content_hash)
        for plan in (self.market, self.prior_market):
            contract.require_hash(plan.token)
        for listing in self.listings:
            if (listing.valid_from > date.fromisoformat(self.market.sessions[0]) or (listing.valid_to_exclusive is not None
                    and listing.valid_to_exclusive <= date.fromisoformat(self.market.sessions[0]))):
                raise ValueError("Opening listing identity does not cover the current session")

    def verify(self, reader):
        """Recheck the existing Keeper-backed population and identity authorities."""
        if certify_identity_plan(self.market, client=reader) != self.identity:
            raise ValueError("Opening broker/listing identity certificate changed")
        pin = load_reference_pin(reader, self.market)
        # Pin the complete existing Reference Gateway snapshot contract. The
        # identity certificate separately pins its selected source-universe day.
        expected_reference_hash = contract.content_hash(asdict(pin))
        opening = datetime.combine(date.fromisoformat(self.market.sessions[0]),
            datetime.min.time(), contract.NEW_YORK).replace(hour=4).astimezone(timezone.utc)
        if (expected_reference_hash != self.population_reference_hash
                or _time(pin.available_at) >= opening or _time(pin.cutoff_at) != opening):
            raise ValueError("Opening population reference pin changed")
        _verify_identity_rows(reader, self)
        _verify_intervals(reader, self)
        if type(self.prior_market) is CertifiedPriorClosePlan:
            self.prior_market.verify(reader)
            return
        unit_by_ticker = _prior_units(self)
        units = tuple(unit_by_ticker.values())
        for offset in range(0, len(units), 128):
            _verify_market_batch(self.prior_market, "bars", "bars_v1",
                self.prior_market.sessions[0], units[offset:offset + 128], reader)

    def scope(self, code_hash):
        return contract.OpeningReferenceScope(self.market.build_id,
            date.fromisoformat(self.market.sessions[0]), self.market.token, self.prior_market.token,
            self.identity.token, self.population_reference_hash, code_hash,
            tuple(sorted(self.market.tickers)))


def _prior_units(source):
    regular = contract.previous_regular_session(date.fromisoformat(source.market.sessions[0]))
    required = {v.ticker for v in source.listings if v.valid_from <= regular.day}
    if type(source.prior_market) is CertifiedPriorClosePlan:
        mapped = {unit.ticker: unit for unit in source.prior_market.units if unit.ticker in required}
        if set(mapped) != required:
            raise ValueError('Opening reference lacks complete sealed QMD prior-close units')
        return mapped
    units = [u for u in source.prior_market.units if u.stage == "bars"
             and u.session_date == source.prior_market.sessions[0]
             and u.ticker in required]
    mapped = {u.ticker: u for u in units}
    if len(mapped) != len(units) or set(mapped) != required:
        raise ValueError("Opening reference lacks unique pinned prior bar units")
    return mapped


def _identity_rows(reader, market, identity):
    names = ("strategy_one_identity_v1", "strategy_one_identity_v2", "strategy_one_identity_v3")
    tables = _rows(reader, "SELECT name FROM system.tables WHERE database='arte' "
        "AND name IN (" + ",".join(literal(n) for n in names) + ")", bound=3)
    found = []
    for table in tables:
        if table.get("name") not in names:
            raise ValueError("Unexpected identity authority")
        rows = _rows(reader, "SELECT ticker,symbol_id,listing_id,security_id,ibkr_conid,"
            "source_run_id,source_inserted_at FROM arte." + table["name"]
            + f" WHERE source_build_id={literal(market.build_id)}"
            + f" AND session_date=toDate({literal(market.sessions[0])})"
            + f" AND identity_attempt_id=toUUID({literal(identity.attempt_id)})"
            + " ORDER BY ticker LIMIT 25001")
        if rows:
            found.append(rows)
    if len(found) != 1 or identity_content_hash(found[0]) != identity.content_hash:
        raise ValueError("Opening listing rows differ from their central identity certificate")
    mapped = {r["ticker"]: r for r in found[0]}
    if len(mapped) != len(found[0]):
        raise ValueError("Opening central identity has duplicate tickers")
    if set(mapped) != set(market.tickers):
        raise ValueError("Opening scanner population must cover the complete sealed identity population; ticker projections are not permitted")
    return mapped


def _verify_identity_rows(reader, source):
    mapped = _identity_rows(reader, source.market, source.identity)
    for listing in source.listings:
        row = mapped.get(listing.ticker)
        if row is None or any(row[k] != getattr(listing, k)
                for k in ("listing_id", "symbol_id", "security_id")):
            raise ValueError("Opening listing differs from the sealed market population")


def certified_opening_input(reader, market, prior_market):
    """Resolve producer inputs through existing sealed population/identity APIs.

    No caller supplies its own symbol mapping, current universe or split factor.
    An effective mapping which begins after the prior session is retained as an
    explicit exclusion; a missing current mapping stops the entire unit.
    """
    if (type(market) is not CertifiedMarketDayPlan or type(prior_market) not in (CertifiedMarketDayPlan, CertifiedPriorClosePlan)
            or len(market.sessions) != 1 or len(prior_market.sessions) != 1):
        raise TypeError("Opening input requires typed single-session market certificates")
    identity = certify_identity_plan(market, client=reader)
    pin = load_reference_pin(reader, market)
    facts = _identity_rows(reader, market, identity)
    listings = []
    for offset in range(0, len(market.tickers), 128):
        names = market.tickers[offset:offset + 128]
        if any(name not in facts for name in names):
            raise ValueError("Opening population lacks sealed listing identity rows")
        identities = ",".join("(" + ",".join(literal(facts[name][k]) for k in (
            "listing_id", "security_id", "ticker")) + ")" for name in names)
        rows = _rows(reader, "SELECT symbol_interval_id,listing_id,security_id,ticker_normalized,"
            "valid_from_date,valid_to_date_exclusive,source_content_sha256 "
            "FROM q_live.id_symbol_interval_v1 FINAL WHERE mapping_status='mapped' AND is_deleted=0 "
            f"AND (listing_id,security_id,ticker_normalized) IN ({identities}) "
            f"AND valid_from_date<=toDate({literal(market.sessions[0])}) "
            f"AND (valid_to_date_exclusive IS NULL OR valid_to_date_exclusive>toDate({literal(market.sessions[0])})) "
            "ORDER BY ticker_normalized,symbol_interval_id LIMIT 129", bound=129)
        if (len(rows) != len(names) or {r["ticker_normalized"] for r in rows} != set(names)
                or len({r["ticker_normalized"] for r in rows}) != len(rows)):
            raise ValueError("Opening population effective mappings are missing or ambiguous")
        for row in rows:
            fact = facts[row["ticker_normalized"]]
            if (row["listing_id"], row["security_id"]) != (fact["listing_id"], fact["security_id"]):
                raise ValueError("Opening interval differs from the sealed current identity")
            listings.append(contract.OpeningListing(row["listing_id"], row["ticker_normalized"],
                fact["symbol_id"], row["security_id"], row["symbol_interval_id"], row["source_content_sha256"],
                date.fromisoformat(row["valid_from_date"]),
                date.fromisoformat(row["valid_to_date_exclusive"]) if row["valid_to_date_exclusive"] else None))
    source = CertifiedOpeningInput(market, prior_market, identity,
        tuple(sorted(listings, key=lambda v: v.listing_id)), contract.content_hash(asdict(pin)))
    source.verify(reader)
    return source


def _verify_intervals(reader, source):
    expected = {v.identity_interval_id: v for v in source.listings}
    if len(expected) != len(source.listings):
        raise ValueError("Opening intervals are not unique")
    for offset in range(0, len(source.listings), 128):
        batch = source.listings[offset:offset + 128]
        rows = _rows(reader, "SELECT symbol_interval_id,listing_id,security_id,ticker_normalized,"
            "valid_from_date,valid_to_date_exclusive,source_content_sha256 "
            "FROM q_live.id_symbol_interval_v1 FINAL WHERE mapping_status='mapped' AND is_deleted=0 "
            "AND symbol_interval_id IN (" + ",".join(literal(v.identity_interval_id) for v in batch)
            + ") ORDER BY symbol_interval_id LIMIT 129", bound=129)
        if len(rows) != len(batch) or {r["symbol_interval_id"] for r in rows} != {
                v.identity_interval_id for v in batch}:
            raise ValueError("Opening effective identity intervals are missing or ambiguous")
        for row in rows:
            v = expected[row["symbol_interval_id"]]
            if ((row["listing_id"], row["security_id"], row["ticker_normalized"],
                 row["valid_from_date"], row["valid_to_date_exclusive"], row["source_content_sha256"])
                    != (v.listing_id, v.security_id, v.ticker, v.valid_from.isoformat(),
                        v.valid_to_exclusive.isoformat() if v.valid_to_exclusive else None,
                        v.identity_source_hash)):
                raise ValueError("Opening effective identity interval changed")


def prior_close_sql(source, listings):
    regular = contract.previous_regular_session(date.fromisoformat(source.market.sessions[0]))
    midnight = datetime.combine(regular.day, datetime.min.time(), contract.NEW_YORK)
    low = int((regular.opened_at.astimezone(contract.NEW_YORK) - midnight).total_seconds())
    high = int((regular.closed_at.astimezone(contract.NEW_YORK) - midnight).total_seconds())
    units = _prior_units(source)
    if not listings or len(listings) > 128 or any(v not in source.listings for v in listings):
        raise ValueError("Opening prior-close query requires a bounded certified listing scope")
    if type(source.prior_market) is CertifiedPriorClosePlan:
        from src.market_engine.regular_close_history import VALUES
        prior = source.prior_market
        return (f'SELECT ticker,closing_bar_count,prior_close_int FROM q_live.{VALUES.name} '
            f'WHERE consumer_build_id={literal(prior.scope.consumer_build_id)} '
            f'AND consumer_session_date=toDate({literal(prior.scope.consumer_session_date)}) '
            f'AND attempt_id=toUUID({literal(prior.header["attempt_id"])}) '
            f'AND ticker IN ({",".join(literal(v.ticker) for v in listings)}) '
            'AND closing_bar_count>0 ORDER BY ticker LIMIT 129')
    pairs = ",".join(f"({literal(v.ticker)},toUUID({literal(units[v.ticker].attempt_id)}))" for v in listings)
    return ("SELECT ticker,count() AS closing_bar_count,argMax(close_int,bucket_index) AS prior_close_int "
        "FROM arte.bars_v1 "
        f"WHERE build_id={literal(source.prior_market.build_id)} "
        f"AND session_date=toDate({literal(regular.day.isoformat())}) AND resolution_ms=1000 "
        f"AND (ticker,attempt_id) IN ({pairs}) AND bucket_index>={low} AND bucket_index<{high} "
        "AND price_valid=1 GROUP BY ticker ORDER BY ticker LIMIT 129")


def opening_splits_sql(source, listings):
    day = date.fromisoformat(source.market.sessions[0])
    regular = contract.previous_regular_session(day)
    opening = datetime.combine(day, datetime.min.time(), contract.NEW_YORK).replace(hour=4)
    if not listings or len(listings) > 128 or any(v not in source.listings for v in listings):
        raise ValueError("Opening split query requires a bounded certified listing scope")
    identities = ",".join("(" + ",".join(literal(getattr(v, k)) for k in (
        "symbol_id", "listing_id", "security_id")) + ")" for v in listings)
    return ("SELECT symbol_id,listing_id,security_id,execution_date,split_from,split_to,"
        "inserted_at,source_content_sha256 FROM q_live.market_stock_split_v1 FINAL "
        f"WHERE (symbol_id,listing_id,security_id) IN ({identities}) "
        f"AND execution_date>toDate({literal(regular.day.isoformat())}) "
        f"AND execution_date<=toDate({literal(day.isoformat())}) "
        f"AND inserted_at<=toDateTime64({literal(opening.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))},6,'UTC') "
        "ORDER BY listing_id,execution_date,inserted_at,source_content_sha256 LIMIT 4097")


def materialize_opening_reference(reader, source, *, attempt_id):
    """Read exact prior certified attempts and opening-known split evidence."""
    if type(source) is not CertifiedOpeningInput:
        raise TypeError("Opening producer requires its certified input contract")
    regular = contract.previous_regular_session(date.fromisoformat(source.market.sessions[0]))
    units = _prior_units(source)
    references, children = [], []
    for offset in range(0, len(source.listings), 128):
        batch = source.listings[offset:offset + 128]
        continuous = tuple(v for v in batch if v.valid_from <= regular.day)
        close_rows = _rows(reader, prior_close_sql(source, continuous), bound=129) if continuous else []
        for row in close_rows:
            if set(row) != {"ticker", "closing_bar_count", "prior_close_int"}:
                raise ValueError("Prior regular-session close schema differs")
            for name in ("closing_bar_count", "prior_close_int"):
                value = row[name]
                if type(value) is str and re.fullmatch(r"0|[1-9][0-9]*", value):
                    row[name] = int(value)
        closes = {row["ticker"]: row for row in close_rows}
        if (len(closes) != len(close_rows) or not set(closes) <= {v.ticker for v in batch}
                or any(type(r["closing_bar_count"]) is not int or r["closing_bar_count"] <= 0
                       or type(r["prior_close_int"]) is not int or r["prior_close_int"] <= 0
                       for r in close_rows)):
            raise ValueError("Prior regular-session close rows are invalid or ambiguous")
        split_rows = _rows(reader, opening_splits_sql(source, continuous), bound=4097) if continuous else []
        if len(split_rows) > 4096:
            raise ValueError("Opening split evidence exceeds its bounded unit")
        splits = tuple(contract.OpeningSplit(r["symbol_id"], r["listing_id"], r["security_id"],
            date.fromisoformat(r["execution_date"]), float(r["split_from"]), float(r["split_to"]),
            _time(r["inserted_at"]), r["source_content_sha256"]) for r in split_rows)
        if not {s.listing_id for s in splits} <= {v.listing_id for v in batch}:
            raise ValueError("Opening split query returned an out-of-scope listing")
        for v in batch:
            unit = units.get(v.ticker)
            row, evidence = contract.project_opening_reference(build_id=source.market.build_id,
                day=date.fromisoformat(source.market.sessions[0]), attempt_id=attempt_id, listing=v,
                regular=regular, prior_build_id=source.prior_market.build_id,
                prior_attempt_id=unit.attempt_id if unit else None,
                prior_unit_hash=contract.content_hash(asdict(unit)) if unit else None,
                prior_close_int=closes[v.ticker]["prior_close_int"] if v.ticker in closes else None,
                splits=tuple(s for s in splits if s.listing_id == v.listing_id))
            references.append(row)
            children.extend(evidence)
            if len(children) > 100000:
                raise ValueError("Opening population split evidence exceeds its bounded contract")
    return (tuple(sorted(references, key=lambda r: r["listing_id"])),
            tuple(sorted(children, key=lambda r: (r["listing_id"], r["source_ordinal"]))))


def _scope(source):
    return (f"source_build_id={literal(source.market.build_id)} "
            f"AND session_date=toDate({literal(source.market.sessions[0])})")


def _coverage(reader, source):
    rows = _rows(reader, f"SELECT * FROM q_live.{contract.COVERAGE.name} WHERE {_scope(source)} LIMIT 2", bound=2)
    if len(rows) > 1:
        raise ValueError("Opening reference has multiple publication seals")
    return [contract.restore_record(contract.COVERAGE, row) for row in rows]


def _readback(reader, source, table, attempt):
    bound = 25001 if table == contract.REFERENCE else 100001
    # Read split evidence in listing-sized scopes; do not request an unbounded
    # population-wide response even when its theoretical row bound is finite.
    output = []
    for offset in range(0, len(source.listings), 128):
        names = ",".join(literal(v.listing_id) for v in source.listings[offset:offset + 128])
        batch_bound = 129 if table == contract.REFERENCE else 4097
        order = "listing_id" + (",source_ordinal" if table == contract.SPLIT else "")
        rows = _rows(reader, f"SELECT * FROM q_live.{table.name} WHERE {_scope(source)} "
            f"AND reference_attempt_id=toUUID({literal(attempt)}) AND listing_id IN ({names}) "
            f"ORDER BY {order} LIMIT {batch_bound}", bound=batch_bound)
        if len(rows) >= batch_bound:
            raise ValueError("Opening reference children exceed their expected bound")
        output.extend(contract.restore_record(table, row) for row in rows)
        if len(output) >= bound:
            raise ValueError("Opening reference readback exceeds its population bound")
    if len(output) >= bound:
        raise ValueError("Opening reference readback exceeds its population bound")
    key = "listing_id" if table == contract.REFERENCE else "(listing_id,source_ordinal)"
    summaries = _rows(reader, f"SELECT count() AS row_count,uniqExact({key}) AS unique_keys "
        f"FROM q_live.{table.name} WHERE {_scope(source)} "
        f"AND reference_attempt_id=toUUID({literal(attempt)})", bound=1)
    if (len(summaries) != 1 or int(summaries[0]["row_count"]) != len(output)
            or int(summaries[0]["unique_keys"]) != len(output)):
        raise ValueError("Opening reference children have extra or duplicate authoritative keys")
    return tuple(sorted(output, key=lambda r: (r["listing_id"], r.get("source_ordinal", 0))))


def _wire_records(table, records):
    converted = []
    for row in records:
        if set(row) != {n for n, _ in table.columns}:
            raise ValueError("Opening reference row differs from its normalized schema")
        wire = dict(row)
        for name, kind in table.columns:
            if kind.startswith("Nullable("):
                if row[name] is None:
                    continue
                kind = kind[9:-1]
            if kind == "Date":
                wire[name] = row[name].isoformat()
            elif kind.startswith("DateTime64"):
                wire[name] = contract.utc(row[name]).strftime("%Y-%m-%d %H:%M:%S.%f")
        converted.append(wire)
    return converted


def source_storage_preflight(reader):
    required = {("arte", "bars_v1"), ("q_live", "id_symbol_interval_v1"),
                ("q_live", "market_stock_split_v1")}
    predicate = "(database,name) IN (" + ",".join(
        f"({literal(db)},{literal(name)})" for db, name in sorted(required)) + ")"
    rows = _rows(reader, "SELECT database,name,engine,storage_policy FROM system.tables WHERE "
                 + predicate, bound=3)
    if (len(rows) != 3 or {(r["database"], r["name"]) for r in rows} != required
            or any(r["storage_policy"] != "live_market_ssd" or r["engine"] not in {
                "MergeTree", "ReplacingMergeTree"} for r in rows)):
        raise ValueError("Opening input authorities require explicit SSD storage")
    parts_predicate = predicate.replace("name)", "table)")
    if _rows(reader, "SELECT database,table,disk_name FROM system.parts WHERE "
             + parts_predicate + " AND active AND disk_name!='live_market_ssd' LIMIT 1", bound=1):
        raise ValueError("Opening source parts are outside live_market_ssd")


def verify_publication(reader, source, header, *, code_hash):
    expected = dict(source_build_id=source.market.build_id,
        session_date=date.fromisoformat(source.market.sessions[0]), reference_revision=contract.REVISION,
        market_plan_hash=source.market.token, prior_market_plan_hash=source.prior_market.token,
        identity_plan_hash=source.identity.token, population_reference_hash=source.population_reference_hash,
        producer_code_hash=code_hash)
    if (set(header) != {n for n, _ in contract.COVERAGE.columns}
            or any(header[k] != v for k, v in expected.items())
            or header["content_hash"] != contract.content_hash({k: v for k, v in header.items()
                if k not in {"content_hash", "certified_at"}})):
        raise ValueError("Opening reference seal differs from source/code pins")
    references, children = materialize_opening_reference(reader, source,
                                                       attempt_id=header["reference_attempt_id"])
    actual = _readback(reader, source, contract.REFERENCE, header["reference_attempt_id"])
    splits = _readback(reader, source, contract.SPLIT, header["reference_attempt_id"])
    if (actual != references or splits != children
            or header["listing_count"] != len(references)
            or header["split_count"] != len(children)
            or header["resolved_count"] != sum(r["status"] == "resolved" for r in references)
            or header["covered_empty_count"] != sum(r["status"] == "covered_empty" for r in references)
            or header["excluded_identity_count"] != sum(r["status"] == "excluded_identity" for r in references)
            or header["reference_content_hash"] != contract.content_hash(references)
            or header["split_content_hash"] != contract.content_hash(children)):
        raise ValueError("Opening reference children differ from recomputed certified source")


def publish_opening_reference(reader, writer, source, *, ownership, code_hash):
    """Exclusive child-first publication. Any ambiguous INSERT terminates the unit."""
    if type(source) is not CertifiedOpeningInput:
        raise TypeError("Opening producer requires its certified input contract")
    contract.require_hash(code_hash)
    endpoint = getattr(reader, "base_url", None)
    if (reader is writer or not isinstance(endpoint, str) or not endpoint
            or endpoint != getattr(writer, "base_url", None)):
        raise ValueError("Opening producer requires separate same-endpoint connections")
    read_user = publication_permission_preflight(reader, writable=False)
    write_user = publication_permission_preflight(writer, writable=True)
    if read_user == write_user:
        raise ValueError("Opening producer requires isolated read and write principals")
    storage_preflight(reader, tables=contract.TABLES)
    source_storage_preflight(reader)
    ownership.assert_owner()
    source.verify(reader)
    existing = _coverage(reader, source)
    if existing:
        verify_publication(reader, source, existing[0], code_hash=code_hash)
        ownership.assert_owner()
        return "skipped_verified"
    for table in (contract.REFERENCE, contract.SPLIT):
        if _rows(reader, f"SELECT reference_attempt_id FROM q_live.{table.name} "
                 f"WHERE {_scope(source)} LIMIT 1", bound=1):
            raise ValueError("Unsealed opening-reference attempt requires operator reconciliation")
    attempt = str(uuid4())
    references, children = materialize_opening_reference(reader, source, attempt_id=attempt)
    # A separate worker owns these finite INSERTs; no event callback waits on I/O.
    for table, records in ((contract.SPLIT, children), (contract.REFERENCE, references)):
        for offset in range(0, len(records), 128):
            ownership.assert_owner()
            insert_json_each_row(writer, "q_live", table.name, [n for n, _ in table.columns],
                                 _wire_records(table, records[offset:offset + 128]))
        actual = _readback(reader, source, table, attempt)
        if actual != records:
            raise ValueError("Opening reference child INSERT readback differs")
    source.verify(reader)
    if materialize_opening_reference(reader, source, attempt_id=attempt) != (references, children):
        raise ValueError("Opening reference source changed before publication seal")
    if _coverage(reader, source):
        raise ValueError("Opening reference seal appeared during exclusive publication")
    header = dict(source_build_id=source.market.build_id,
        session_date=date.fromisoformat(source.market.sessions[0]), reference_attempt_id=attempt,
        reference_revision=contract.REVISION, market_plan_hash=source.market.token,
        prior_market_plan_hash=source.prior_market.token, identity_plan_hash=source.identity.token,
        population_reference_hash=source.population_reference_hash, producer_code_hash=code_hash,
        listing_count=len(references), resolved_count=sum(r["status"] == "resolved" for r in references),
        covered_empty_count=sum(r["status"] == "covered_empty" for r in references),
        excluded_identity_count=sum(r["status"] == "excluded_identity" for r in references),
        split_count=len(children), reference_content_hash=contract.content_hash(references),
        split_content_hash=contract.content_hash(children))
    header["content_hash"] = contract.content_hash(header)
    header["certified_at"] = datetime.now(timezone.utc)
    ownership.assert_owner()
    insert_json_each_row(writer, "q_live", contract.COVERAGE.name,
                         [n for n, _ in contract.COVERAGE.columns], _wire_records(contract.COVERAGE, [header]))
    final = _coverage(reader, source)
    if len(final) != 1 or final[0] != header:
        raise ValueError("Opening reference seal acknowledgement is ambiguous")
    verify_publication(reader, source, final[0], code_hash=code_hash)
    ownership.assert_owner()
    return "published"


def publication_permission_preflight(client, *, writable):
    user = client.execute("SELECT currentUser()").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", user) is None:
        raise ValueError("Opening reference principal is invalid")
    target = {t.name for t in contract.TABLES}
    from src.market_engine.regular_close_history import TABLES as PRIOR_CLOSE
    allowed_target = target | {table.name for table in PRIOR_CLOSE}
    required = {("q_live", n) for n in target} | {("arte", "bars_v1")}
    required |= {("q_live", n) for n in ("id_symbol_interval_v1", "market_stock_split_v1")}
    required |= {("system", n) for n in (
        "storage_policies", "tables", "columns", "parts", "data_skipping_indices")}
    reads, writes = set(), set()
    for line in client.execute("SHOW GRANTS FINAL").splitlines():
        match = re.fullmatch(r"GRANT ([A-Z ,]+) ON ([A-Za-z_][A-Za-z0-9_]*)\."
            r"([A-Za-z_][A-Za-z0-9_]*) TO ([A-Za-z_][A-Za-z0-9_]*)", line.strip())
        if match is None or match.group(4) != user:
            raise ValueError("Opening reference principal has delegated or unknown grants")
        scope = match.group(2, 3)
        for privilege in {v.strip() for v in match.group(1).split(",")}:
            if privilege == "SELECT":
                reads.add(scope)
            elif privilege == "INSERT" and writable and scope[0] == "q_live" and scope[1] in allowed_target:
                writes.add(scope[1])
            else:
                raise ValueError("Opening reference principal has unauthorized write/DDL authority")
    if not required <= reads or (writes not in (target, allowed_target) if writable else bool(writes)):
        raise ValueError("Opening reference publication grants are incomplete")
    return user


async def publish_opening_reference_async(reader_factory, writer_factory, source, **authority):
    """Bounded worker owns and closes both clients, including cancellation."""
    def run():
        reader = reader_factory()
        try:
            writer = writer_factory()
            try:
                return publish_opening_reference(reader, writer, source, **authority)
            finally:
                writer.close()
        finally:
            reader.close()
    task = asyncio.create_task(asyncio.to_thread(run))
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


class OpeningReferencePipeline:
    """Bounded operator lane from existing certified plans to publication.

    Capacity is explicitly provided by the operator's compute funnel. Full or
    duplicate units fail before opening clients; no hidden unbounded work queue
    or independently selected market population is introduced.
    """
    def __init__(self, reader_factory, writer_factory, *, maximum_concurrent_units):
        if type(maximum_concurrent_units) is not int or not 1 <= maximum_concurrent_units <= 8:
            raise ValueError("Opening producer needs explicit bounded worker capacity")
        self.reader_factory = reader_factory
        self.writer_factory = writer_factory
        self.capacity = maximum_concurrent_units
        self.active = set()
        self.failed = set()
        self.completed = 0
        self.skipped_verified = 0
        self._loop = None

    async def publish(self, market, prior_market, *, ownership, code_hash):
        if type(market) is not CertifiedMarketDayPlan or len(market.sessions) != 1:
            raise TypeError("Opening producer lane requires a certified single-session plan")
        if type(prior_market) not in (CertifiedMarketDayPlan, CertifiedPriorClosePlan):
            raise TypeError("Opening producer lane requires a certified prior-session plan")
        contract.require_hash(code_hash)
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ValueError("Opening producer lane cannot cross event-loop ownership")
        self._loop = loop
        key = (market.build_id, market.sessions[0])
        if key in self.failed:
            raise ValueError("Failed opening unit requires operator reconciliation")
        if key in self.active or len(self.active) >= self.capacity:
            raise ValueError("Opening producer capacity is full or the unit is already active")
        self.active.add(key)
        def run():
            ownership.assert_owner()
            reader = self.reader_factory()
            try:
                writer = self.writer_factory()
                try:
                    publication_permission_preflight(reader, writable=False)
                    source = certified_opening_input(reader, market, prior_market)
                    status = publish_opening_reference(reader, writer, source,
                        ownership=ownership, code_hash=code_hash)
                    return status, source.scope(code_hash)
                finally:
                    writer.close()
            finally:
                reader.close()
        task = asyncio.create_task(asyncio.to_thread(run))
        cancelled = False
        try:
            while True:
                try:
                    result = await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    cancelled = True
                    if task.cancelled():
                        self.failed.add(key)
                        raise
            status, scope = result
            if status == "published":
                self.completed += 1
            elif status == "skipped_verified":
                self.skipped_verified += 1
            else:
                raise ValueError("Opening producer returned an unknown receipt status")
            if cancelled:
                raise asyncio.CancelledError
            return status, scope
        except asyncio.CancelledError:
            # The worker has drained and its publication result was accounted
            # for above; interruption is not an invented failed receipt.
            raise
        except Exception:
            self.failed.add(key)
            if cancelled:
                raise asyncio.CancelledError from None
            raise
        finally:
            self.active.remove(key)
