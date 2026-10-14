import asyncio
from dataclasses import replace
from datetime import date
import json
import re
from threading import Event
from types import MappingProxyType

import polars as pl
import numpy as np
import pytest

from pipelines.market_sip.events import columnar_history_producer as p
from pipelines.market_sip.events.opening_price_reference_producer import CertifiedOpeningInput
from pipelines.market_sip.events.liquidity_resolution_producer import CertifiedLiquidityInput
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
from src.backend.backtest_liquidity_price import PriceLevelUnit
from src.backend.backtest_liquidity_resolution import CertifiedLiquidityResolution
from src.backend.qmd_opening_reference import CertifiedOpeningReference
from src.backend import qmd_columnar_history as reader
from src.market_engine import opening_price_reference as opening
from src.market_engine import columnar_history_product as c

UID = "00000000-0000-0000-0000-000000000001"
CODE = "5" * 64


def source(n=200):
    prior_unit = MarketDayUnit("prior", "2026-07-31", "ABC", "bars", UID, "a" * 64, 2, "123")
    prior = CertifiedMarketDayPlan(ExecutionInterval.fixed(1000), "prior", "b" * 64,
        ("2026-07-31",), ("ABC",), (prior_unit,), (1000,), "c" * 64)
    bar = MarketDayUnit("current", "2026-08-03", "ABC", "bars", UID, "d" * 64, n - 3, "123")
    broker = replace(bar, stage="broker_100ms", output_rows=n - 1)
    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(1000), "current", "d" * 64,
        ("2026-08-03",), ("ABC",), (bar, broker), (1000,), "e" * 64)
    identity = CertifiedIdentityPlan("current", "2026-08-03", UID, market.token,
        ("ABC",), (1,), "f" * 64, "1" * 64)
    listing = opening.OpeningListing("listing", "ABC", "symbol", "security", "interval", "2" * 64,
        date(2025, 1, 1), None)
    root = CertifiedOpeningInput(market, prior, identity, (listing,), "3" * 64)
    reference = CertifiedOpeningReference(root.scope("4" * 64), UID, "6" * 64,
        MappingProxyType({"listing": MappingProxyType({"ticker": "ABC"})}))
    price = PriceLevelUnit("2026-08-03", "ABC", UID, UID, n - 1, n - 1, (n - 1) * 10., "7" * 64)
    liquid_source = CertifiedLiquidityInput(market, broker, price)
    liquid = CertifiedLiquidityResolution(liquid_source.unit, UID, "8" * 64, "9" * 64, n - 1, n - 1, (n - 1) * 10.)
    fields = tuple(sorted((c.BY_NAME["history.attention_mean.58"], c.BY_NAME["history.swing_low.2.1"]),
        key=lambda f: f.field_id))
    return p.CertifiedHistoryInput(root, reference, liquid_source, liquid, "ABC", 34200, 34200 + n, fields, 30)


class Store:
    def __init__(self, n=200):
        self.records = {t.name: [] for t in c.TABLES}
        self.writes = []
        self.failure = None
        self.bad_disk = False
        self.closed = []
        self.bars = [dict(bucket_index=i, price_valid=1, extremes_valid=int(i % 7 != 0),
            close_int=100000 + i % 21, high_int=100100, low_int=99900, volume=10.) for i in range(34203, 34200 + n)]


def encode(rows):
    return "\n".join(json.dumps(r, default=str) for r in rows)


