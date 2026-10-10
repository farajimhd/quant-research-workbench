from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType
from hashlib import sha256

import polars as pl
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_exit_lineage import case
from src.backend.backtest_native_channel_exit_projection import (
    NativeChannelExitCheckpointReference, project_native_channel_exit,
)
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


def context(installed):
    request, group, executions, kwargs = case(installed)
    # A serialization fixture, not a certified financial run: move its clock
    # into PM so the original entry's 04:00-origin boundary is causal.
    offset = 14_460_000
    acquired = replace(request.acquisition,
        acquisition_day_us=offset * 1000,
        first_execution_time=request.acquisition.first_execution_time + timedelta(milliseconds=offset))
    decision = dict(request.decision)
    for key in ('decision_day_ms', 'sequence_start_day_ms', 'sequence_end_day_ms'):
        decision[key] += offset
    decision['acquisition_day_us'] = acquired.acquisition_day_us
    decision['quote_day_us'] += offset * 1000
    support = pl.from_arrow(request.supporting_rows).with_columns(
        (pl.col('available_day_boundary_ms') + offset).alias('available_day_boundary_ms')).to_arrow()
    plan = request.source_plan
    source_request = replace(plan.request, through_day_boundary_ms=plan.request.through_day_boundary_ms + offset)
    token = sha256((source_request.market.token + source_request.policy.digest + plan.feature_attempt_id +
        str(source_request.through_day_boundary_ms) + plan.feature_hash + plan.coverage_hash).encode()).hexdigest()
    plan = replace(plan, request=source_request, token=token)
    request = replace(request, acquisition=acquired, decision=MappingProxyType(decision), supporting_rows=support,
        source_plan=plan)
    as_of = kwargs['as_of'] + timedelta(milliseconds=offset)
    group = replace(group, submitted_at=group.submitted_at + timedelta(milliseconds=offset), updated_at=as_of)
    executions = tuple(replace(e, trade_time=e.trade_time + timedelta(milliseconds=offset),
        trade_time_r=e.trade_time_r + offset) for e in executions)
    kwargs['as_of'] = as_of
    entry = StrategyOneEntryProposal('assignment', 'account', request.acquisition.ticker,
        100, 100, 1.0, 0.8, 1.2, 'target', 0.2, 100, 'support', 117)
    checkpoint = NativeChannelExitCheckpointReference(
        '55555555-5555-4555-8555-555555555555', 12, 'a' * 64,
        '66666666-6666-4666-8666-666666666666', 'b' * 64)
    args = dict(original_entry=entry, source_entry_intent_id=request.acquisition.source_entry_intent_id,
        current_financial=request.financial, checkpoint=checkpoint, **kwargs)
    return request, group, executions, args


def test_header_binds_risk_exact_clock_and_child_counts_without_publication(installed):
    request, group, executions, args = context(installed)
    header, sources, fills = project_native_channel_exit(request, group, executions, **args)
    assert header['source_row_count'] == len(sources) == 1
    assert header['execution_row_count'] == len(fills) == 1
    assert header['quote_age_us'] == 0
    assert header['original_ask'] == 1.0 and header['initial_stop'] == 0.8
    assert header['first_execution_time'] == request.acquisition.first_execution_time
    assert header['source_manager_snapshot_hash'] == 'a' * 64
    assert len(header['content_hash']) == 64
    assert project_native_channel_exit(request, group, executions, **args) == (header, sources, fills)


@pytest.mark.parametrize('defect', ['risk', 'owner', 'entry_after_fill', 'quantity', 'intent', 'checkpoint', 'source_scope'])
def test_projection_rejects_borrowed_or_stale_owner_inputs(installed, defect):
    request, group, executions, args = context(installed)
    if defect == 'risk': args['original_entry'] = replace(args['original_entry'], initial_stop=0.7)
    if defect == 'owner': args['original_entry'] = replace(args['original_entry'], account_id='foreign')
    if defect == 'entry_after_fill': args['original_entry'] = replace(args['original_entry'], boundary_ms=60_100)
    if defect == 'quantity': args['current_financial'] = replace(request.financial, position_quantity=4.0)
    if defect == 'intent': args['source_entry_intent_id'] = '77777777-7777-4777-8777-777777777777'
    if defect == 'checkpoint': args['checkpoint'] = dict(source_manager_checkpoint_sequence=12)
    if defect == 'source_scope':
        request = replace(request, source_plan=case(installed)[0].source_plan)
    with pytest.raises(ValueError): project_native_channel_exit(request, group, executions, **args)


def test_midnight_protocol_fixture_cannot_be_relabelled_native_pm_entry(installed):
    request, group, executions, kwargs = case(installed)
    _, _, _, args = context(installed)
    args.update(kwargs, current_financial=request.financial)
    with pytest.raises(ValueError, match='later than its actual acquisition'):
        project_native_channel_exit(request, group, executions, **args)
