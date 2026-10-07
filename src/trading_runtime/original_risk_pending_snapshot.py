"""Selected typed pending decisions, sealed by a versioned manager root."""
from dataclasses import fields

from .arte_journal_schema import TableContract
from .arte_followthrough_failure_v4 import FAILURE
from .arte_original_risk_diagnostic_v4 import DIAGNOSTIC


_GRAPH = frozenset(('record_id','parent_record_id','run_id','event_month','batch_id',
    'failure_record_id','failure_content_hash','content_hash',
    'source_manager_snapshot_id','source_manager_checkpoint_sequence','source_manager_snapshot_hash',
    'source_broker_snapshot_id','source_broker_snapshot_hash'))
_FAILURE_FIELDS = tuple((name,kind) for name,kind in FAILURE.columns if name not in _GRAPH)
_DIAGNOSTIC_FIELDS = tuple((name,kind) for name,kind in DIAGNOSTIC.columns
    if name not in _GRAPH and name not in dict(_FAILURE_FIELDS))
PENDING = TableContract('trading_original_risk_pending_snapshot_v1',(
    ('snapshot_id','UUID'),('run_id','String'),('snapshot_month','Date'),
    ('checkpoint_sequence','UInt64'),('account_id','String'),('held_quantity','Float64'),
    ('status','String'),('permission_observe','UInt8'),('permission_enter','UInt8'),
    ('permission_add','UInt8'),('permission_reduce','UInt8'),('permission_exit','UInt8'),
    ('permission_reenter','UInt8'),('pending_entry','UInt8'),('pending_exit','UInt8'),
    ('pending_capital_request','UInt8'),('completed_entries','UInt32'),
    ('reentry_not_before_ms','UInt32'),('current_purchase_groups','UInt32'),
    *_FAILURE_FIELDS,*_DIAGNOSTIC_FIELDS,('content_hash','FixedString(64)')),
    'toYYYYMM(snapshot_month)','run_id, checkpoint_sequence, account_id, assignment_id, ticker')


def canonical_pending_snapshot_row(row):
    """Use declared Float64 types before hashing or verifying JSON readback."""
    from math import isfinite
    from .strategy_one_protection_snapshot import _canonical_snapshot_row
    canonical = _canonical_snapshot_row(PENDING, row)
    for name, kind in PENDING.columns:
        if kind != 'Float64':
            continue
        value = canonical[name]
        if type(value) not in (int, float):
            raise ValueError('Pending original-risk Float64 scalar has a foreign type')
        try:
            value = float(value)
        except OverflowError as error:
            raise ValueError('Pending original-risk Float64 scalar is not finite') from error
        if not isfinite(value):
            raise ValueError('Pending original-risk Float64 scalar is not finite')
        canonical[name] = 0.0 if value == 0.0 else value
    return canonical


def selected_parent_contract():
    from .strategy_one_management_snapshot import PARENT_V3
    return TableContract('trading_strategy_one_manager_snapshot_v4',(
        *PARENT_V3.columns[:-1],('original_risk_pending_count','UInt32'),
        ('original_risk_pending_hash','FixedString(64)'),PARENT_V3.columns[-1]),
        PARENT_V3.partition,PARENT_V3.order)


def selected_snapshot_contracts():
    return (PENDING,selected_parent_contract())


def project_pending_requests(requests, common, state):
    from .original_risk_checkpoint import OriginalRiskCheckpointRequest, validate_original_risk_state
    from .confirmed_original_risk_failure import validate_decision_diagnostic
    from .numbered_fixed_strategy import numbered_fixed_strategy
    from .strategy_followthrough_failure import FollowThroughFailure
    from .strategy_one_protection_snapshot import _digest,_price
    if type(requests) is not tuple or len(requests)>65536:
        raise ValueError('Pending original-risk snapshot is not bounded typed authority')
    result=[];seen=set()
    for request in requests:
        if type(request) is not OriginalRiskCheckpointRequest:
            raise ValueError('Pending original-risk snapshot contains foreign requests')
        diagnostic=request.diagnostic;witness=request.witness;financial=request.financial
        source=validate_original_risk_state(witness,state,financial)
        policy=numbered_fixed_strategy(source.strategy_number).confirmed_original_risk_policy
        validate_decision_diagnostic(diagnostic,policy=policy)
        newest=diagnostic.newest;prior=diagnostic.prior
        key=financial.account_id,financial.assignment_id,financial.ticker
        if (key in seen or newest.ticker!=financial.ticker
                or newest.session_date!=common['session_date']):
            raise ValueError('Pending original-risk request repeats or crosses entry/session authority')
        seen.add(key)
        scalar={f.name:getattr(witness,f.name) for f in fields(FollowThroughFailure)}
        for name in ('reference_ask','initial_stop','bid','ask'):
            scalar[name]=_price(scalar[name])
        row={k:common[k] for k in ('snapshot_id','run_id','snapshot_month','checkpoint_sequence')}
        row.update(account_id=financial.account_id,held_quantity=financial.position_quantity,
            status=financial.status.value,
            **{'permission_'+name:int(getattr(financial.permissions,name))
               for name in ('observe','enter','add','reduce','exit','reenter')},
            pending_entry=int(financial.pending_entry),pending_exit=int(financial.pending_exit),
            pending_capital_request=int(financial.pending_capital_request),
            completed_entries=financial.completed_entries,reentry_not_before_ms=financial.reentry_not_before_ms,
            current_purchase_groups=financial.current_purchase_groups,
            strategy_number=source.strategy_number,source_entry_intent_id=request.source_entry_intent_id,
            assignment_id=financial.assignment_id,**scalar,semantic_rule=diagnostic.semantic_rule,
            source_build_id=newest.source_build_id,source_market_plan_token=newest.source_market_plan_token,
            source_bars_attempt_id=newest.source_bars_attempt_id,
            source_indicators_attempt_id=newest.source_indicators_attempt_id,
            source_liquidity_attempt_id=newest.source_liquidity_attempt_id,
            session_date=newest.session_date,ticker=newest.ticker,has_prior=int(prior is not None),
            prior_boundary_ms=prior.boundary_ms if prior else 0,
            prior_close_int=prior.close_int if prior else 0,
            prior_macd_line=prior.macd_line if prior else 0.,
            prior_macd_signal=prior.macd_signal if prior else 0.)
        canonical = canonical_pending_snapshot_row({**row, 'content_hash': ''})
        canonical.pop('content_hash')
        result.append({**canonical,'content_hash':_digest(canonical)})
    return tuple(sorted(result,key=lambda r:(r['account_id'],r['assignment_id'],r['ticker'])))


