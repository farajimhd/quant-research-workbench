import asyncio
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import json
import re
from threading import Event

import pytest

from pipelines.market_sip.events import opening_price_reference_producer as p
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
from src.trading_runtime.historical_reference_identity import ReferencePin
from src.market_engine import opening_price_reference as c


def source():
    uid = "00000000-0000-0000-0000-000000000001"
    unit = MarketDayUnit("prior", "2026-07-31", "ABC", "bars", uid, "a" * 64, 2, "123")
    prior = CertifiedMarketDayPlan(ExecutionInterval.fixed(1000), "prior", "b" * 64,
        ("2026-07-31",), ("ABC",), (unit,), (1000,), "c" * 64)
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(1000), "current", "d" * 64,
        ("2026-08-03",), ("ABC",), (), (1000,), "e" * 64)
    identity = CertifiedIdentityPlan("current", "2026-08-03", uid, market.token,
                                     ("ABC",), (1,), "f" * 64, "1" * 64)
    listing = c.OpeningListing("listing", "ABC", "symbol", "security", "interval", "2" * 64,
                               date(2025, 1, 1), None)
    return p.CertifiedOpeningInput(market, prior, identity, (listing,), "3" * 64)


class Store:
    def __init__(self):
        self.records = {t.name: [] for t in c.TABLES}
        self.writes = []
        self.failure = None
        self.bad_disk = False
        self.change_split = False
        self.split_reads = 0
        self.closed = []


class Client:
    base_url = "http://synthetic"
    def __init__(self, store, writer=False):
        self.store, self.writer = store, writer

    def close(self):
        self.store.closed.append("writer" if self.writer else "reader")

    def execute(self, query):
        def encode(rows):
            return "\n".join(json.dumps(r, default=str) for r in rows)
        if query == "SELECT currentUser()":
            return "writer" if self.writer else "reader"
        if query == "SHOW GRANTS FINAL":
            user = "writer" if self.writer else "reader"
            scopes = [("arte", t.name) for t in c.TABLES] + [("arte", "bars_v1"),
                ("q_live", "id_symbol_interval_v1"), ("q_live", "market_stock_split_v1")]
            scopes += [("system", n) for n in ("storage_policies", "tables", "columns", "parts", "data_skipping_indices")]
            return "\n".join([*(f"GRANT SELECT ON {db}.{n} TO {user}" for db, n in scopes),
                *(f"GRANT INSERT ON arte.{t.name} TO {user}" for t in c.TABLES if self.writer)])
        if "FROM system.storage_policies" in query:
            return encode([dict(disks=["live_market_ssd"])])
        if "FROM system.tables" in query:
            if "(database,name) IN" in query:
                return encode([dict(database=db, name=n, engine="MergeTree", storage_policy="live_market_ssd")
                    for db, n in [("arte", "bars_v1"), ("q_live", "id_symbol_interval_v1"), ("q_live", "market_stock_split_v1")]])
            return encode([dict(name=t.name, engine="MergeTree", storage_policy="live_market_ssd",
                                partition_key=t.partition, sorting_key=t.order) for t in c.TABLES])
        if "FROM system.columns" in query:
            return encode([dict(table=t.name, name=n, type=k) for t in c.TABLES for n, k in t.columns])
        if "FROM system.data_skipping_indices" in query:
            return ""
        if "FROM system.parts" in query:
            return encode([dict(disk_name="default")]) if self.store.bad_disk else ""
        if "FROM arte.bars_v1" in query:
            return encode([dict(ticker="ABC", closing_bar_count=2, prior_close_int=200000)])
        if "FROM q_live.market_stock_split_v1" in query:
            self.store.split_reads += 1
            return encode([dict(symbol_id="symbol", listing_id="listing", security_id="security",
                execution_date="2026-08-03", split_from=1.,
                split_to=3. if self.store.change_split and self.store.split_reads > 1 else 2.,
                inserted_at="2026-08-03 07:00:00.000000", source_content_sha256="4" * 64)])
        if query.startswith("INSERT"):
            assert self.writer
            table = re.search(r"INSERT INTO `arte`\.`([a-z0-9_]+)`", query).group(1)
            self.store.writes.append(table)
            self.store.records[table].extend(json.loads(row) for row in query.split("\n")[1:])
            if self.store.failure == table:
                raise RuntimeError("ambiguous INSERT")
            return ""
        for t in c.TABLES:
            if f"FROM arte.{t.name} " in query:
                rows = self.store.records[t.name]
                match = re.search(r"reference_attempt_id=toUUID\('([^']+)'\)", query)
                if match:
                    rows = [r for r in rows if r["reference_attempt_id"] == match.group(1)]
                if query.startswith("SELECT count() AS row_count"):
                    keys = {(r["listing_id"], r.get("source_ordinal", 0)) for r in rows}
                    return encode([dict(row_count=len(rows), unique_keys=len(keys))])
                if query.startswith("SELECT reference_attempt_id"):
                    return encode([{"reference_attempt_id": r["reference_attempt_id"]} for r in rows[:1]])
                if query.startswith("SELECT listing_id,ticker"):
                    return encode([{k: r[k] for k in ("listing_id", "ticker")} for r in rows])
                return encode(rows)
        raise AssertionError(query[:150])


