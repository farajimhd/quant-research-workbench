from datetime import date, datetime, timedelta
import asyncio
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.backend.fixed_v7_stream import FixedV7Cache, FixedV7Stream
from src.backend.backtest_market_data import CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit
from src.backend.structural_v7_seed import CertifiedSeedPlan
from src.backend.structural_v7_seed import load_seed
from src.backend.replay_run_service import ReplayRunController, RunMode
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION


NY = ZoneInfo("America/New_York")


def seed():
    value = dict(ticker="TEST", session="2026-08-17",
        available_at=datetime(2026, 8, 17, 20, tzinfo=NY).timestamp(),
        source_extraction_version=EXTRACTION_VERSION,
        band_config={**CONFIG, "coverage": .8}, levels=[],
        input_policy="legacy-unfiltered")
    value["checkpoint_hash"] = digest(value)
    return value


def test_completed_second_advances_causally_from_empty_seed():
    stream = FixedV7Stream(seed(), ticker="TEST", session=date(2026, 8, 18))
    before = datetime(2026, 8, 18, 4, 5, tzinfo=NY)
    assert stream.context(as_of=before)["qmd_structure_unified_levels"] == []
    assert stream.context(as_of=before, price=10.0)["qmd_structure_support_price"] is None
    at = datetime(2026, 8, 18, 4, 5, 1, tzinfo=NY)
    row = dict(resolution_ms=1000, price_valid=1, extremes_valid=1,
               open_int=100000, high_int=100100, low_int=99900,
               close_int=100050, volume=100)
    stream.update_second(row, at=at)
    assert stream.context(as_of=at)["v7_seed_input_policy"] == "exclude-trades-before-0405-et-v1"
    assert stream.engine.bars_processed == 1
    with pytest.raises(ValueError, match="rewind"):
        stream.context(as_of=before)
    with pytest.raises(ValueError, match="strictly increasing"):
        stream.update_second(row, at=at)


def test_private_typed_seed_transfers_observation_ownership_without_default_mutation():
    from tests.test_structural_v7_seed import Client

    shared = load_seed(Client(), ticker="TEST", session=date(2026, 8, 18))
    copied = FixedV7Stream(shared, ticker="TEST", session=date(2026, 8, 18))
    assert copied.engine.rows[0]["observations"] is not shared["levels"][0]["observations"]

    private = load_seed(Client(), ticker="TEST", session=date(2026, 8, 18))
    consumed = FixedV7Stream(private, ticker="TEST", session=date(2026, 8, 18),
                             consume_seed=True)
    assert consumed.engine.rows[0]["observations"] is private["levels"][0]["observations"]


def test_strategy_one_reuses_validated_geometry_without_rich_context(monkeypatch):
    from tests.test_structural_v7_seed import Client
    from src.trading_runtime import strategy_one_v7

    prior = load_seed(Client(), ticker="TEST", session=date(2026, 8, 18))
    stream = FixedV7Stream(prior, ticker="TEST", session=date(2026, 8, 18))
    at = datetime(2026, 8, 18, 4, 0, tzinfo=NY)
    calls = []
    original = strategy_one_v7.admitted_v7_levels
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(strategy_one_v7, "admitted_v7_levels", counted)
    first = stream.strategy_one_levels(as_of=at,
                                       seed_policy=prior["input_policy"])
    second = stream.strategy_one_levels(as_of=at,
                                        seed_policy=prior["input_policy"])
    assert len(first) > 0
    assert second is first
    assert len(calls) == 1
    later = at + timedelta(seconds=2)
    assert stream.strategy_one_levels(as_of=later,
                                      seed_policy=prior["input_policy"]) == ()
    stream.engine.as_of = later.timestamp()
    assert stream.strategy_one_levels(as_of=later,
                                      seed_policy=prior["input_policy"]) is first
    assert len(calls) == 1


