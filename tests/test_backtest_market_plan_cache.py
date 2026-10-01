import json
from types import SimpleNamespace

import pytest

from src.backend import backtest_market_plan_cache as subject


class InventoryClient:
    def __init__(self):
        self.part_name = "part-1"
        self.unrelated_part_name = None
        self.disk_name = "live_market_ssd"
        self.selected_queries = 0

    def execute(self, sql):
        if "FROM system.tables" in sql:
            rows = [dict(name=name, uuid=f"uuid-{name}",
                         storage_policy="live_market_ssd",
                         metadata_modification_time="2026-09-25 00:00:00")
                    for name in subject._NAMES]
        elif "FROM system.columns" in sql:
            rows = [dict(table=name, name="run_id", type="String", position=1)
                    for name in subject._NAMES]
        elif "FROM system.parts" in sql:
            rows = [dict(table="bars_v1", name=self.part_name,
                         disk_name=self.disk_name, rows=10, bytes_on_disk=100,
                         hash_of_all_files="a" * 32)]
            if self.unrelated_part_name:
                rows.append(dict(table="bars_v1", name=self.unrelated_part_name,
                                 disk_name=self.disk_name, rows=20, bytes_on_disk=200,
                                 hash_of_all_files="b" * 32))
        elif sql.startswith("SELECT table_name,part_name FROM ("):
            self.selected_queries += 1
            assert all(f"FROM arte.{name} " in sql for name in subject._NAMES)
            return f"bars_v1\t{self.part_name}"
        else:
            raise AssertionError(sql)
        return "\n".join(json.dumps(row) for row in rows)


def test_market_inventory_fingerprint_invalidates_on_part_change_and_fails_off_ssd():
    client = InventoryClient()
    first = subject.market_inventory_fingerprint(client)
    assert len(first) == 64
    assert subject.market_inventory_fingerprint(client) == first
    client.part_name = "part-2"
    assert subject.market_inventory_fingerprint(client) != first
    client.disk_name = "default"
    with pytest.raises(RuntimeError, match="outside SSD"):
        subject.market_inventory_fingerprint(client)


def test_product_inventory_fingerprint_requires_exact_ssd_tables(monkeypatch):
    monkeypatch.setattr(subject, "_NAMES", ("bars_v1", "indicators_v1"))
    client = InventoryClient()
    names = ("bars_v1", "indicators_v1")
    first = subject.product_inventory_fingerprint(client, names)
    assert len(first) == 64
    client.part_name = "part-2"
    assert subject.product_inventory_fingerprint(client, names) != first
    client.disk_name = "default"
    with pytest.raises(RuntimeError, match="outside SSD"):
        subject.product_inventory_fingerprint(client, names)
    with pytest.raises(ValueError, match="distinct arte table names"):
        subject.product_inventory_fingerprint(client, ("bars_v1", "bars_v1"))


def test_selected_product_fences_only_scope_and_retains_global_ssd_checks(monkeypatch):
    monkeypatch.setattr(subject, "_NAMES", ("bars_v1", "indicators_v1"))
    class ScopedClient(InventoryClient):
        def execute(self, sql):
            if sql.startswith("SELECT table_name,part_name FROM ("):
                assert "source_build_id='" + "a" * 64 + "'" in sql
                assert "session_date=toDate('2026-08-18')" in sql
                assert "ticker IN ('AMIX','SLE')" in sql
            return super().execute(sql)
    client = ScopedClient()
    client.part_name = "selected_1"
    scope = dict(source_build_id="a" * 64, session_date="2026-08-18",
                 tickers=("AMIX", "SLE"))
    names = subject._NAMES
    first = subject.selected_product_inventory_fingerprint(client, names, **scope)
    client.unrelated_part_name = "unrelated_1"
    assert subject.selected_product_inventory_fingerprint(client, names, **scope) == first
    client.part_name = "selected_2"
    assert subject.selected_product_inventory_fingerprint(client, names, **scope) != first
    client.disk_name = "default"
    with pytest.raises(RuntimeError, match="off-SSD"):
        subject.selected_product_inventory_fingerprint(client, names, **scope)


@pytest.mark.parametrize("selected", ["bars_v1\tmissing", "unknown\tpart_1",
                                      "bars_v1\tpart_1\nbars_v1\tpart_1"])
def test_selected_product_rejects_missing_or_malformed_part_inventory(monkeypatch, selected):
    monkeypatch.setattr(subject, "_NAMES", ("bars_v1",))
    class ChangedClient(InventoryClient):
        def execute(self, sql):
            if sql.startswith("SELECT table_name,part_name FROM ("):
                return selected
            return super().execute(sql)
    client = ChangedClient()
    client.part_name = "part_1"
    with pytest.raises(RuntimeError, match="merged|invalid part"):
        subject.selected_product_inventory_fingerprint(
            client, subject._NAMES, source_build_id="a" * 64,
            session_date="2026-08-18", tickers=("SLE",))


