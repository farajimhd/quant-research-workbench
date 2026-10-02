"""Native Arrow streaming, overflow/duplicate rejection and one-query cache load."""
import pyarrow as pa
import polars as pl
import pytest

from src.backend.backtest_strategy_liquidity_fade_loader import load_compiled_liquidity_fade_lookup
from test_liquidity_fade_columnar_lookup import fixture


class Stream:
    def __init__(self, batches): self.batches, self.closed = iter(batches), False
    def __iter__(self): return self.batches
    def close(self): self.closed = True


class Reader:
    def __init__(self, batches): self.stream, self.queries = Stream(batches), []
    def iter_arrow_record_batches(self, query):
        self.queries.append(query)
        return self.stream


def native_batches():
    witness, frame, args = fixture()
    table = frame.with_columns(pl.col('session_date').str.to_date()).to_arrow()
    return witness, args, table.to_batches(max_chunksize=2)


def test_one_pinned_select_loads_native_dates_uint_counts_and_closes_stream():
    witness, args, batches = native_batches()
    reader = Reader(batches)
    cache = load_compiled_liquidity_fade_lookup(reader, **args)
    assert cache.window_at('PLUG', witness.boundary_ms).candles == witness.candles
    assert reader.stream.closed and len(reader.queries) == 1
    query = reader.queries[0]
    assert 'FROM arte.bars_v1' in query and 'resolution_ms=5000' in query and 'LIMIT 2000001' in query
    assert args['plan'].units[0].attempt_id in query and 'file(' not in query
    assert 'toString(attempt_id) AS attempt_id' not in query
    assert 'max_block_size' not in query


def test_overflow_or_changed_schema_rejects_instead_of_returning_partial_cache():
    _, args, batches = native_batches()
    reader = Reader(batches)
    with pytest.raises(ValueError, match='incomplete load rejected'):
        load_compiled_liquidity_fade_lookup(reader, **args, max_rows=3)
    assert reader.stream.closed
    changed = Reader((pa.RecordBatch.from_arrays([pa.array([1])], ['unexpected']),))
    with pytest.raises(ValueError, match='changed shape'):
        load_compiled_liquidity_fade_lookup(changed, **args)
    assert changed.stream.closed


def test_duplicate_native_rows_or_foreign_units_do_not_get_dropped():
    _, args, batches = native_batches()
    reader = Reader((*batches, batches[0]))
    with pytest.raises(ValueError, match='repeats'):
        load_compiled_liquidity_fade_lookup(reader, **args)
    assert reader.stream.closed
    from dataclasses import replace
    reader = Reader(batches)
    bad = replace(args['plan'], units=args['plan'].units + args['plan'].units[:1])
    with pytest.raises(ValueError, match='duplicate'):
        load_compiled_liquidity_fade_lookup(reader, **dict(args, plan=bad))
    assert not reader.queries


def test_empty_native_stream_has_no_imputed_zero_activity():
    witness, args, _ = native_batches()
    reader = Reader(())
    assert load_compiled_liquidity_fade_lookup(reader, **args).window_at('PLUG', witness.boundary_ms) is None
    assert reader.stream.closed


def test_candidate_scope_preserves_plan_identity_and_complete_held_window():
    from dataclasses import replace
    witness, args, batches = native_batches()
    plan = args['plan']
    larger = replace(plan, tickers=plan.tickers + ('UNUSED',),
        units=plan.units + tuple(replace(unit, ticker='UNUSED') for unit in plan.units))
    reader = Reader(batches)
    oldest_start = witness.candles[0].boundary_ms - 5000
    cache = load_compiled_liquidity_fade_lookup(reader, plan=larger,
        session_date=args['session_date'], tickers=('PLUG',),
        after_boundary_ms=oldest_start, through_boundary_ms=witness.boundary_ms)
    assert cache.market_plan_token == larger.token
    assert cache.window_at('PLUG', witness.boundary_ms).candles == witness.candles
    assert 'UNUSED' not in reader.queries[0]
    assert 'LIMIT 2000001' in reader.queries[0]
    assert cache.window_at('UNUSED', witness.boundary_ms) is None


@pytest.mark.parametrize('scope', [
    {'tickers': ['PLUG']}, {'tickers': ('FOREIGN',)}, {'tickers': ('PLUG', 'PLUG')},
    {'after_boundary_ms': True}, {'after_boundary_ms': -100},
    {'after_boundary_ms': 1}, {'through_boundary_ms': 57_600_100},
    {'after_boundary_ms': 100, 'through_boundary_ms': 100},
])
def test_invalid_scope_rejects_before_network_access(scope):
    _, args, batches = native_batches()
    reader = Reader(batches)
    with pytest.raises(ValueError, match='scope differs'):
        load_compiled_liquidity_fade_lookup(reader, **args, **scope)
    assert not reader.queries


@pytest.mark.parametrize('scope', [
    {'tickers': ()}, {'after_boundary_ms': 100, 'through_boundary_ms': 4900},
])
def test_no_possible_whole_candle_or_entry_needs_no_query(scope):
    witness, args, batches = native_batches()
    reader = Reader(batches)
    cache = load_compiled_liquidity_fade_lookup(reader, **args, **scope)
    assert cache.market_plan_token == args['plan'].token
    assert cache.window_at('PLUG', witness.boundary_ms) is None
    assert not reader.queries


@pytest.mark.parametrize('clock', ['partial_first_bar', 'future_last_bar'])
def test_scope_leak_rejects_without_filtering_native_rows(clock):
    witness, args, batches = native_batches()
    reader = Reader(batches)
    kwargs = ({'after_boundary_ms': witness.candles[0].boundary_ms-4900}
              if clock == 'partial_first_bar' else
              {'through_boundary_ms': witness.completed_five_second_boundary_ms-100})
    with pytest.raises(ValueError, match='outside its requested scope'):
        load_compiled_liquidity_fade_lookup(reader, **args, **kwargs)
    assert reader.stream.closed