class Owner:
    def __init__(self):
        self.checks = 0
    def assert_owner(self):
        self.checks += 1


@pytest.fixture
def verified_source(monkeypatch):
    # This suite exercises the publication protocol. Separate tests exercise
    # actual source-verification and its certificate/identity failure paths.
    monkeypatch.setattr(p.CertifiedOpeningInput, "verify", lambda self, reader: None)
    return source()


def run(store, src):
    return p.publish_opening_reference(Client(store), Client(store, True), src,
                                       ownership=Owner(), code_hash="5" * 64)


def test_publication_seals_last_then_exact_retry_is_read_only(verified_source):
    store = Store()
    assert run(store, verified_source) == "published"
    assert store.writes == [c.SPLIT.name, c.REFERENCE.name, c.COVERAGE.name]
    row = store.records[c.REFERENCE.name][0]
    assert row["adjusted_prior_close"] == 10.
    assert row["prior_market_unit_hash"] == c.content_hash(asdict(source().prior_market.units[0]))
    writes = list(store.writes)
    assert run(store, verified_source) == "skipped_verified"
    assert store.writes == writes


def test_ambiguous_children_cannot_be_sealed_or_blindly_retried(verified_source):
    store = Store()
    store.failure = c.REFERENCE.name
    with pytest.raises(RuntimeError, match="ambiguous"):
        run(store, verified_source)
    assert not store.records[c.COVERAGE.name]
    store.failure = None
    with pytest.raises(ValueError, match="operator reconciliation"):
        run(store, verified_source)


def test_source_change_before_seal_is_detected(verified_source):
    store = Store()
    store.change_split = True
    with pytest.raises(ValueError, match="changed before"):
        run(store, verified_source)
    assert not store.records[c.COVERAGE.name]


def test_wrong_storage_never_starts_a_writer(verified_source):
    store = Store()
    store.bad_disk = True
    with pytest.raises(ValueError, match="live_market_ssd"):
        run(store, verified_source)
    assert not store.writes


def test_query_uses_exact_prior_regular_bounds_and_opening_cutoff():
    src = source()
    close = p.prior_close_sql(src, src.listings)
    assert "bucket_index>=34200 AND bucket_index<57600" in close
    assert "resolution_ms=1000" in close and "attempt_id) IN" in close
    assert "argMax(close_int,bucket_index)" in close
    split = p.opening_splits_sql(src, src.listings)
    assert "execution_date>toDate('2026-07-31')" in split
    assert "execution_date<=toDate('2026-08-03')" in split
    assert "inserted_at<=toDateTime64('2026-08-03 08:00:00.000000',6,'UTC')" in split
    assert "listing_id,security_id) IN" in split


def test_async_cancellation_drains_worker_and_closes_owned_clients(monkeypatch):
    started, finish = Event(), Event()
    store = Store()
    def blocked(reader, writer, src, **authority):
        started.set()
        assert finish.wait(5)
        return "published"
    monkeypatch.setattr(p, "publish_opening_reference", blocked)
    async def scenario():
        task = asyncio.create_task(p.publish_opening_reference_async(
            lambda: Client(store), lambda: Client(store, True), source()))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert store.closed == ["writer", "reader"]


