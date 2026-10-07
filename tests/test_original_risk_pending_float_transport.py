"""Selected projection and native restore across integral Float64 JSON transport."""
import json
from dataclasses import fields, is_dataclass, replace
from datetime import date
from decimal import Decimal

import pytest

from src.backend.backtest_strategy_one_management import OriginalRiskManagementState
from src.trading_runtime import strategy_one_management_snapshot as snapshots
from src.trading_runtime.original_risk_pending_snapshot import (
    PENDING, canonical_pending_snapshot_row, restore_pending_requests,
)
from src.trading_runtime.strategy_one_protection_snapshot import _digest


def captured(quantity=967.0, *, confirmed=False, signed_zero=False):
    from test_original_risk_pending_snapshot import genuine_entry_graph
    from src.trading_runtime.confirmed_original_risk_failure import (
        CompletedRiskBucket, OriginalRiskDecisionDiagnostic,
        CONFIRMED_ORIGINAL_RISK_RULE, INHERITED_ORIGINAL_RISK_RULE,
    )
    from src.trading_runtime.original_risk_checkpoint import OriginalRiskCheckpointRequest
    from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
    from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
    from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
    from src.trading_runtime.strategy_one_position import ProtectionState
    proposal,intent,authority,_=genuine_entry_graph('float-transport')
    financial=StrategyOneFinancialView(proposal.assignment_id,proposal.account_id,proposal.ticker,
        AssignmentStatus.MANAGING,StrategyPermissions(),quantity,False,False,False,1)
    close,line,signal,bid,ask=(99800,.1,.2,9.98,9.99) if confirmed else (98000,-.1,-.05,9.8,9.81)
    newest=CompletedRiskBucket(120000,close,True,line,signal,'build','a'*64,
        '00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002',
        '2026-08-18','AAA','00000000-0000-0000-0000-000000000003')
    witness=FollowThroughFailure(120000,42000,10.01,9.89,close,line,signal,bid,ask,1000)
    prior=replace(newest,boundary_ms=115000) if confirmed else None
    if signed_zero:prior=replace(prior,macd_line=-0.0)
    diagnostic=OriginalRiskDecisionDiagnostic(witness,newest,prior,
        CONFIRMED_ORIGINAL_RISK_RULE if confirmed else INHERITED_ORIGINAL_RISK_RULE)
    request=OriginalRiskCheckpointRequest(diagnostic,financial,intent.intent_id)
    key=financial.account_id,financial.assignment_id,financial.ticker
    state=OriginalRiskManagementState(120000,((key,proposal),),
        ((key,ProtectionState(42000,proposal.initial_stop,proposal.initial_target)),),(),
        ((key,100100),),(),((key,42000),),(request,))
    rows=snapshots.project_manager_snapshot(run_id='float-transport',session_date=date(2026,8,18),
        checkpoint_sequence=42,state=state,first_price_source=authority)
    return state,rows


def clickhouse_json_transport(value):
    """Model observed JSONEachRow integral-Float64 emission, keeping typed containers."""
    if is_dataclass(value):
        return replace(value,**{f.name:clickhouse_json_transport(getattr(value,f.name)) for f in fields(value)})
    if type(value) is tuple:return tuple(clickhouse_json_transport(v) for v in value)
    if type(value) is dict:
        return json.loads(json.dumps({k:clickhouse_json_transport(v) for k,v in value.items()}))
    if type(value) is float and value.is_integer():return int(value)
    return value


@pytest.mark.parametrize('quantity', [967,967.0])
@pytest.mark.parametrize('confirmed', [False,True])
def test_real_project_json_transport_manager_restore_preserves_pending_state(quantity,confirmed):
    state,rows=captured(quantity,confirmed=confirmed)
    projected,=rows.original_risk_requests
    assert type(projected['held_quantity']) is float
    assert type(projected['prior_macd_line']) is float
    transported=clickhouse_json_transport(rows)
    readback,=transported.original_risk_requests
    assert type(readback['held_quantity']) is int
    if not confirmed:assert type(readback['prior_macd_line']) is int
    restored=snapshots.restore_manager_snapshot(transported)
    assert type(restored) is OriginalRiskManagementState
    assert restored.original_risk_requests==state.original_risk_requests
    assert restored.first_held_boundaries==state.first_held_boundaries
    assert restored.positions==state.positions


def test_integer_and_float_projection_produce_identical_hashes():
    _,integer=captured(967)
    _,floating=captured(967.0)
    assert integer.original_risk_requests==floating.original_risk_requests
    assert integer.snapshot==floating.snapshot


def test_real_confirmed_negative_zero_prior_survives_integral_json_transport():
    state,rows=captured(confirmed=True,signed_zero=True)
    transported=clickhouse_json_transport(rows)
    assert type(transported.original_risk_requests[0]['prior_macd_line']) is int
    restored=snapshots.restore_manager_snapshot(transported)
    assert restored.original_risk_requests==state.original_risk_requests


@pytest.fixture(scope='module')
def pending_row():
    _,rows=captured()
    return rows.original_risk_requests[0]


@pytest.mark.parametrize('name', [k for k,kind in PENDING.columns if kind=='Float64'])
@pytest.mark.parametrize('bad', [True,None,'0',Decimal('0'),float('nan'),float('inf'),float('-inf'),10**400])
def test_declared_float_columns_reject_foreign_and_nonfinite_values(pending_row,name,bad):
    with pytest.raises(ValueError,match='Float64 scalar'):
        canonical_pending_snapshot_row(dict(pending_row,**{name:bad}))


@pytest.mark.parametrize('defect', ['quantity','content_hash','boundary','session','snapshot','missing','extra','count','aggregate'])
def test_transport_normalization_keeps_hash_root_and_inventory_guards(defect):
    _,rows=captured()
    seal=dict(rows.snapshot);row=clickhouse_json_transport(rows.original_risk_requests[0])
    if defect=='quantity':row['held_quantity']+=1
    elif defect=='content_hash':row['content_hash']='f'*64
    elif defect=='boundary':row['boundary_ms']+=100
    elif defect=='session':row['session_date']='2026-08-19'
    elif defect=='snapshot':row['snapshot_id']='00000000-0000-0000-0000-000000000001'
    elif defect=='missing':row.pop('held_quantity')
    elif defect=='extra':row['foreign']=0
    elif defect=='count':seal['original_risk_pending_count']+=1
    else:seal['original_risk_pending_hash']='f'*64
    with pytest.raises(ValueError):restore_pending_requests((row,),seal)


def test_selected_normalizer_preserves_generic_snapshot_canonicalizer(pending_row):
    from src.trading_runtime.strategy_one_protection_snapshot import _canonical_snapshot_row
    transported=clickhouse_json_transport(pending_row)
    assert type(_canonical_snapshot_row(PENDING,transported)['held_quantity']) is int
    canonical=canonical_pending_snapshot_row(transported)
    assert type(canonical['held_quantity']) is float
    assert _digest({k:v for k,v in canonical.items() if k!='content_hash'})==pending_row['content_hash']