@pytest.mark.parametrize("failure", ["missing_table", "missing_columns", "policy",
                                     "unrelated_off_ssd"])
def test_selected_product_does_not_hide_global_authority_failures(monkeypatch, failure):
    monkeypatch.setattr(subject, "_NAMES", ("bars_v1", "indicators_v1"))
    class BrokenClient(InventoryClient):
        def execute(self, sql):
            result = super().execute(sql)
            if "FORMAT JSONEachRow" not in sql:
                return result
            rows = [json.loads(line) for line in result.splitlines()]
            if "FROM system.tables" in sql:
                if failure == "missing_table":
                    rows.pop()
                elif failure == "policy":
                    rows[0]["storage_policy"] = "default"
            elif "FROM system.columns" in sql and failure == "missing_columns":
                rows.pop()
            elif "FROM system.parts" in sql and failure == "unrelated_off_ssd":
                rows[-1]["disk_name"] = "default"
            return "\n".join(json.dumps(row) for row in rows)
    client = BrokenClient()
    client.part_name = "selected_1"
    client.unrelated_part_name = "unrelated_1"
    with pytest.raises(RuntimeError, match="schema or policy|off-SSD"):
        subject.selected_product_inventory_fingerprint(
            client, subject._NAMES, source_build_id="a" * 64,
            session_date="2026-08-18", tickers=("SLE",))


def test_selected_inventory_ignores_unrelated_parts_but_fences_selected_parts():
    client = InventoryClient()
    client.part_name = "part_1"
    args = (("a" * 64,), ("2026-08-18",))
    first = subject.selected_market_inventory_fingerprint(client, *args)
    assert client.selected_queries == 1
    global_before = subject.market_inventory_fingerprint(client)
    client.unrelated_part_name = "part_2"
    assert subject.market_inventory_fingerprint(client) != global_before
    assert subject.selected_market_inventory_fingerprint(client, *args) == first
    client.part_name = "part_3"
    assert subject.selected_market_inventory_fingerprint(client, *args) != first
    client.disk_name = "default"
    with pytest.raises(RuntimeError, match="off-SSD"):
        subject.selected_market_inventory_fingerprint(client, *args)


def test_market_plan_cache_requires_exact_keeper_and_inventory():
    cache = subject.MarketPlanCache()
    plan = object()
    cache.put(("scope",), {"build": "proof"}, "fingerprint", plan,
              selected_fingerprint="selected")
    assert cache.get(("scope",), {"build": "proof"}, "fingerprint") is plan
    assert cache.get_selected(("scope",), {"build": "proof"}, "selected") is plan
    assert cache.get(("scope",), {"build": "changed"}, "fingerprint") is None
    assert cache.get_selected(("scope",), {"build": "changed"}, "selected") is None
    assert cache.get(("scope",), {"build": "proof"}, "changed") is None
    assert cache.get_selected(("scope",), {"build": "proof"}, "changed") is None
    with pytest.raises(RuntimeError, match="attested"):
        cache.put(("scope",), {"build": None}, "fingerprint", plan)


def test_market_plan_cache_retains_run_and_context_without_weakening_proofs():
    cache = subject.MarketPlanCache(max_entries=2)
    proofs = {"build": "proof"}
    run = object()
    context = object()
    cache.put(("run",), proofs, "parts-run", run,
              selected_fingerprint="selected-run")
    cache.put(("context",), proofs, "parts-context", context,
              selected_fingerprint="selected-context")
    assert cache.get(("run",), proofs, "parts-run") is run
    assert cache.get_selected(("context",), proofs, "selected-context") is context
    assert cache.get(("run",), {"build": "changed"}, "parts-run") is None
    assert cache.get_selected(("context",), proofs, "changed") is None
    cache.put(("third",), proofs, "parts-third", object())
    assert len(cache._entries) == 2
    assert cache.get(("run",), proofs, "parts-run") is None
    assert cache.get(("context",), proofs, "parts-context") is context
    with pytest.raises(ValueError, match="bounded"):
        subject.MarketPlanCache(max_entries=0)


