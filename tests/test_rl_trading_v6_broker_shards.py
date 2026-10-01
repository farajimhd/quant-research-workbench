import numpy as np
import polars as pl
import pytest
import torch
from research.rl_trading.v6.broker_shards import SCHEMA,validate_rows,materialize
from research.rl_trading.v6.luld import LuldBook
from test_rl_trading_v6_tensor_broker import DEVICES


def rows():
    return pl.DataFrame([('A',10,100.,1000.,1,110000,90000,1,1_000_000,99000,101000,1)],schema=SCHEMA,orient='row')


@pytest.mark.parametrize('device',DEVICES)
def test_sparse_broker_materialization_clock_validity_and_luld(device):
    frame=rows();validate_rows(frame,10,11,{'A','B'})
    changes=pl.DataFrame({'available_us':[1_200_000],'lower':[9.],
        'upper':[11.],'paused':[True],'pause_start_us':[1_200_000]})
    book=LuldBook({'A':changes},end_us=2_000_000)
    result=list(materialize(frame,('A','B'),0,1_000_000,1_200_000,device=device,luld=book))
    assert [b.clock_us for b in result]==[1_100_000,1_200_000]
    assert float(result[0].vwap[0])==10 and float(result[0].spread[0])==.2
    assert result[0].valid.tolist()==[True,False]
    assert result[1].paused.tolist()==[True,False]
    assert not result[1].valid.any()
    with pytest.raises(MemoryError):
        list(materialize(frame,('A','B'),0,1_000_000,1_200_000,device=device,luld=book,max_device_bytes=1))


def test_broker_source_rejects_duplicate_or_malformed_evidence():
    with pytest.raises(ValueError):validate_rows(pl.concat([rows(),rows()]),10,11,{'A'})
    with pytest.raises(ValueError):validate_rows(rows().with_columns(pl.lit(-1.).alias('execution_volume')),10,11,{'A'})
    with pytest.raises(ValueError):validate_rows(rows().with_columns(pl.lit(2).alias('quote_valid')),10,11,{'A'})
