import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType
from threading import Lock, Barrier
from time import sleep

import polars as pl
import pytest

from src.backend.qmd_columnar_history import CertifiedHistoryProduct
from src.backend.qmd_columnar_population import CertifiedPopulationPin, read_population_window
from src.backend.watchlist_runtime_service import WatchlistRuntime
from src.market_engine.volume_rank_rules import VolumeUniverseRule
from src.market_engine import columnar_history_product as c
from test_columnar_history_publication import Store, publish, consume, prepared_source
from test_qmd_columnar_history_transport import response_for


@pytest.fixture
def population(prepared_source):
    source = replace(prepared_source, fields=())
    store = Store()
    publish(store, source)
    first = consume(store, source)
    prepared = replace(first.prepared, scope=replace(first.prepared.scope, listing_id="10", ticker="ZZZ"))
    header = c.history_seal(prepared, first.parents, certified_at=first.header["certified_at"])
    second = CertifiedHistoryProduct(prepared, first.parents, MappingProxyType(header))
    references = {listing: MappingProxyType(dict(listing_id=listing, ticker=ticker,
        adjusted_prior_close=10., available_at=datetime(2026, 8, 3, 8, tzinfo=UTC), status="resolved"))
        for listing, ticker in (("listing", "ABC"), ("10", "ZZZ"))}
    opening = replace(source.opening_reference,
        scope=replace(source.opening_reference.scope, tickers=("ABC", "ZZZ")),
        rows_by_listing=MappingProxyType(references))
    pin = CertifiedPopulationPin(opening, {"listing": first.pin(), "10": second.pin()},
        VolumeUniverseRule(30, 1, .8, 50.))
    return pin, {"ABC": first, "ZZZ": second}


def test_complete_population_transport_is_bounded_concurrent_and_exact(population):
    pin, certificates = population
    active = peak = calls = 0
    lock = Lock()
    def post(path, request, timeout):
        nonlocal active, peak, calls
        with lock:
            active += 1
            calls += 1
            peak = max(peak, active)
        try:
            sleep(.03)
            assert request["field_ids"] == []
            return response_for(certificates[request["scope"]["ticker"]],
                request["first_ordinal"], request["stop_ordinal"])
        finally:
            with lock:
                active -= 1
    result = asyncio.run(read_population_window(pin, first_bucket=34200, stop_bucket=34205,
        as_of=datetime(2026, 8, 3, 13, 30, 5, tzinfo=UTC), concurrency=2, history_post=post))
    assert calls == peak == 2 and active == 0
    assert result.rows.height == 10
    assert result.price_eligible_count == result.listing_count == 2
    assert result.ranked["ticker"].to_list() == ["ZZZ", "ZZZ"]
    assert result.source_revision == pin.source_revision
    # Exercise the actual shared app watchlist rank/transition/cache path.
    configuration = dict(market_discovery=dict(watchlists=[dict(watchlist_id="volume",
        enabled=True, ranking_field="market.rolling_share_volume", ranking_direction="descending",
        ranking_tie_field="identity.listing_id", ranking_tie_type="string", maximum_size=1,
        membership_expiry="on_re_evaluation", inclusion_rule_sets=["positive-volume"])],
        rule_sets=[dict(rule_set_id="positive-volume", enabled=True, operator="all",
            conditions=[dict(enabled=True, left_source_id="market.rolling_share_volume", comparator="greater_than", value=0)])]))
    runtime = WatchlistRuntime()
    clock = result.rows["boundary_at"].max()
    current = result.scanner_frame().filter(pl.col("boundary_at") == clock).to_dicts()
    snapshot = runtime.resolve(configuration, current, as_of=clock, publish_targets=False,
        data_fields_projected=True)
    assert snapshot["watchlists"][0]["members"][0]["ticker"] == "ZZZ"
    empty = [{**row, "rolling_share_volume": 0.} for row in current]
    snapshot = runtime.resolve(configuration, empty, as_of=clock, publish_targets=False,
        data_fields_projected=True)
    assert snapshot["watchlists"][0]["members"] == []