def certificate_source(monkeypatch):
    pin = ReferencePin("snapshot", "preopen-tradable-snapshot-v3", "6" * 64,
                       "2026-08-03 07:59:00", "2026-08-03 08:00:00")
    facts = [dict(ticker="ABC", symbol_id="symbol", listing_id="listing", security_id="security",
                  ibkr_conid=1, source_run_id="snapshot", source_inserted_at="2026-08-03 07:59:00.000")]
    src = source()
    src = replace(src, identity=replace(src.identity, content_hash=p.identity_content_hash(facts)),
                  population_reference_hash=c.content_hash(asdict(pin)))
    monkeypatch.setattr(p, "certify_identity_plan", lambda market, client: src.identity)
    monkeypatch.setattr(p, "load_reference_pin", lambda reader, market: pin)
    return src, facts


class CertificateClient(Client):
    def __init__(self, facts, *, bad_identity=False, bad_interval=False, bad_bars=False):
        super().__init__(Store())
        self.facts, self.bad_identity, self.bad_interval, self.bad_bars = facts, bad_identity, bad_interval, bad_bars

    def execute(self, query):
        def encode(rows):
            return "\n".join(json.dumps(r) for r in rows)
        if "FROM system.tables" in query:
            return encode([dict(name="strategy_one_identity_v3")])
        if "FROM arte.strategy_one_identity_v3" in query:
            return encode([dict(self.facts[0], listing_id="wrong")]) if self.bad_identity else encode(self.facts)
        if "FROM q_live.id_symbol_interval_v1" in query:
            return encode([dict(symbol_interval_id="interval", listing_id="listing", security_id="security",
                ticker_normalized="ABC", valid_from_date="2025-01-01", valid_to_date_exclusive=None,
                source_content_sha256="7" * 64 if self.bad_interval else "2" * 64)])
        if "FROM arte.bars_v1" in query:
            return encode([dict(ticker="ABC", attempt_id=source().prior_market.units[0].attempt_id,
                n=2, unique_keys=2, hash="999" if self.bad_bars else "123", resolutions=[1000],
                eligible_keys=2, key_hash="456")])
        raise AssertionError(query[:150])


def test_source_verification_binds_actual_identity_interval_bar_and_population_pins(monkeypatch):
    src, facts = certificate_source(monkeypatch)
    src.verify(CertificateClient(facts))


@pytest.mark.parametrize("fault", ["bad_identity", "bad_interval", "bad_bars"])
def test_source_verification_rejects_changed_authoritative_rows(monkeypatch, fault):
    src, facts = certificate_source(monkeypatch)
    with pytest.raises(ValueError, match="identity|interval|integrity"):
        src.verify(CertificateClient(facts, **{fault: True}))


def test_future_population_availability_and_incomplete_resolution_are_rejected(monkeypatch):
    src, facts = certificate_source(monkeypatch)
    pin = ReferencePin("snapshot", "preopen-tradable-snapshot-v3", "6" * 64,
                       "2026-08-03 08:00:01", "2026-08-03 08:00:00")
    monkeypatch.setattr(p, "load_reference_pin", lambda reader, market: pin)
    src = replace(src, population_reference_hash=c.content_hash(asdict(pin)))
    with pytest.raises(ValueError, match="reference pin"):
        src.verify(CertificateClient(facts))
    with pytest.raises(ValueError, match="one-second"):
        replace(src, prior_market=replace(src.prior_market, required_resolutions_ms=(100,)))


def test_factory_uses_certified_central_identity_and_effective_intervals(monkeypatch):
    src, facts = certificate_source(monkeypatch)
    rebuilt = p.certified_opening_input(CertificateClient(facts), src.market, src.prior_market)
    assert rebuilt == src