def restore_pending_requests(rows, seal):
    from .strategy_one_protection_snapshot import _digest
    from .strategy_followthrough_failure import FollowThroughFailure
    from .confirmed_original_risk_failure import CompletedRiskBucket,OriginalRiskDecisionDiagnostic
    from .original_risk_checkpoint import OriginalRiskCheckpointRequest
    from .strategy_one_stateful import StrategyOneFinancialView
    from .strategy_engine import AssignmentStatus,StrategyPermissions
    result=[];seen=set();canonical=[]
    for raw in rows:
        row=canonical_pending_snapshot_row(raw)
        if (row['content_hash']!=_digest({k:v for k,v in row.items() if k!='content_hash'})
                or any(row[k]!=seal[k] for k in ('snapshot_id','run_id','snapshot_month','checkpoint_sequence'))
                or row['boundary_ms']!=seal['boundary_ms'] or row['session_date']!=seal['session_date']):
            raise ValueError('Pending original-risk snapshot crosses its selected manager root')
        key=row['account_id'],row['assignment_id'],row['ticker']
        if key in seen:raise ValueError('Pending original-risk snapshot repeats held identity')
        seen.add(key);canonical.append(row)
        integers={'boundary_ms','first_held_boundary_ms','completed_close_int','quote_age_us'}
        witness=FollowThroughFailure(**{f.name:(row[f.name] if f.name in integers else float(row[f.name]))
            for f in fields(FollowThroughFailure)})
        identity={k:row[k] for k in ('source_build_id','source_market_plan_token','source_bars_attempt_id',
            'source_indicators_attempt_id','source_liquidity_attempt_id','session_date','ticker')}
        newest=CompletedRiskBucket(witness.boundary_ms,witness.completed_close_int,True,
                                   witness.macd_line,witness.macd_signal,**identity)
        if type(row['has_prior']) is not int or row['has_prior'] not in (0,1):
            raise ValueError('Pending original-risk prior cardinality changed')
        prior=(CompletedRiskBucket(row['prior_boundary_ms'],row['prior_close_int'],True,
            row['prior_macd_line'],row['prior_macd_signal'],**identity) if row['has_prior'] else None)
        if prior is None and any(row[k]!=0 for k in ('prior_boundary_ms','prior_close_int',
                                                    'prior_macd_line','prior_macd_signal')):
            raise ValueError('Pending inherited decision hides prior observations')
        flags=('permission_observe','permission_enter','permission_add','permission_reduce','permission_exit',
               'permission_reenter','pending_entry','pending_exit','pending_capital_request')
        if any(type(row[k]) is not int or row[k] not in (0,1) for k in flags):
            raise ValueError('Pending original-risk financial flags are malformed')
        financial=StrategyOneFinancialView(row['assignment_id'],row['account_id'],row['ticker'],
            AssignmentStatus(row['status']),StrategyPermissions(**{name:bool(row['permission_'+name])
                for name in ('observe','enter','add','reduce','exit','reenter')}),row['held_quantity'],
            bool(row['pending_entry']),bool(row['pending_exit']),bool(row['pending_capital_request']),
            row['completed_entries'],row['reentry_not_before_ms'],row['current_purchase_groups'])
        result.append(OriginalRiskCheckpointRequest(OriginalRiskDecisionDiagnostic(
            witness,newest,prior,row['semantic_rule']),financial,row['source_entry_intent_id']))
    canonical.sort(key=lambda r:(r['account_id'],r['assignment_id'],r['ticker']))
    if (seal['original_risk_pending_count']!=len(canonical)
            or seal['original_risk_pending_hash']!=_digest([r['content_hash'] for r in canonical])):
        raise ValueError('Selected manager root lacks complete pending original-risk companions')
    return tuple(result)
