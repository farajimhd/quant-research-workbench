from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit_request import inputs
from src.backend.backtest_native_channel_position_index import (
    prepare_native_channel_position_index, prepare_native_channel_acquisition_index,
)


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


def test_acquisition_grid_preserves_source_and_matches_causal_single_boundary(installed):
    prepared,_,decisions,_,_,acquired=inputs(installed)
    before=len(installed[0].sql)
    at=decisions['decision_day_ms'].item()
    prepared=replace(prepared,policy=replace(prepared.policy,
        max_decisions=at//prepared.policy.inputs.decision_interval_ms+1))
    index=prepare_native_channel_acquisition_index(prepared,acquisition=acquired,
        through_day_boundary_ms=at)
    single=prepare_native_channel_position_index(prepared,decisions,acquisition=acquired)
    assert index.prepared is prepared
    assert index.candidate_at(at,acquisition=acquired,prepared=prepared)==single.candidate_at(
        at,acquisition=acquired,prepared=prepared)
    boundaries=np.frombuffer(index.boundaries_bytes,dtype=np.int64)
    assert boundaries[0]==0 and boundaries[-1]==at
    assert np.all(np.diff(boundaries)==prepared.policy.inputs.decision_interval_ms)
    assert len(installed[0].sql)==before
    from datetime import timedelta
    shifted=replace(acquired,acquisition_day_us=1,
        first_execution_time=acquired.first_execution_time+timedelta(microseconds=1))
    shifted_index=prepare_native_channel_acquisition_index(prepared,acquisition=shifted,
        through_day_boundary_ms=at)
    assert np.frombuffer(shifted_index.boundaries_bytes,dtype=np.int64)[0]==(
        prepared.policy.inputs.decision_interval_ms)
    with pytest.raises(ValueError):
        prepare_native_channel_acquisition_index(prepared,acquisition=shifted,
            through_day_boundary_ms=0)
    with pytest.raises(ValueError):
        prepare_native_channel_acquisition_index(prepared,acquisition=acquired,
            through_day_boundary_ms=prepared.packet.request.through_day_boundary_ms+1)
    with pytest.raises(ValueError):
        prepare_native_channel_acquisition_index(replace(prepared,
            policy=replace(prepared.policy,max_decisions=1)),acquisition=acquired,
            through_day_boundary_ms=at)


@pytest.mark.parametrize('defect',['ticker','date','duplicate','before_fill'])
def test_index_rejects_foreign_or_noncausal_position_schedule(installed,defect):
    prepared,_,decisions,_,_,acquired=inputs(installed)
    if defect=='ticker': decisions=decisions.with_columns(pl.lit('foreign').alias('ticker'))
    elif defect=='date': decisions=decisions.with_columns(pl.lit('2026-08-05').alias('session_date'))
    elif defect=='duplicate': decisions=pl.concat([decisions,decisions])
    else: decisions=decisions.with_columns(pl.lit(-100,dtype=pl.Int64).alias('decision_day_ms'))
    with pytest.raises(ValueError): prepare_native_channel_position_index(prepared,decisions,acquisition=acquired)
