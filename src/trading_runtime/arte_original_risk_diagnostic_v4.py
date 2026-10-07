"""Versioned selected-rule companion; existing failure table remains unchanged."""
from dataclasses import fields
from uuid import UUID, NAMESPACE_URL, uuid5

from .arte_journal_schema import TableContract
from .confirmed_original_risk_failure import (
    CompletedRiskBucket, OriginalRiskDecisionDiagnostic, validate_decision_diagnostic,
)
from .strategy_followthrough_failure import FollowThroughFailure

DIAGNOSTIC = TableContract('trading_original_risk_diagnostic_v4', (
    ('record_id','UUID'), ('parent_record_id','UUID'), ('run_id','String'),
    ('event_month','Date'), ('batch_id','UUID'), ('strategy_number','UInt32'),
    ('failure_record_id','UUID'), ('failure_content_hash','FixedString(64)'),
    ('source_entry_intent_id','UUID'), ('assignment_id','String'),
    ('semantic_rule','String'), ('boundary_ms','UInt32'),
    ('source_build_id','String'), ('source_market_plan_token','FixedString(64)'),
    ('source_bars_attempt_id','UUID'), ('source_indicators_attempt_id','UUID'),
    ('source_liquidity_attempt_id','UUID'),
    ('session_date','Date'), ('ticker','String'), ('has_prior','UInt8'),
    ('prior_boundary_ms','UInt32'), ('prior_close_int','UInt64'),
    ('prior_macd_line','Float64'), ('prior_macd_signal','Float64'),
    ('source_manager_snapshot_id','UUID'), ('source_manager_checkpoint_sequence','UInt64'),
    ('source_manager_snapshot_hash','FixedString(64)'),
    ('source_broker_snapshot_id','UUID'), ('source_broker_snapshot_hash','FixedString(64)'),
    ('content_hash','FixedString(64)')), 'toYYYYMM(event_month)',
    'run_id, parent_record_id, record_id')
TABLES = (DIAGNOSTIC,)


def diagnostic_policy(strategy_number):
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return numbered_fixed_strategy(strategy_number).confirmed_original_risk_policy


def diagnostic_premarket_policy(strategy_number):
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return getattr(numbered_fixed_strategy(strategy_number),'premarket_confirmed_original_risk_policy',None)


def project_original_risk_diagnostic(diagnostic, failure):
    policy = diagnostic_policy(failure['strategy_number'])
    if policy is None:
        raise ValueError('Diagnostic companion requires explicit declared capability')
    validate_decision_diagnostic(diagnostic,policy=policy,
        premarket_policy=diagnostic_premarket_policy(failure['strategy_number']))
    from .original_risk_checkpoint import OriginalRiskCheckpointReference
    if type(diagnostic.checkpoint) is not OriginalRiskCheckpointReference:
        raise ValueError('Diagnostic projection requires its actual native checkpoint reference')
    from dataclasses import asdict
    integer_fields={'boundary_ms','first_held_boundary_ms','completed_close_int','quote_age_us'}
    if any(getattr(diagnostic.current,f.name) != (int(failure[f.name]) if f.name in integer_fields
                                               else float(failure[f.name]))
           for f in fields(FollowThroughFailure)):
        raise ValueError('Diagnostic current witness differs from normalized failure')
    from .arte_journal_writer import typed_row
    sealed_failure = typed_row('trading_followthrough_failure_v4',
                              {k:v for k,v in failure.items() if k!='content_hash'})
    newest=diagnostic.newest;prior=diagnostic.prior
    return dict(record_id=str(uuid5(NAMESPACE_URL,f"{failure['run_id']}:{failure['parent_record_id']}:original-risk-diagnostic")),
        **{k:failure[k] for k in ('parent_record_id','run_id','event_month','batch_id',
            'strategy_number','source_entry_intent_id','assignment_id','boundary_ms')},
        failure_record_id=failure['record_id'],failure_content_hash=sealed_failure['content_hash'],
        semantic_rule=diagnostic.semantic_rule,source_build_id=newest.source_build_id,
        source_market_plan_token=newest.source_market_plan_token,
        source_bars_attempt_id=newest.source_bars_attempt_id,
        source_indicators_attempt_id=newest.source_indicators_attempt_id,
        source_liquidity_attempt_id=newest.source_liquidity_attempt_id,
        session_date=newest.session_date,ticker=newest.ticker,has_prior=int(prior is not None),
        prior_boundary_ms=prior.boundary_ms if prior else 0,
        prior_close_int=prior.close_int if prior else 0,
        prior_macd_line=prior.macd_line if prior else 0.,
        prior_macd_signal=prior.macd_signal if prior else 0., **asdict(diagnostic.checkpoint))


