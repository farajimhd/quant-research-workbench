from dataclasses import replace
from datetime import date

import numpy as np
import pyarrow as pa
import pytest

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_squeeze_ladder_loader import load_ladder_observations
from src.backend.fixed_bar_signal import canonical_stream_activation, load_first_squeeze_occurrences
from tests.test_backtest_strategy_one_loader import DAY, TICKER, plan
from tests.test_squeeze_ladder_columnar import columns


def fixture():
    market = plan()
    market = replace(market, units=tuple(replace(unit, output_rows=700) for unit in market.units))
    stream, activation = canonical_stream_activation()
    class Scanner:
        def iter_json_each_row(self, sql):
            assert 'arte.bars_v1' in sql and 'INSERT' not in sql
            return iter([dict(session_date=DAY, ticker=TICKER, bucket_index=144639,
                             open_int=100000, close_int=100100, previous_close_int=100000,
                             volume=200., previous_volume=100., trade_count=4, previous_trade_count=2)])
    scan = load_first_squeeze_occurrences(market, stream=stream, activation=activation,
                                        through_boundary_ms=70000, client=Scanner())
    args = columns()
    origin = int(market_day_boundary(date.fromisoformat(DAY), 0).timestamp()) * 1_000_000
    data = {key: value for key, value in args.items()
            if isinstance(value, np.ndarray) and len(value) == 700 and key != 'evaluation_epoch_us'}
    data['quote_timestamp_us'] = origin + args['boundary_ms'] * 1000
    data.update(session_date=pa.array([date.fromisoformat(DAY)] * 700, type=pa.date32()),
                ticker=[TICKER] * 700, resolution_ms=[100] * 700)
    table = pa.table(data)
    class Reader:
        def __init__(self, table):
            self.table, self.queries = table, []
        def iter_arrow_record_batches(self, sql):
            self.queries.append(sql)
            assert sql.rstrip().endswith('FORMAT ArrowStream')
            assert 'arte.liquidity_100ms_v1' in sql
            assert 'strategy_one_candidate_v1' not in sql
            yield from self.table.to_batches(max_chunksize=111)
    return market, scan, args['policy'], table, Reader


def test_native_full_source_reader_preserves_vwap_and_admission_without_higher_macds():
    market, scan, policy, table, Reader = fixture()
    reader = Reader(table)
    result, = load_ladder_observations(market, session_date=DAY, tickers=(TICKER,),
        through_boundary_ms=70000, certified_scan=scan, policy=policy, client=reader)
    assert len(reader.queries) == 1
    assert result.gate.vwap_cross_indices.tolist() == [649]
    assert result.gate.admission_boundary_ms[649] == 64000
    assert result.market_plan_token == market.token
    assert result.scan_content_hash == scan['authority']['content_hash']
    np.testing.assert_array_equal(result.completed_source['execution_vwap'].to_numpy(),
                                  table['execution_vwap'].to_numpy())
    assert 'macd_line' not in result.completed_source.column_names


@pytest.mark.parametrize('broken', ['scan', 'prefix', 'count', 'scope'])
def test_changed_source_and_signal_proof_fail_closed(broken):
    market, scan, policy, table, Reader = fixture()
    if broken == 'scan':
        scan['occurrences'][0]['ticker'] = 'OTHER'
    elif broken == 'prefix':
        table = table.set_column(table.schema.get_field_index('boundary_ms'), 'boundary_ms',
                                 pa.array([*range(100, 70000, 100), 70100]))
    elif broken == 'count':
        table = table.set_column(table.schema.get_field_index('volume_trade_count'), 'volume_trade_count',
                                 pa.array([None, *([1] * 699)], type=pa.int64()))
    else:
        table = table.set_column(table.schema.get_field_index('ticker'), 'ticker', pa.array(['OTHER'] * 700))
    with pytest.raises(ValueError):
        load_ladder_observations(market, session_date=DAY, tickers=(TICKER,),
            through_boundary_ms=70000, certified_scan=scan, policy=policy, client=Reader(table))