def test_current_only_listing_is_explicitly_excluded_without_a_prior_bar_read(verified_source):
    src = replace(verified_source, listings=(replace(verified_source.listings[0],
        valid_from=date(2026, 8, 3)),), prior_market=replace(verified_source.prior_market,
        tickers=(), units=()))
    store = Store()
    assert run(store, src) == "published"
    assert store.records[c.REFERENCE.name][0]["status"] == "excluded_identity"
    assert store.records[c.COVERAGE.name][0]["excluded_identity_count"] == 1
    assert store.split_reads == 0


def test_consumer_cold_read_verifies_publication_and_pins_before_scanner_use(verified_source):
    from src.backend.qmd_opening_reference import certify_opening_reference
    store = Store()
    run(store, verified_source)
    expected = store.records[c.COVERAGE.name][0]["content_hash"]
    certificate = certify_opening_reference(Client(store), verified_source.scope("5" * 64),
                                            expected_content_hash=expected)
    row = certificate.rows_by_listing["listing"]
    assert row["adjusted_prior_close"] == 10.
    assert row["available_at"] == datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
    assert len(certificate.resolved_rows()) == 1
    with pytest.raises(TypeError):
        row["adjusted_prior_close"] = 99.
    with pytest.raises(ValueError, match="pinned"):
        certify_opening_reference(Client(store), verified_source.scope("5" * 64),
                                  expected_content_hash="9" * 64)
    with pytest.raises(ValueError, match="source pins"):
        certify_opening_reference(Client(store), verified_source.scope("9" * 64))


def test_consumer_detects_corrupt_children_and_missing_seals(verified_source):
    from src.backend.qmd_opening_reference import certify_opening_reference
    store = Store()
    with pytest.raises(ValueError, match="coverage is missing"):
        certify_opening_reference(Client(store), verified_source.scope("5" * 64))
    run(store, verified_source)
    store.records[c.REFERENCE.name][0]["adjusted_prior_close"] = 999.
    with pytest.raises(ValueError, match="content hashes"):
        certify_opening_reference(Client(store), verified_source.scope("5" * 64))


def test_operator_pipeline_bounds_inflight_io_and_drains_cancelled_publication(monkeypatch):
    started, finish = Event(), Event()
    store = Store()
    src = source()
    monkeypatch.setattr(p, "certified_opening_input", lambda reader, market, prior: src)
    def publish(reader, writer, source, **authority):
        started.set()
        assert finish.wait(5)
        return "published"
    monkeypatch.setattr(p, "publish_opening_reference", publish)
    lane = p.OpeningReferencePipeline(lambda: Client(store), lambda: Client(store, True),
                                     maximum_concurrent_units=1)
    async def scenario():
        task = asyncio.create_task(lane.publish(src.market, src.prior_market,
                                               ownership=Owner(), code_hash="5" * 64))
        assert await asyncio.to_thread(started.wait, 5)
        with pytest.raises(ValueError, match="capacity is full"):
            await lane.publish(src.market, src.prior_market, ownership=Owner(), code_hash="5" * 64)
        task.cancel()
        await asyncio.sleep(.02)
        assert lane.active and not task.done()
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert not lane.active and not lane.failed
    assert lane.completed == 1 and lane.skipped_verified == 0
    assert store.closed == ["writer", "reader"]


def test_failed_operator_unit_is_not_blindly_retried(monkeypatch):
    store = Store()
    src = source()
    monkeypatch.setattr(p, "certified_opening_input", lambda reader, market, prior: src)
    def fail(reader, writer, source, **authority):
        raise RuntimeError("ambiguous publication")
    monkeypatch.setattr(p, "publish_opening_reference", fail)
    lane = p.OpeningReferencePipeline(lambda: Client(store), lambda: Client(store, True),
                                     maximum_concurrent_units=1)
    async def scenario():
        with pytest.raises(RuntimeError, match="ambiguous"):
            await lane.publish(src.market, src.prior_market, ownership=Owner(), code_hash="5" * 64)
        with pytest.raises(ValueError, match="operator reconciliation"):
            await lane.publish(src.market, src.prior_market, ownership=Owner(), code_hash="5" * 64)
    asyncio.run(scenario())
    assert len(lane.failed) == 1 and not lane.active
    assert store.closed == ["writer", "reader"]