def restore_original_risk_diagnostic(row,witness):
    if type(row['has_prior']) is not int or row['has_prior'] not in (0,1):
        raise ValueError('Diagnostic prior cardinality is malformed')
    identity={k:str(row[k]) for k in ('source_build_id','source_market_plan_token',
        'source_bars_attempt_id','source_indicators_attempt_id','session_date','ticker',
        'source_liquidity_attempt_id')}
    newest=CompletedRiskBucket(witness.boundary_ms,witness.completed_close_int,True,
                              witness.macd_line,witness.macd_signal,**identity)
    prior=(CompletedRiskBucket(int(row['prior_boundary_ms']),int(row['prior_close_int']),True,
            float(row['prior_macd_line']),float(row['prior_macd_signal']),**identity)
            if row['has_prior'] else None)
    if prior is None and any(row[k]!=0 for k in
            ('prior_boundary_ms','prior_close_int','prior_macd_line','prior_macd_signal')):
        raise ValueError('Inherited diagnostic cannot hide unselected prior facts')
    from .original_risk_checkpoint import OriginalRiskCheckpointReference
    keys=('source_manager_snapshot_id','source_manager_checkpoint_sequence',
          'source_manager_snapshot_hash','source_broker_snapshot_id','source_broker_snapshot_hash')
    sequence=row['source_manager_checkpoint_sequence']
    if type(sequence) is not int and not (type(sequence) is str and sequence.isascii()
            and sequence.isdigit() and str(int(sequence))==sequence):
        raise ValueError('Diagnostic checkpoint sequence has malformed integer authority')
    reference=OriginalRiskCheckpointReference(**{k:(int(row[k]) if k=='source_manager_checkpoint_sequence'
                                                   else str(row[k])) for k in keys})
    result=OriginalRiskDecisionDiagnostic(witness,newest,prior,row['semantic_rule'],reference)
    policy=diagnostic_policy(row['strategy_number'])
    if policy is None:raise ValueError('Foreign undeclared original-risk companion')
    validate_decision_diagnostic(result,policy=policy,
        premarket_policy=diagnostic_premarket_policy(row['strategy_number']))
    return result


def seal_original_risk_diagnostics(client,rows,failures,intents,*,first_price_source=None,
                                   verified_prefix=None,events=()):
    """Recompute exact completed producer facts and bind the normalized exit graph."""
    from .arte_journal_writer import typed_row
    from .arte_intent_projection import _verify_stored_row
    selected_rows=tuple(r for r in failures if diagnostic_policy(r['strategy_number']) is not None)
    if len({str(r['parent_record_id']) for r in selected_rows})!=len(selected_rows):
        raise ValueError('Original-risk failure repeats selected parent authority')
    selected={str(r['parent_record_id']):r for r in selected_rows}
    if len(rows)!=len(selected):
        raise ValueError('Selected original-risk diagnostic is missing or extra')
    if len({str(r['record_id']) for r in intents})!=len(intents):
        raise ValueError('Original-risk intent inventory repeats parent authority')
    parents={str(r['record_id']):r for r in intents}
    if len({str(r['record_id']) for r in events}) != len(events):
        raise ValueError('Original-risk event inventory repeats parent authority')
    event_map={str(r['record_id']):r for r in events}
    sealed=[];seen=set();diagnostics={}
    for row in rows:
        key=str(row['parent_record_id'])
        if key in seen or key not in selected or key not in parents:
            raise ValueError('Original-risk diagnostic lacks unique selected failure parent')
        seen.add(key);failure=selected[key];parent=parents[key]
        # Shape and hash validation precedes interpretation, including foreign overrides.
        expected=typed_row(DIAGNOSTIC.name,{k:v for k,v in row.items() if k!='content_hash'})
        if 'content_hash' in row and row['content_hash']!=expected['content_hash']:
            raise ValueError('Original-risk diagnostic content hash changed')
        sealed_failure=typed_row('trading_followthrough_failure_v4',
                                {k:v for k,v in failure.items() if k!='content_hash'})
        if (str(row['failure_record_id'])!=str(failure['record_id'])
                or row['failure_content_hash']!=sealed_failure['content_hash']
                or any(str(row[k])!=str(failure[k]) for k in
                       ('run_id','batch_id','event_month','strategy_number',
                        'source_entry_intent_id','assignment_id','boundary_ms'))
                or row['ticker']!=parent['ticker']):
            raise ValueError('Original-risk diagnostic crosses failure, entry, assignment or exit')
        from .arte_followthrough_failure_v4 import _restore_failure_scalar
        witness=_restore_failure_scalar(failure)
        diagnostic=restore_original_risk_diagnostic(row,witness)
        if first_price_source is None:
            raise RuntimeError('Selected original-risk diagnostic lacks certified cold source authority')
        if key not in event_map:
            raise ValueError('Original-risk checkpoint lacks native exit event')
        from .original_risk_checkpoint import load_original_risk_checkpoint
        load_original_risk_checkpoint(client,verified_prefix,failure,parent,event_map[key],diagnostic,
                                      first_price_source=first_price_source)
        market=first_price_source.plan.source.market
        if (row['source_build_id']!=market.build_id or row['source_market_plan_token']!=market.token
                or market.sessions!=(str(row['session_date']),)):
            raise ValueError('Original-risk diagnostic differs from active certified session')
        from src.backend.backtest_confirmed_original_risk_source import load_completed_risk_lookup
        from datetime import date
        lookup=load_completed_risk_lookup(client,plan=market,
            session_date=date.fromisoformat(str(row['session_date'])),tickers=(row['ticker'],),
            through_boundary_ms=witness.boundary_ms)
        newest=lookup.bucket_at(row['ticker'],witness.boundary_ms)
        if newest!=diagnostic.newest:
            raise ValueError('Original-risk diagnostic newest facts differ from completed source')
        if diagnostic.prior is not None:
            pair=lookup.pair_at(row['ticker'],witness.boundary_ms)
            if pair!=(diagnostic.prior,diagnostic.newest):
                raise ValueError('Original-risk diagnostic prior facts differ from consecutive source')
        _verify_current_quote_source(client,diagnostic,market)
        diagnostics[key]=diagnostic;sealed.append(expected)
    return tuple(sealed),diagnostics


