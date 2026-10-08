import polars as pl
import pytest
from polars.testing import assert_frame_equal
from research.causal_strategy_features.v3.native_bars import native_channels


def source():
    return pl.DataFrame(dict(build_id=['build']*10,session_date=['2026-08-04']*10,
        ticker=['X']*10,attempt_id=['source']*10,resolution_ms=[60000]*10,
        bucket_index=list(range(10)),open_int=list(range(100,110)),
        high_int=list(range(102,112)),low_int=list(range(98,108)),close_int=list(range(101,111)),
        execution_volume=[10.]*10,execution_notional=[1000.]*10,trade_count=[5]*10,
        price_valid=[1]*10,extremes_valid=[1]*10))


def project(frame,boundary=600000):
    return native_channels(frame,resolutions_ms=(60000,),through_day_boundary_ms=boundary,lookback_bars=3)


def test_native_completion_prefix_and_future_mutation():
    raw=source();expected=project(raw.filter(pl.col('bucket_index')<5),300000)
    changed=raw.with_columns(*[pl.when(pl.col('bucket_index')>=5).then(pl.col(c)*10)
        .otherwise(pl.col(c)).alias(c) for c in ('open_int','high_int','low_int','close_int','execution_volume')])
    assert_frame_equal(expected,project(changed,300000))


def test_source_attempts_do_not_share_baselines():
    raw=source().with_columns(pl.when(pl.col('bucket_index')>=5).then(pl.lit('new'))
                             .otherwise(pl.col('attempt_id')).alias('attempt_id'))
    assert project(raw).filter(pl.col('bucket_index')==5)['close_return'][0] is None


def test_invalid_candle_preserved_as_missing_and_no_gap_fill():
    raw=source().filter(pl.col('bucket_index')!=3).with_columns(
        pl.when(pl.col('bucket_index')==6).then(0).otherwise(pl.col('price_valid')).alias('price_valid'))
    result=project(raw)
    assert result.height==9
    assert result.filter(pl.col('bucket_index')==4)['close_return'][0] is None
    assert result.filter(pl.col('bucket_index')==6)['close_return'][0] is None


def test_unpublished_resolution_fails_closed():
    with pytest.raises(ValueError,match='producer resolutions'):
        native_channels(source(),resolutions_ms=(900000,),through_day_boundary_ms=600000)