def test_population_missing_lineage_and_future_clocks_fail_before_io(population):
    pin, _ = population
    with pytest.raises(ValueError, match="complete price-eligible"):
        replace(pin, histories={"listing": pin.histories["listing"]})
    with pytest.raises(ValueError, match="lineage"):
        replace(pin, opening=replace(pin.opening, content_hash="f" * 64))
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid population must not open transport")
    with pytest.raises(ValueError, match="future completed"):
        asyncio.run(read_population_window(pin, first_bucket=34200, stop_bucket=34205,
            as_of=datetime(2026, 8, 3, 13, 30, 4, tzinfo=UTC), concurrency=2, history_post=forbidden))


def test_population_cache_reuses_full_origins_with_exact_window_rankings(population):
    from src.backend.backtest_program_population_cache import ProgramPopulationCache
    pin, certificates = population
    calls = []
    def post(path, request, timeout):
        calls.append((request['scope']['ticker'], request['first_ordinal'], request['stop_ordinal']))
        return response_for(certificates[request['scope']['ticker']],
            request['first_ordinal'], request['stop_ordinal'])
    async def run():
        cache = ProgramPopulationCache(maximum_retained_bytes=1000000)
        history = next(iter(pin.histories.values()))
        at = c.completed_clock(history.scope, history.scope.stop_bucket - 1)
        for first, stop in ((34200, 34202), (34202, 34205)):
            actual = await cache.load(pin, first_bucket=first, stop_bucket=stop,
                as_of=at, concurrency=2, history_post=post)
            assert len(calls) == 2
            expected = await read_population_window(pin, first_bucket=first, stop_bucket=stop,
                as_of=at, concurrency=2, history_post=lambda path, request, timeout: response_for(
                    certificates[request['scope']['ticker']], request['first_ordinal'], request['stop_ordinal']))
            assert actual.rows.equals(expected.rows) and actual.ranked.equals(expected.ranked)
            assert actual.source_revision == expected.source_revision
        assert cache.retained_bytes > 0
        with pytest.raises(ValueError, match='future completed'):
            await cache.load(pin, first_bucket=34202, stop_bucket=34205,
                as_of=datetime(2026, 8, 3, 13, 30, 4, tzinfo=UTC), concurrency=2, history_post=post)
        assert len(calls) == 2
    asyncio.run(run())


def test_population_cache_budget_failure_never_installs_partial_origins(population):
    from src.backend.backtest_program_population_cache import ProgramPopulationCache
    pin, certificates = population
    async def run():
        cache = ProgramPopulationCache(maximum_retained_bytes=1)
        history = next(iter(pin.histories.values()))
        with pytest.raises(ExceptionGroup):
            await cache.load(pin, first_bucket=34200, stop_bucket=34205,
                as_of=c.completed_clock(history.scope, history.scope.stop_bucket - 1), concurrency=2,
                history_post=lambda path, request, timeout: response_for(
                    certificates[request['scope']['ticker']], request['first_ordinal'], request['stop_ordinal']))
        assert cache._key is None and cache._frames is None and cache.retained_bytes == 0
    asyncio.run(run())


def test_population_failure_drains_concurrent_io_and_returns_no_partial_result(population):
    pin, certificates = population
    barrier = Barrier(2)
    finished = set()
    lock = Lock()
    def post(path, request, timeout):
        ticker = request["scope"]["ticker"]
        try:
            barrier.wait(timeout=2)
            if ticker == "ABC":
                raise ValueError("unavailable certified input")
            sleep(.03)
            return response_for(certificates[ticker], 0, 5)
        finally:
            with lock:
                finished.add(ticker)
    with pytest.raises(ExceptionGroup) as failure:
        asyncio.run(read_population_window(pin, first_bucket=34200, stop_bucket=34205,
            as_of=datetime(2026, 8, 3, 13, 30, 5, tzinfo=UTC), concurrency=2, history_post=post))
    assert any("unavailable certified" in str(error) for error in failure.value.exceptions)
    assert finished == {"ABC", "ZZZ"}