class Client:
    base_url = "http://synthetic"

    def __init__(self, store, writer=False):
        self.store, self.writer = store, writer

    def close(self):
        self.store.closed.append("writer" if self.writer else "reader")

    def execute(self, query):
        if isinstance(query, bytes):
            from product_binary_fixture import product_request_as_json
            query = product_request_as_json(query, c.TABLES)
        user = "writer" if self.writer else "reader"
        if query == "SELECT currentUser()":
            return user
        if query == "SHOW GRANTS FINAL":
            scopes = [("q_live", t.name) for t in c.TABLES]
            scopes += [("system", n) for n in ("storage_policies", "tables", "columns", "parts", "data_skipping_indices")]
            return "\n".join([*(f"GRANT SELECT ON {db}.{n} TO {user}" for db, n in scopes),
                *(f"GRANT INSERT ON q_live.{t.name} TO {user}" for t in c.TABLES if self.writer)])
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
        if query.startswith("INSERT"):
            assert self.writer
            name = re.search(r"INSERT INTO `q_live`\.`([a-z0-9_]+)`", query).group(1)
            self.store.writes.append(name)
            if name == c.VALUE.name and 'FORMAT JSONEachRow' not in query:
                rows = []
                for select in query.split(" UNION ALL "):
                    row = {}
                    for value, field in re.findall(r"CAST\((NULL|'(?:\\.|[^'])*'),'(?:\\.|[^'])*'\) AS `([a-z_]+)`", select):
                        row[field] = None if value == "NULL" else re.sub(r"\\(.)", lambda m: m.group(1), value[1:-1], flags=re.S)
                    row["value"] = None if row["value_bits"] is None else float(np.asarray(int(row["value_bits"]), dtype=np.uint32).view(np.float32))
                    rows.append(c.restore_record(c.VALUE, row))
            else:
                rows = [json.loads(line) for line in query.split("\n")[1:] if line.strip()]
            self.store.records[name].extend(rows)
            if self.store.failure == name:
                raise RuntimeError("ambiguous INSERT")
            return ""
        if "FROM arte.bars_v1 " in query:
            rows = list(self.store.bars)
        else:
            name = next((t.name for t in c.TABLES if f"FROM q_live.{t.name} " in query), None)
            if name is None:
                raise AssertionError(query)
            rows = list(self.store.records[name])
            match = re.search(r"derivation_attempt_id=toUUID\('([^']+)'\)", query)
            if match:
                rows = [r for r in rows if r["derivation_attempt_id"] == match.group(1)]
            if query.startswith("SELECT count() AS row_count"):
                keys = {(r.get("field_id"), r.get("first_ordinal"), r.get("bucket_index")) for r in rows}
                return encode([dict(row_count=len(rows), unique_keys=len(keys))])
            if query.startswith("SELECT derivation_attempt_id"):
                return encode([{"derivation_attempt_id": r["derivation_attempt_id"]} for r in rows[:1]])
        cursor = re.search(r"AND \(field_id,first_ordinal\)>\((\d+),(\d+)\)", query)
        if cursor:
            after = tuple(map(int, cursor.groups()))
            rows = [r for r in rows if (r["field_id"], r["first_ordinal"]) > after]
        for key in ("bucket_index", "first_ordinal", "field_id"):
            for symbol, predicate in ((">=", lambda a, b: a >= b), ("<", lambda a, b: a < b),
                                      (">", lambda a, b: a > b)):
                match = re.search(key + re.escape(symbol) + r"(\d+)", query)
                if match:
                    rows = [r for r in rows if predicate(r[key], int(match.group(1)))]
        match = re.search(r"AND field_id=(\d+)", query)
        if match:
            rows = [r for r in rows if r["field_id"] == int(match.group(1))]
        if "ORDER BY field_id,first_ordinal" in query:
            rows.sort(key=lambda r: (r["field_id"], r["first_ordinal"]))
        elif "ORDER BY first_ordinal" in query:
            rows.sort(key=lambda r: r["first_ordinal"])
        elif "ORDER BY field_id" in query:
            rows.sort(key=lambda r: r["field_id"])
        elif "ORDER BY bucket_index" in query:
            rows.sort(key=lambda r: r["bucket_index"])
        match = re.search(r"LIMIT (\d+)(?: OFFSET (\d+))?", query)
        if match:
            start = int(match.group(2) or 0)
            rows = rows[start:start + int(match.group(1))]
        return encode(rows)


class Owner:
    def assert_owner(self):
        pass


