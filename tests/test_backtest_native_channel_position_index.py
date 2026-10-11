from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit_request import inputs
from src.backend.backtest_native_channel_position_index import prepare_native_channel_position_index


def test_index_matches_vectorized_screening_without_new_source_reads(installed):
    prepared,_,decisions,_,_,acquired=inputs(installed)
    before=len(installed[0].sql)
    index=prepare_native_channel_position_index(prepared,decisions,acquisition=acquired)
    expected=prepared.qualify(decisions,np.ones(decisions.height,dtype=bool),
        np.full(decisions.height,acquired.acquisition_day_us,dtype=np.int64))
    assert index.candidate_at(decisions['decision_day_ms'].item(),acquisition=acquired,prepared=prepared)==bool(expected[0])
    assert index.candidate_at(decisions['decision_day_ms'].item(),
        acquisition=replace(acquired,execution_ids=(*acquired.execution_ids,'additional-fill')),prepared=prepared)==bool(expected[0])
    assert len(installed[0].sql)==before
    assert not np.frombuffer(index.qualified_bytes,dtype=bool).flags.writeable
    with pytest.raises(ValueError): index.candidate_at(decisions['decision_day_ms'].item()+1,acquisition=acquired,prepared=prepared)
    with pytest.raises(ValueError): index.candidate_at(decisions['decision_day_ms'].item(),
        acquisition=replace(acquired,source_entry_intent_id='new-entry'),prepared=prepared)
    with pytest.raises(ValueError): replace(index,qualified_bytes=b'')
    with pytest.raises(ValueError): replace(index,qualified_bytes=b'\x02')
    with pytest.raises(ValueError): index.candidate_at(decisions['decision_day_ms'].item(),
        acquisition=acquired,prepared=replace(prepared,policy=replace(prepared.policy,threshold=0.01)))


def test_later_schedule_rows_do_not_change_an_earlier_feature_candidate(installed):
    prepared,_,decisions,_,_,acquired=inputs(installed)
    from tests.test_backtest_native_channel_exit import setup
    _,_,all_decisions,_=setup(installed)
    schedule=all_decisions.filter(pl.col('ticker')==acquired.ticker)
    assert schedule.filter(pl.col('decision_day_ms')>decisions['decision_day_ms'].item()).height>0
    full=prepare_native_channel_position_index(prepared,schedule.reverse(),acquisition=acquired)
    first=prepare_native_channel_position_index(prepared,decisions,acquisition=acquired)
    at=decisions['decision_day_ms'].item()
    assert full.candidate_at(at,acquisition=acquired,prepared=prepared)==first.candidate_at(at,acquisition=acquired,prepared=prepared)


@pytest.mark.parametrize('defect',['ticker','date','duplicate','before_fill'])
def test_index_rejects_foreign_or_noncausal_position_schedule(installed,defect):
    prepared,_,decisions,_,_,acquired=inputs(installed)
    if defect=='ticker': decisions=decisions.with_columns(pl.lit('foreign').alias('ticker'))
    elif defect=='date': decisions=decisions.with_columns(pl.lit('2026-08-05').alias('session_date'))
    elif defect=='duplicate': decisions=pl.concat([decisions,decisions])
    else: decisions=decisions.with_columns(pl.lit(-100,dtype=pl.Int64).alias('decision_day_ms'))
    with pytest.raises(ValueError): prepare_native_channel_position_index(prepared,decisions,acquisition=acquired)