def test_fixed_plan_reuses_only_unchanged_verified_snapshot(monkeypatch):
    from src.backend import backtest_market_data as market
    from src.trading_runtime import arte_market_day_cold_preflight as cold
    from src.trading_runtime import arte_market_day_keeper as keeper_module
    from src.backend import backtest_market_keeper_pool as keeper_pool
    from research.mlops import clickhouse

    class Reader:
        def close(self):
            pass

    class Session:
        client = object()
        writable = True
        def close(self):
            pass

    monkeypatch.setattr(clickhouse, "ClickHouseHttpClient", Reader)
    monkeypatch.setattr(market, "readonly_clickhouse_client", lambda **_: Reader())
    monkeypatch.setattr(cold, "market_day_fence_build_ids", lambda *_: ("a" * 64,))
    monkeypatch.setattr(keeper_pool, "open_workstation_keeper_session", Session)
    monkeypatch.setattr(keeper_pool, "MARKET_CERTIFICATE_KEEPER_POOL",
                        keeper_pool.MarketCertificateKeeperPool())
    monkeypatch.setattr(keeper_module, "MarketDayKeeperReader",
                        lambda _: SimpleNamespace(load=lambda _build: "proof"))
    generations = []
    monkeypatch.setattr(cold, "discover_cold_certified_market_day_plan",
                        lambda *_args, **_kwargs: generations.append(object()) or generations[-1])
    fingerprint = ["part-1"]
    selected = ["selected-1"]
    monkeypatch.setattr(subject, "market_inventory_fingerprint",
                        lambda _reader: fingerprint[0])
    monkeypatch.setattr(subject, "selected_market_inventory_fingerprint",
                        lambda *_args: selected[0])
    monkeypatch.setattr(subject, "MARKET_PLAN_CACHE", subject.MarketPlanCache())
    args = dict(sessions=("2026-08-18",), tickers=("ABCD",),
                configuration={"strategy": {"execution_interval": "100ms"}})
    first = market.certified_market_plan_from_arte(**args)
    assert market.certified_market_plan_from_arte(**args) is first
    assert len(generations) == 1
    fingerprint[0] = "part-2"
    assert market.certified_market_plan_from_arte(**args) is first
    assert len(generations) == 1
    selected[0] = "selected-2"
    assert market.certified_market_plan_from_arte(**args) is not first
    assert len(generations) == 2


@pytest.mark.parametrize("refreshed_token,changes_plan", [
    ("same", False), ("different", True),
])
def test_cold_plan_rechecks_pinned_build_during_unrelated_part_growth(
        monkeypatch, refreshed_token, changes_plan):
    from src.backend import backtest_market_data as market
    from src.trading_runtime import arte_market_day_cold_preflight as cold
    from src.trading_runtime import arte_market_day_keeper as keeper_module
    from src.backend import backtest_market_keeper_pool as keeper_pool
    from research.mlops import clickhouse

    class Reader:
        def close(self):
            pass

    class Session:
        client = object()
        writable = True
        def close(self):
            pass

    monkeypatch.setattr(clickhouse, "ClickHouseHttpClient", Reader)
    monkeypatch.setattr(market, "readonly_clickhouse_client", lambda **_: Reader())
    monkeypatch.setattr(cold, "market_day_fence_build_ids", lambda *_: ("a" * 64,))
    monkeypatch.setattr(keeper_pool, "open_workstation_keeper_session", Session)
    monkeypatch.setattr(keeper_pool, "MARKET_CERTIFICATE_KEEPER_POOL",
                        keeper_pool.MarketCertificateKeeperPool())
    monkeypatch.setattr(keeper_module, "MarketDayKeeperReader",
                        lambda _: SimpleNamespace(load=lambda _build: "proof"))
    fingerprints = iter(("before", "after"))
    monkeypatch.setattr(subject, "market_inventory_fingerprint",
                        lambda _reader: next(fingerprints))
    scoped = iter(("selected-before", "selected-after"))
    monkeypatch.setattr(subject, "selected_market_inventory_fingerprint",
                        lambda *_args: next(scoped))
    scans = []
    def discover(*_args, **kwargs):
        assert kwargs["expected_build_ids"] == ("a" * 64,)
        result = SimpleNamespace(token="same" if not scans else refreshed_token)
        scans.append(result)
        return result
    monkeypatch.setattr(cold, "discover_cold_certified_market_day_plan", discover)
    cache = subject.MarketPlanCache()
    monkeypatch.setattr(subject, "MARKET_PLAN_CACHE", cache)
    args = dict(sessions=("2026-08-18",), tickers=("ABCD",),
                configuration={"strategy": {"execution_interval": "100ms"}})
    if changes_plan:
        with pytest.raises(RuntimeError, match="plan changed"):
            market.certified_market_plan_from_arte(**args)
    else:
        assert market.certified_market_plan_from_arte(**args) is scans[-1]
    assert len(scans) == 2
    assert not cache._entries