def test_population_counts_rejected_and_empty_references_without_silent_drop(population):
    pin, certificates = population
    references = dict(pin.opening.rows_by_listing)
    for listing, ticker, status, close in (("empty", "EMPTY", "covered_empty", None),
            ("excluded", "EXCLUDED", "excluded_identity", None),
            ("price", "PRICE", "resolved", 100.)):
        references[listing] = MappingProxyType(dict(listing_id=listing, ticker=ticker,
            status=status, adjusted_prior_close=close))
    opening = replace(pin.opening,
        scope=replace(pin.opening.scope, tickers=("ABC", "EMPTY", "EXCLUDED", "PRICE", "ZZZ")),
        rows_by_listing=MappingProxyType(references))
    pin = replace(pin, opening=opening)
    result = asyncio.run(read_population_window(pin, first_bucket=34200, stop_bucket=34205,
        as_of=datetime(2026, 8, 3, 13, 30, 5, tzinfo=UTC), concurrency=1,
        history_post=lambda path, request, timeout: response_for(certificates[request["scope"]["ticker"]], 0, 5)))
    assert (result.listing_count, result.price_eligible_count, result.covered_empty_reference_count,
            result.excluded_identity_count, result.price_rejected_count) == (5, 2, 1, 1, 1)


def test_current_configuration_admits_explicit_replace_and_identity_contract():
    from src.backend.trading_configuration_service import _default_draft, _validate_market_discovery
    discovery = _default_draft()["market_discovery"]
    watchlist = next(row for row in discovery["watchlists"] if row["watchlist_id"] == "core-candidates")
    # A new standalone scanner configuration, rather than legacy strategy rules.
    discovery["rule_sets"] = []
    discovery["watchlists"] = [watchlist]
    discovery["signal_streams"] = []
    discovery["column_catalog"] = [column for column in discovery["column_catalog"]
                                    if column.get("source_kind") != "rule_set"]
    columns = {column["column_id"] for column in discovery["column_catalog"]}
    for composition in (discovery["core_scan"], watchlist):
        composition["columns"] = [column for column in composition.get("columns", []) if column in columns]
    watchlist.update(ranking_field="market.rolling_share_volume", ranking_direction="descending",
        ranking_tie_field="identity.listing_id", ranking_tie_type="string",
        membership_expiry="on_re_evaluation", manual_inclusions=[],
        inclusion_rule_sets=[], exclusion_rule_sets=[])
    from src.backend.data_field_contracts import compile_data_field_plan, data_field_output_index
    index = data_field_output_index(discovery["data_fields"])
    watchlist["ranking_field_ref"] = index["market.rolling_share_volume"]["field_ref"]
    discovery["data_field_plan"] = compile_data_field_plan(discovery)
    _validate_market_discovery(discovery)
    watchlist["ranking_tie_type"] = "float"
    with pytest.raises(ValueError, match="identity tie contract"):
        _validate_market_discovery(discovery)


def test_certified_empty_population_returns_counts_without_io(population):
    pin, _ = population
    reference = MappingProxyType(dict(listing_id="empty", ticker="EMPTY",
        adjusted_prior_close=None, status="covered_empty"))
    opening = replace(pin.opening, scope=replace(pin.opening.scope, tickers=("EMPTY",)),
        rows_by_listing=MappingProxyType({"empty": reference}))
    pin = replace(pin, opening=opening, histories={})
    def forbidden(*args, **kwargs):
        raise AssertionError("A proven empty eligible population must not read a missing product")
    result = asyncio.run(read_population_window(pin, first_bucket=34200, stop_bucket=34205,
        as_of=datetime(2026, 8, 3, 13, 30, 5, tzinfo=UTC), concurrency=1, history_post=forbidden))
    assert result.rows.is_empty() and result.ranked.is_empty()
    assert result.covered_empty_reference_count == result.listing_count == 1
    assert result.price_eligible_count == result.price_rejected_count == 0