def test_lazy_v7_cache_replays_only_completed_pinned_seconds(monkeypatch):
    coverage = dict(ticker="TEST", session_date="2026-08-17",
                    available_at="2026-08-18 00:00:00.000000000", state="empty",
                    level_count=0, observation_count=0, input_policy="",
                    source_extraction_version="", band_config_hash="0" * 64,
                    source_checkpoint_hash="", source_plan_hash="b" * 64)
    bar = dict(ticker="TEST", resolution_ms=1000, bucket_index=14700,
               price_valid=1, extremes_valid=1, open_int=100000,
               high_int=100100, low_int=99900, close_int=100050, volume=100)
    next_bar = {**bar, "bucket_index": 14701, "close_int": 100100}

    class Client:
        def __init__(self):
            self.queries = []

        def execute(self, sql):
            self.queries.append(sql)
            if "structural_level_coverage_v7" in sql:
                rows = [coverage]
            elif "structural_levels_v7" in sql or "structural_level_observations_v7" in sql:
                rows = []
            elif "market_stock_split_v1" in sql:
                rows = []
            elif "arte.bars_v1" in sql:
                rows = ([next_bar] if "bucket_index>=14701" in sql
                        and "bucket_index<14702" in sql else
                        [bar] if "bucket_index<14701" in sql else [])
            else:
                raise AssertionError(sql)
            return "\n".join(json.dumps(row) for row in rows)

        def iter_json_each_row(self, sql):
            for line in self.execute(sql).splitlines():
                if line.strip():
                    yield json.loads(line)

    market = CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "market", "definition",
        ("2026-08-18",), ("TEST",),
        (MarketDayUnit("market", "2026-08-18", "TEST", "bars",
                       "00000000-0000-0000-0000-000000000001", "source", 1, "hash"),),
        (100, 1000), "market-token")
    v7 = CertifiedSeedPlan("market", "b" * 64,
                           ({**coverage, "backtest_session": "2026-08-18"},),
                           "v7-token", True)
    client = Client()
    cache = FixedV7Cache(market_plan=market, seed_plan=v7,
                         session=date(2026, 8, 18), client=client)
    assert not cache.has_stream("TEST")
    before = datetime(2026, 8, 18, 4, 5, 0, 100000, tzinfo=NY)
    from src.backend import experimental_structure_book
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Strategy 1 must skip rich level context")
    with monkeypatch.context() as patcher:
        patcher.setattr(experimental_structure_book, "context", forbidden)
        assert cache.strategy_one_levels("TEST", as_of=before) == ()
    assert cache.context("TEST", as_of=before, price=10.0)["qmd_structure_unified_levels"] == []
    assert cache.has_stream("TEST")
    assert cache._streams["TEST"].engine.bars_processed == 0
    completed = datetime(2026, 8, 18, 4, 5, 1, tzinfo=NY)
    cache.advance_seconds([bar], at=completed)
    assert cache.context("TEST", as_of=completed, price=10.0)["qmd_structure_session_high"] == 10.01
    assert cache._streams["TEST"].engine.bars_processed == 1
    assert all(sql.startswith("SELECT") for sql in client.queries)
    later = datetime(2026, 8, 18, 4, 5, 2, tzinfo=NY)
    cache.strategy_one_levels("TEST", as_of=later)
    assert cache._streams["TEST"].engine.bars_processed == 2
    assert any("bucket_index>=14701" in sql and "bucket_index<14702" in sql
               for sql in client.queries)
    count = len(client.queries)
    cache.strategy_one_levels("TEST", as_of=later + timedelta(milliseconds=100))
    assert len(client.queries) == count

    late = FixedV7Cache(market_plan=market, seed_plan=v7,
                        session=date(2026, 8, 18), client=Client())
    late.context("TEST", as_of=completed, price=10.0)
    assert late._streams["TEST"].engine.bars_processed == 1


def test_fixed_controller_projection_never_falls_back_to_disk_v7_cursor():
    controller = object.__new__(ReplayRunController)
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST,
                                            causal_v7_plan={"token": "pinned"})
    at = datetime(2026, 8, 18, 4, 5, tzinfo=NY)

    async def inspect():
        controller._fixed_v7_caches = {}
        with pytest.raises(RuntimeError, match="cannot fall back"):
            await controller._fixed_or_experimental_structure_snapshot("TEST", at, price=10)
        controller._fixed_v7_caches = {"2026-08-18": SimpleNamespace(context=lambda *a, **k: {
            "qmd_structure_unified_levels": [], "qmd_structure_session_high": 10.0,
            "v7_max_input_timestamp": at.timestamp(), "qmd_level_book_version": "v7",
            "v7_input_policy": "filtered", "v7_seed_input_policy": "legacy-unfiltered",
        })}
        snapshot = await controller._fixed_or_experimental_structure_snapshot("TEST", at, price=10)
        assert snapshot["unified_levels"] == []
        assert snapshot["max_input_timestamp"] == at.timestamp()

    asyncio.run(inspect())