def test_unrelated_part_growth_reuses_full_audit_with_selected_fence(monkeypatch):
    from src.backend import backtest_market_data as market
    from src.trading_runtime import arte_market_day_cold_preflight as cold
    from src.trading_runtime import arte_market_day_keeper as keeper_module
    from src.backend import backtest_market_keeper_pool as keeper_pool
    from research.mlops import clickhouse

    class Reader:
        def close(self):
            pass

    class Session:
        client = object()
        writable = True
        def close(self):
            pass

    monkeypatch.setattr(clickhouse, "ClickHouseHttpClient", Reader)
    monkeypatch.setattr(market, "readonly_clickhouse_client", lambda **_: Reader())
    monkeypatch.setattr(cold, "market_day_fence_build_ids", lambda *_: ("a" * 64,))
    monkeypatch.setattr(keeper_pool, "open_workstation_keeper_session", Session)
    monkeypatch.setattr(keeper_pool, "MARKET_CERTIFICATE_KEEPER_POOL",
                        keeper_pool.MarketCertificateKeeperPool())
    monkeypatch.setattr(keeper_module, "MarketDayKeeperReader",
                        lambda _: SimpleNamespace(load=lambda _build: "proof"))
    scans = []
    monkeypatch.setattr(cold, "discover_cold_certified_market_day_plan",
                        lambda *_args, **_kwargs: scans.append(
                            SimpleNamespace(token="same")) or scans[-1])
    inventory = iter(("before", "after", "later"))
    monkeypatch.setattr(subject, "market_inventory_fingerprint",
                        lambda _reader: next(inventory))
    monkeypatch.setattr(subject, "selected_market_inventory_fingerprint",
                        lambda *_args: "selected")
    cache = subject.MarketPlanCache()
    monkeypatch.setattr(subject, "MARKET_PLAN_CACHE", cache)
    plan = market.certified_market_plan_from_arte(
        sessions=("2026-08-18",), tickers=("ABCD",),
        configuration={"strategy": {"execution_interval": "100ms"}})
    assert plan is scans[0] and len(scans) == 1
    assert len(cache._entries) == 1
    assert market.certified_market_plan_from_arte(
        sessions=("2026-08-18",), tickers=("ABCD",),
        configuration={"strategy": {"execution_interval": "100ms"}}) is plan
    assert len(scans) == 1


def test_selected_part_change_during_cache_recheck_forces_full_audit(monkeypatch):
    from src.backend import backtest_market_data as market
    from src.trading_runtime import arte_market_day_cold_preflight as cold
    from src.trading_runtime import arte_market_day_keeper as keeper_module
    from src.backend import backtest_market_keeper_pool as keeper_pool
    from research.mlops import clickhouse

    class Reader:
        def close(self):
            pass

    class Session:
        client = object()
        writable = True
        def close(self):
            pass

    monkeypatch.setattr(clickhouse, "ClickHouseHttpClient", Reader)
    monkeypatch.setattr(market, "readonly_clickhouse_client", lambda **_: Reader())
    monkeypatch.setattr(cold, "market_day_fence_build_ids", lambda *_: ("a" * 64,))
    monkeypatch.setattr(keeper_pool, "open_workstation_keeper_session", Session)
    monkeypatch.setattr(keeper_pool, "MARKET_CERTIFICATE_KEEPER_POOL",
                        keeper_pool.MarketCertificateKeeperPool())
    monkeypatch.setattr(keeper_module, "MarketDayKeeperReader",
                        lambda _: SimpleNamespace(load=lambda _build: "proof"))
    scans = []
    monkeypatch.setattr(cold, "discover_cold_certified_market_day_plan",
                        lambda *_args, **_kwargs: scans.append(
                            SimpleNamespace(token="same")) or scans[-1])
    global_part = ["part-1"]
    monkeypatch.setattr(subject, "market_inventory_fingerprint",
                        lambda _reader: global_part[0])
    scoped = iter(("selected-1", "selected-1", "selected-2", "selected-2"))
    monkeypatch.setattr(subject, "selected_market_inventory_fingerprint",
                        lambda *_args: next(scoped))
    monkeypatch.setattr(subject, "MARKET_PLAN_CACHE", subject.MarketPlanCache())
    args = dict(sessions=("2026-08-18",), tickers=("ABCD",),
                configuration={"strategy": {"execution_interval": "100ms"}})
    first = market.certified_market_plan_from_arte(**args)
    global_part[0] = "part-2"
    assert market.certified_market_plan_from_arte(**args) is not first
    assert len(scans) == 2  # selected change requires a fresh cold audit
