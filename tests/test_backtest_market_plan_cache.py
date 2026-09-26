import json
from types import SimpleNamespace

import pytest

from src.backend import backtest_market_plan_cache as subject


class InventoryClient:
    def __init__(self):
        self.part_name = "part-1"
        self.disk_name = "live_market_ssd"

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


def test_market_plan_cache_requires_exact_keeper_and_inventory():
    cache = subject.MarketPlanCache()
    plan = object()
    cache.put(("scope",), {"build": "proof"}, "fingerprint", plan)
    assert cache.get(("scope",), {"build": "proof"}, "fingerprint") is plan
    assert cache.get(("scope",), {"build": "changed"}, "fingerprint") is None
    assert cache.get(("scope",), {"build": "proof"}, "changed") is None
    with pytest.raises(RuntimeError, match="attested"):
        cache.put(("scope",), {"build": None}, "fingerprint", plan)


def test_fixed_plan_reuses_only_unchanged_verified_snapshot(monkeypatch):
    from src.backend import backtest_market_data as market
    from src.trading_runtime import arte_market_day_cold_preflight as cold
    from src.trading_runtime import arte_market_day_keeper as keeper_module
    from src.trading_runtime import keeper_session as session_module
    from research.mlops import clickhouse

    class Reader:
        def close(self):
            pass

    class Session:
        client = object()
        def close(self):
            pass

    monkeypatch.setattr(clickhouse, "ClickHouseHttpClient", Reader)
    monkeypatch.setattr(market, "readonly_clickhouse_client", lambda **_: Reader())
    monkeypatch.setattr(cold, "market_day_fence_build_ids", lambda *_: ("a" * 64,))
    monkeypatch.setattr(session_module, "open_workstation_keeper_session", Session)
    monkeypatch.setattr(keeper_module, "MarketDayKeeperReader",
                        lambda _: SimpleNamespace(load=lambda _build: "proof"))
    generations = []
    monkeypatch.setattr(cold, "discover_cold_certified_market_day_plan",
                        lambda *_args, **_kwargs: generations.append(object()) or generations[-1])
    fingerprint = ["part-1"]
    monkeypatch.setattr(subject, "market_inventory_fingerprint",
                        lambda _reader: fingerprint[0])
    monkeypatch.setattr(subject, "MARKET_PLAN_CACHE", subject.MarketPlanCache())
    args = dict(sessions=("2026-08-18",), tickers=("ABCD",),
                configuration={"strategy": {"execution_interval": "100ms"}})
    first = market.certified_market_plan_from_arte(**args)
    assert market.certified_market_plan_from_arte(**args) is first
    assert len(generations) == 1
    fingerprint[0] = "part-2"
    assert market.certified_market_plan_from_arte(**args) is not first
    assert len(generations) == 2