def _verify_current_quote_source(client,diagnostic,market):
    from .arte_journal_writer import _rows,_literal
    from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS, market_day_boundary, assert_select_only
    from datetime import date,datetime,timezone
    from fractions import Fraction
    newest=diagnostic.newest;witness=diagnostic.current
    units=[u for u in market.units if u.stage=='broker_100ms' and u.ticker==newest.ticker
           and u.session_date==newest.session_date]
    if len(units)!=1 or units[0].attempt_id!=newest.source_liquidity_attempt_id:
        raise ValueError('Diagnostic quote source differs from active certified attempt')
    bucket=(SESSION_OPEN_OFFSET_MS+witness.boundary_ms)//100-1
    query=assert_select_only(f'''SELECT bid_int,ask_int,quote_valid,quote_timestamp_us
        FROM arte.liquidity_100ms_v1 WHERE build_id={_literal(market.build_id)}
        AND session_date=toDate({_literal(newest.session_date)}) AND ticker={_literal(newest.ticker)}
        AND attempt_id=toUUID({_literal(newest.source_liquidity_attempt_id)})
        AND bucket_index={bucket} LIMIT 2 FORMAT JSONEachRow''')
    rows=_rows(client,query)
    if len(rows)!=1:raise ValueError('Diagnostic quote source is missing or ambiguous')
    def integer(value):
        if type(value) is int:return value
        if type(value) is str and value.isascii() and value.isdigit() and str(int(value))==value:return int(value)
        raise ValueError('Diagnostic source quote has malformed integer facts')
    row={k:integer(v) for k,v in rows[0].items()}
    at=market_day_boundary(date.fromisoformat(newest.session_date),witness.boundary_ms).astimezone(timezone.utc)
    elapsed=at-datetime(1970,1,1,tzinfo=timezone.utc)
    clock_us=(elapsed.days*86400+elapsed.seconds)*1_000_000+elapsed.microseconds
    if (set(row)!= {'bid_int','ask_int','quote_valid','quote_timestamp_us'} or row['quote_valid']!=1
            or Fraction(str(witness.bid))!=Fraction(row['bid_int'],10000)
            or Fraction(str(witness.ask))!=Fraction(row['ask_int'],10000)
            or clock_us-row['quote_timestamp_us']!=witness.quote_age_us):
        raise ValueError('Diagnostic fresh quote differs from exact contemporaneous source')


def load_original_risk_diagnostic(client,prefix,exit_record_id,failure):
    from .arte_journal_writer import _rows,_literal
    from .arte_intent_projection import _verify_stored_row
    from .arte_followthrough_failure_v4 import _restore_failure_scalar
    columns=','.join(k for k,_ in DIAGNOSTIC.columns)
    rows=_rows(client,f'''SELECT {columns} FROM arte.{DIAGNOSTIC.name}
        WHERE run_id={_literal(prefix.run_id)} AND parent_record_id=toUUID({_literal(exit_record_id)})
        LIMIT 2 FORMAT JSONEachRow''')
    if (len(rows)!=1 or str(rows[0]['batch_id']) not in prefix.batch_ids
            or str(rows[0]['batch_id'])!=str(failure['batch_id'])
            or str(rows[0]['parent_record_id'])!=str(failure['parent_record_id'])):
        raise RuntimeError('Recovered original-risk diagnostic lacks its exact committed prefix')
    _verify_stored_row(DIAGNOSTIC.name,rows[0])
    diagnostic=restore_original_risk_diagnostic(rows[0],_restore_failure_scalar(failure))
    from .arte_journal_writer import typed_row
    expected=project_original_risk_diagnostic(diagnostic,failure)
    if typed_row(DIAGNOSTIC.name,expected)['content_hash']!=rows[0]['content_hash']:
        raise RuntimeError('Recovered original-risk diagnostic differs from normalized failure')
    return rows[0],diagnostic
