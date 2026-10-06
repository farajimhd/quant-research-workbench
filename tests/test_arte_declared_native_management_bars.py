"""Physical typed Arrow source reads; own run/certificates remain fixtures."""
from dataclasses import replace
import re

import pyarrow as pa
import pytest

from test_arte_declared_native_managed_sources import managed
from test_arte_declared_native_fixed_sources import setup
from src.trading_runtime import arte_declared_native_management_bars as module


def install(x, mode='normal'):
    queries = []
    def arrow(sql):
        queries.append(sql)
        resolution = int(re.search(r'resolution_ms=(\d+)', sql).group(1))
        keys = [int(k) for k in re.search(r'bucket_index IN \(([0-9,]+)\)',sql).group(1).split(',')]
        bars = 'FROM arte.bars_v1' in sql
        names = (('bucket_index','resolution_ms','close_int','high_int','low_int','trade_count','price_valid','extremes_valid')
            if bars else ('bucket_index','resolution_ms','macd_line','macd_signal'))
        types = ((pa.uint32(),pa.uint32(),pa.uint64(),pa.uint64(),pa.uint64(),pa.uint64(),pa.uint8(),pa.uint8())
            if bars else (pa.uint32(),pa.uint32(),pa.float64(),pa.float64()))
        if mode == 'missing': keys = keys[1:]
        if mode == 'orphan' and bars: keys = keys[1:]
        if mode == 'duplicate': keys.append(keys[-1])
        if mode == 'foreign': keys.append(keys[-1]+1)
        if mode == 'no_indicator' and not bars: keys = []
        rows = []
        for key in keys:
            actual = resolution+100 if mode=='resolution' else resolution
            if bars:
                rows.append((key,actual,9900,10000,9800,0,2 if mode=='flag' else 1,1))
            else:
                rows.append((key,actual,float('inf') if mode=='infinite' else float('nan') if mode=='nan' else -.03,.02))
        arrays = [pa.array([row[i] for row in rows], dtype) for i,dtype in enumerate(types)]
        return iter([pa.RecordBatch.from_arrays(arrays,names=names)])
    x.client.iter_arrow_record_batches = arrow
    return queries


def read(x, boundary=31000):
    return module.read_declared_management_bars(x.managed_resolver,x.proposal.ticker,boundary)


def test_actual_completed_five_ten_and_four_sparse_candle_inputs(managed):
    x=managed;queries=install(x)
    value=read(x)
    assert tuple(row.boundary_ms for row in value.five_second)==(15000,20000,25000,30000)
    assert all(type(row) is module.DeclaredManagementBar and row.trade_count==0 for row in value.five_second)
    assert value.ten_second.boundary_ms==30000 and value.ten_second.resolution_ms==10000
    assert value.configuration_hash==x.managed_envelope['payload_hash']
    assert len(queries)==4
    assert all('attempt_id=toUUID(' in q and 'build_id=' in q and 'session_date=toDate(' in q for q in queries)
    assert module.compare_declared_management_bars(x.managed_resolver,value)==value


def test_sparse_missing_is_not_zero_and_unavailable_macd_is_not_invented(managed):
    install(managed,'missing');value=read(managed)
    assert value.five_second[0] is None and value.ten_second is None
    assert value.five_second[1].trade_count==0
    install(managed,'no_indicator');value=read(managed)
    assert all(row.macd_line is None and row.macd_signal is None for row in value.five_second)
    install(managed,'nan');value=read(managed)
    assert value.five_second[-1].macd_line is None


@pytest.mark.parametrize('mode',['duplicate','foreign','orphan','resolution','flag','infinite'])
def test_corrupt_producer_keys_flags_resolution_and_infinite_macd_fail(managed,mode):
    install(managed,mode)
    with pytest.raises(ValueError): read(managed)


@pytest.mark.parametrize('boundary',[True,31000.,0,31101,57600100])
def test_invalid_or_outside_fenced_clock_fails_before_market_reads(managed,boundary):
    queries=install(managed)
    with pytest.raises(ValueError): read(managed,boundary)
    assert queries==[]


def test_fresh_complete_config_and_ticker_fences_precede_reads(managed):
    queries=install(managed)
    with pytest.raises(ValueError):
        module.read_declared_management_bars(managed.managed_resolver,'FOREIGN',31000)
    assert queries==[]
    managed.context['configuration_hash']='f'*64
    with pytest.raises(ValueError): read(managed)
    assert queries==[]


def test_resealed_source_scalar_change_is_not_attested(managed):
    install(managed);value=read(managed)
    changed=replace(value,five_second=(*value.five_second[:-1],replace(value.five_second[-1],trade_count=1)))
    from hashlib import sha256
    changed=replace(changed,content_hash=sha256(module.canonical_json(changed.payload()).encode()).hexdigest())
    with pytest.raises(ValueError,match='independently reloaded'):
        module.compare_declared_management_bars(managed.managed_resolver,changed)


def test_serialized_shape_cannot_replace_exact_typed_source_bars(managed):
    from dataclasses import asdict
    install(managed);value=read(managed)
    changed=replace(value,five_second=tuple(asdict(row) for row in value.five_second))
    assert changed.payload()==value.payload()
    with pytest.raises(ValueError,match='independently reloaded'):
        module.compare_declared_management_bars(managed.managed_resolver,changed)