@pytest.mark.parametrize('failure', [None, 'boundary_duplicate', 'missing', 'changed'])
def test_keyset_readback_checks_every_page_and_boundary_duplicates(monkeypatch, failure):
    from types import SimpleNamespace
    expected = {c.FIELD: [dict(field_id=i) for i in range(1100)],
                c.CLOCK: [dict(bucket_index=i) for i in range(1100)],
                c.VALUE: [dict(field_id=i // 550, first_ordinal=i % 550, value=i)
                          for i in range(1100)]}
    actual = {table: [dict(row) for row in rows] for table, rows in expected.items()}
    if failure == 'boundary_duplicate':
        actual[c.VALUE].insert(1024, dict(actual[c.VALUE][1023]))
    elif failure == 'missing':
        actual[c.VALUE].pop(1024)
    elif failure == 'changed':
        actual[c.VALUE][1024]['value'] = -1
    queries = []
    def read(reader, query, bound):
        queries.append(query)
        table = next(t for t in actual if 'FROM q_live.' + t.name + ' ' in query)
        rows = actual[table]
        keys = ('field_id', 'first_ordinal') if table == c.VALUE else (
            'field_id' if table == c.FIELD else 'bucket_index',)
        key = lambda r: tuple(r[k] for k in keys)
        if query.startswith('SELECT count()'):
            return [dict(row_count=len(rows), unique_keys=len({key(r) for r in rows}))]
        cursor = re.search(r'AND (?:\(field_id,first_ordinal\)|field_id|bucket_index)>(\([^)]*\)|\d+)', query)
        if cursor:
            last = tuple(map(int, cursor[1].strip('()').split(',')))
            rows = [r for r in rows if key(r) > last]
        return rows[:bound]
    monkeypatch.setattr(p, '_rows', read)
    monkeypatch.setattr(c, 'child_records', lambda prepared, table: iter(expected[table]))
    monkeypatch.setattr(c, 'restore_record', lambda table, row: row)
    prepared = SimpleNamespace(scope=source().scope(UID, CODE))
    if failure:
        with pytest.raises(ValueError):
            p._verify_children_before_seal(None, prepared)
    else:
        p._verify_children_before_seal(None, prepared)
    assert not any('OFFSET' in query for query in queries)
    assert any('AND (field_id,first_ordinal)>' in query for query in queries)


@pytest.mark.parametrize('failure', [None, 'duplicate', 'outside', 'negative', 'nonfinite', 'schema'])
def test_activity_projection_keeps_scope_order_and_finite_notional(failure):
    certificate=source().liquidity
    rows=[dict(bucket_index=34200,execution_notional=23.),
          dict(bucket_index=34201,execution_notional=0.)]
    if failure=='duplicate':rows[1]['bucket_index']=34200
    if failure=='outside':rows[1]['bucket_index']=38296
    if failure=='negative':rows[1]['execution_notional']=-1.
    if failure=='nonfinite':rows[1]['execution_notional']=float('inf')
    if failure=='schema':rows[1]['unexpected']=0
    queries=[]
    class Reader:
        def execute(self,query):
            queries.append(query)
            return encode(rows)
    if failure:
        with pytest.raises(ValueError):
            p.read_history_activity_window(Reader(),certificate,first_bucket=34200,stop_bucket=38296)
    else:
        assert p.read_history_activity_window(Reader(),certificate,first_bucket=34200,stop_bucket=38296)==tuple(rows)
    assert 'execution_price_levels' not in queries[0]
    assert certificate.derivation_attempt_id in queries[0]
    assert 'LIMIT 4097' in queries[0]


@pytest.fixture
def prepared_source(monkeypatch):
    # Publication protocol uses synthetic certified parent providers. Tests
    # below exercise the real source verifier's calls and mismatch rejection.
    monkeypatch.setattr(p.CertifiedHistoryInput, "verify", lambda self, reader: None)
    monkeypatch.setattr(p, "read_history_activity_window", lambda reader, certificate, first_bucket, stop_bucket:
        tuple(dict(bucket_index=i, execution_notional=23.) for i in range(max(first_bucket, 34201), stop_bucket)))
    return source()


def publish(store, src):
    return p.publish_history(Client(store), Client(store, True), src, ownership=Owner(), code_hash=CODE)


def consume(store, src):
    header = c.restore_record(c.COVERAGE, store.records[c.COVERAGE.name][0])
    scope = src.scope(header["derivation_attempt_id"], CODE)
    return reader.certify_history_product(Client(store), scope, src.fields, src.parents(CODE),
        expected_content_hash=header["content_hash"])


def test_coverage_last_publication_exact_cold_consumer_and_readonly_retry(prepared_source):
    store = Store()
    assert publish(store, prepared_source) == "published"
    assert store.writes[-1] == c.COVERAGE.name
    assert all(name != c.COVERAGE.name for name in store.writes[:-1])
    certificate = consume(store, prepared_source)
    actual = certificate.window(first_ordinal=0, stop_ordinal=200, evaluation_block="test-block")
    assert actual.height == 200
    assert actual["execution_notional"][1] == 23.
    assert actual["share_volume"][1] == 0.
    assert actual["source_known"].head(3).to_list() == [0, 1, 1]
    writes = list(store.writes)
    assert publish(store, prepared_source) == "skipped_verified"
    assert store.writes == writes


def test_activity_only_population_skips_features_and_preserves_cold_seal(prepared_source, monkeypatch):
    from src.backend import qmd_columnar_history_transport as transport
    from test_qmd_columnar_history_transport import response_for
    def forbidden_features(*args, **kwargs):
        raise AssertionError("Scanner population must not calculate program features")
    monkeypatch.setattr(c, "prepare_causal_history", forbidden_features)
    source = replace(prepared_source, fields=())
    store = Store()
    assert publish(store, source) == "published"
    certificate = consume(store, source)
    assert certificate.header["field_count"] == certificate.header["value_row_count"] == 0
    assert store.records[c.FIELD.name] == store.records[c.VALUE.name] == []
    assert set(store.writes) == {c.CLOCK.name, c.COVERAGE.name}
    pin = certificate.pin()
    frame = transport.read_history_window(pin, first_ordinal=0, stop_ordinal=200,
        evaluation_block="scanner-window", as_of=c.completed_clock(pin.scope, pin.scope.stop_bucket - 1),
        history_post=lambda path, request, timeout: response_for(certificate, 0, 200))
    from polars.testing import assert_frame_equal
    assert_frame_equal(frame, certificate.window(first_ordinal=0, stop_ordinal=200,
        evaluation_block="scanner-window"), check_exact=True)
    assert frame["rolling_volume"][-1] == 300.
    assert publish(store, source) == "skipped_verified"
    field = prepared_source.fields[0]
    store.records[c.FIELD.name].extend(p.wire_records(c.FIELD, [{**pin.scope.prefix(),
        "field_id": field.field_id, "field_name": field.name, "unit": field.unit}]))
    with pytest.raises(ValueError, match="catalog differs"):
        consume(store, source)


def test_ambiguous_child_never_seals_or_blindly_retries(prepared_source):
    store = Store()
    store.failure = c.VALUE.name
    with pytest.raises(RuntimeError, match="ambiguous"):
        publish(store, prepared_source)
    assert not store.records[c.COVERAGE.name]
    store.failure = None
    with pytest.raises(ValueError, match="operator reconciliation"):
        publish(store, prepared_source)


@pytest.mark.parametrize("corruption", ["missing_clock", "extra_field", "duplicate_interval", "value", "parent"])
def test_cold_consumer_rejects_corrupted_normalized_evidence(prepared_source, corruption):
    store = Store()
    publish(store, prepared_source)
    if corruption == "missing_clock":
        store.records[c.CLOCK.name].pop()
    elif corruption == "extra_field":
        row = dict(store.records[c.FIELD.name][0], field_id=9999)
        store.records[c.FIELD.name].append(row)
    elif corruption == "duplicate_interval":
        store.records[c.VALUE.name].append(dict(store.records[c.VALUE.name][0]))
    elif corruption == "value":
        next(r for r in store.records[c.VALUE.name] if r["valid"])["value"] = 999.
    else:
        store.records[c.COVERAGE.name][0]["source_bar_unit_hash"] = "b" * 64
    with pytest.raises(ValueError):
        consume(store, prepared_source)


def test_wrong_disk_blocks_writers_and_reader_cannot_write(prepared_source):
    store = Store()
    store.bad_disk = True
    with pytest.raises(ValueError):
        publish(store, prepared_source)
    assert not store.writes
    with pytest.raises(ValueError, match="unauthorized"):
        reader.permission_preflight(Client(store, True))


def test_source_contract_rejects_mismatched_liquidity_parent():
    src = source()
    with pytest.raises(ValueError, match="scope"):
        replace(src, liquidity=replace(src.liquidity, source=replace(src.liquidity.source, source_attempt_id=
            "00000000-0000-0000-0000-000000000002")))


def test_actual_source_verifier_rechecks_all_authorities_and_rejects_change(monkeypatch):
    src = source()
    calls = []
    monkeypatch.setattr(CertifiedOpeningInput, "verify", lambda self, reader: calls.append("opening-source"))
    monkeypatch.setattr(CertifiedLiquidityInput, "verify", lambda self, reader: calls.append("liquidity-source"))
    monkeypatch.setattr(p, "certify_opening_reference", lambda *a, **k: calls.append("opening-seal") or src.opening_reference)
    monkeypatch.setattr(p, "certify_liquidity_resolution", lambda *a, **k: calls.append("liquidity-seal") or src.liquidity)
    monkeypatch.setattr(p, "_verify_market_batch", lambda *a, **k: calls.append("bars"))
    src.verify(object())
    assert calls == ["opening-source", "liquidity-source", "opening-seal", "liquidity-seal", "bars"]
    monkeypatch.setattr(p, "certify_liquidity_resolution", lambda *a, **k: replace(src.liquidity, content_hash="a" * 64))
    with pytest.raises(ValueError, match="changed"):
        src.verify(object())


def test_async_consumer_cancel_drains_and_closes_owned_reader(monkeypatch):
    entered, release = Event(), Event()
    store = Store()
    def work(*args, **kwargs):
        entered.set()
        release.wait(5)
        return "completed"
    monkeypatch.setattr(reader, "certify_history_product", work)
    async def scenario():
        task = asyncio.create_task(reader.certify_history_product_async(lambda: Client(store), None, None, None))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert store.closed == ["reader"]


def test_bounded_pipeline_rejects_duplicates_and_accounts_cancelled_success(prepared_source, monkeypatch):
    entered, release = Event(), Event()
    store = Store()
    original = p.publish_history
    def blocked(*args, **kwargs):
        entered.set()
        release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(p, "publish_history", blocked)
    pipeline = p.HistoryProductPipeline(lambda: Client(store), lambda: Client(store, True), maximum_concurrent_units=1)
    async def scenario():
        task = asyncio.create_task(pipeline.publish(prepared_source, ownership=Owner(), code_hash=CODE))
        assert await asyncio.to_thread(entered.wait, 5)
        with pytest.raises(ValueError, match="capacity"):
            await pipeline.publish(prepared_source, ownership=Owner(), code_hash=CODE)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        assert len(pipeline.active) == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pipeline.completed == 1
        assert not pipeline.active and not pipeline.failed
        receipt = await pipeline.publish(prepared_source, ownership=Owner(), code_hash=CODE)
        assert receipt.status == "skipped_verified"
        assert pipeline.skipped_verified == 1
        assert receipt.content_hash == c.restore_record(c.COVERAGE, store.records[c.COVERAGE.name][0])["content_hash"]
    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert store.closed == ["writer", "reader", "writer", "reader"]


def test_failed_pipeline_unit_requires_reconciliation(prepared_source):
    store = Store()
    store.failure = c.CLOCK.name
    pipeline = p.HistoryProductPipeline(lambda: Client(store), lambda: Client(store, True), maximum_concurrent_units=1)
    async def scenario():
        with pytest.raises(RuntimeError, match="ambiguous"):
            await pipeline.publish(prepared_source, ownership=Owner(), code_hash=CODE)
        assert len(pipeline.failed) == 1 and not pipeline.active
        with pytest.raises(ValueError, match="reconciliation"):
            await pipeline.publish(prepared_source, ownership=Owner(), code_hash=CODE)
    asyncio.run(scenario())
    assert not store.records[c.COVERAGE.name]


def test_multi_page_clocks_and_intervals_preserve_exact_window_values(prepared_source):
    src = source(2200)
    src = replace(src, fields=tuple(sorted((*src.fields, c.BY_NAME["history.momentum_close.1"]), key=lambda f: f.field_id)))
    store = Store(2200)
    publish(store, src)
    certificate = consume(store, src)
    assert certificate.prepared.values.filter(pl.col("field_id") == c.BY_NAME["history.momentum_close.1"].field_id).height > 2048
    expected_product = p.materialize_history(Client(store), src, certificate.prepared.scope, code_hash=CODE)
    expected = c.expand_values(expected_product, first_ordinal=2040, stop_ordinal=2110, evaluation_block="test-block")
    assert certificate.window(first_ordinal=2040, stop_ordinal=2110, evaluation_block="test-block").equals(expected)
