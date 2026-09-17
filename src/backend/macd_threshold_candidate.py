"""Fresh strategy/lifecycle declarations over the existing account infrastructure."""
from copy import deepcopy
from src.trading_runtime.macd_threshold import CONTRACT, DEFAULTS

PROFILE_ID='macd-threshold-100ms'
LABEL='100ms MACD thresholds - enter -5 / exit -10 bps'


def build(base, *, profile_id=PROFILE_ID, label=LABEL, parameters=None, macd_timeframe='100ms', quantity=100.):
    # Shared declaration plumbing; each saved candidate owns its policy parameters.
    PROFILE_ID, LABEL = profile_id, label
    payload=deepcopy(base)
    canvas=payload.pop('canvas')
    rules=[]
    for suffix,source,timeframe,comparator,value in [
        ('price','market.last_price','100ms','greater_than',0.),
        ('macd','indicator.macd.line',macd_timeframe,'greater_than',-1e9),
        ('signal','indicator.macd.signal',macd_timeframe,'greater_than',-1e9),
        ('quality-clock','market.last_price','1s','greater_than',0.),
        ('invalid','market.last_price','100ms','less_or_equal',0.)]:
        rules.append(dict(rule_set_id=PROFILE_ID+'-'+suffix,name='Observe '+suffix,enabled=True,
            operator='all',conditions=[dict(condition_id=suffix,enabled=True,left_source_id=source,
                left_timeframe=timeframe,comparator=comparator,value=value)]))
    payload['market_discovery']['rule_sets'].extend(rules)
    def stage(ids,operator='and'):
        return dict(operator='all',groups=[],expression=dict(kind='operator',operator=operator,
            children=[dict(kind='rule_set',rule_set_id=PROFILE_ID+'-'+x) for x in ids]))
    observe=stage(['price','macd','signal','quality-clock'])
    blocker=stage(['invalid'])
    order=dict(execution_policy='adaptive_urgent',deadline_ms=100,partial_fill_policy='complete_remainder')
    capital=dict(mode='fixed_quantity',value=quantity,allow_replacement=False)
    lifecycle=dict(phase_modes={k:'automatic' for k in ['initial_entry','manage','exit','reentry']},
        trading_behavior=dict(side='long',eligible_sessions=['premarket','regular','after_hours'],
            entry_cutoff_time='20:00:00',flatten_time='20:00:00'),
        initial_entry=dict(action_id='position.enter_long',opportunity=observe,confirmation=observe,
            blockers=blocker,capital_request=capital,order_intent=order,add_steps=[]),
        reentry=dict(action_id='position.enter_long',enabled=True,cooldown_ms=0,unlimited_attempts=True,
            maximum_attempts=0,after_protective_exit=True,require_new_confirmation=False,
            capital_request=capital,order_intent=order,
            rules=dict(opportunity=observe,confirmation=observe,blockers=blocker)),exit=dict(rule_sets=[]))
    # Definition ID selects the existing assignment/OMS adapter. This profile's
    # independent evaluator bypasses that adapter's legacy trading policy.
    profile=dict(profile_id=PROFILE_ID,name=LABEL,revision=1,definition_id='long-momentum-campaign',
        definition_revision=47,derived_from_profile_id='',description=LABEL,enabled=True,origin='user',
        editable=True,protected=False,publication_status='draft',action_policy_ids=[],lifecycle=lifecycle,
        parameters=deepcopy(parameters) if parameters is not None else dict(macd_threshold_contract=CONTRACT,macd_threshold=dict(DEFAULTS)))
    payload['strategy']['profiles'].append(profile)
    payload['strategy']['active_profile_id']=PROFILE_ID
    watch_id=PROFILE_ID+'-universe'
    watch=dict(watchlist_id=watch_id,name='Selected historical instruments',enabled=True,origin='user',
        template=False,availability='available',source_scan_id='qmd-core-scan',
        inclusion_rule_sets=[PROFILE_ID+'-quality-clock'],inclusion_operator='all',exclusion_rule_sets=[],
        manual_inclusions=[],manual_exclusions=[],maximum_size=10000,refresh_interval_ms=1000,
        membership_expiry='end_of_trading_day',membership_ttl_ms=300000,columns=['symbol','last_price'],
        column_intervals={},column_aggregations={},membership_history=[])
    payload['market_discovery']['watchlists'].append(watch)
    stream=dict(signal_stream_id=PROFILE_ID+'-observe',name='Observe selected instruments',enabled=True,
        revision=1,origin='user',protected=False,source_type='core_scan',source_id='qmd-core-scan',
        source_scan_id='qmd-core-scan',inclusion_rule_sets=[PROFILE_ID+'-quality-clock'],
        inclusion_operator='all',refresh_interval_ms=1000,trigger_policy='false_to_true',
        rearm_policy='after_false',cooldown_ms=0,maximum_events=10000,columns=['symbol','last_price'],
        column_intervals={},column_aggregations={},watchlist_routes=[])
    payload['market_discovery']['signal_streams'].append(stream)
    payload['run_plans']['universes'].append(dict(universe_id=watch_id,name='Selected instruments',
        source='watchlist',symbols=[],scanner_view_id=watch_id,scanner_view_ids=[watch_id],
        signal_stream_ids=[stream['signal_stream_id']],signal_stream_snapshots=[deepcopy(stream)],watchlist_snapshots=[deepcopy(watch)]))
    plan_id=PROFILE_ID+'-backtest'
    # Account limits/broker identities remain owned by Portfolio, not the policy.
    account=next(x for x in payload['accounts']['bindings'] if x.get('enabled',True) and 'backtest' in x.get('modes',[]))
    budget=next(x for x in payload['portfolio']['mandates'] if x['account_key']==account['account_key'])
    mandate=deepcopy(budget)
    mandate.update(mandate_id=plan_id,run_plan_id=plan_id,principal_id=plan_id,
        maximum_action_authority='automatic')
    payload['portfolio']['mandates'].append(mandate)
    plan=dict(run_plan_id=plan_id,name=LABEL,description=LABEL,profile_id=PROFILE_ID,enabled=True,
        compiled=False,allowed_environments=['backtest'],universe_id=watch_id,book_id='default',
        oms_profile_id=payload['oms']['profiles'][0]['profile_id'],
        canvas_profile_id='current-canvas',mandate_ids=[plan_id],runtime_assignments=[],
        signal_stream_ids=[stream['signal_stream_id']],watchlist_ids=[watch_id],source_revision_policy='require_complete',
        data_plan_ids={'backtest':'market.historical_scanner_materialization.v1'},
        enablement=dict(state='enabled',scope='persistent',effective_session=''),
        activation=dict(event_policy='new_occurrences',watchlist_policy='not_required'),
        action_policy_rule_set_ids=[],action_authority={k:'automatic' for k in
            ['default','initial_entry','add','reentry','strategic_exit','protective_exit','emergency_exit']},
        campaign_lifecycle=dict(initial_entry_authority='automatic',reentry_authority='automatic',
            exit_authority='automatic',protective_exit_authority='automatic',maximum_reentries=0,
            reentry_cooldown_ms=0,maximum_initial_watch_ms=0,session_end_behavior='exit_and_stop',
            retain_ticker_while_paused=True))
    payload['run_plans']['plans'].append(plan)
    return payload,canvas,plan_id


def create():
    from .trading_configuration_service import configuration_base,create_test_candidate
    payload,canvas,plan=build(configuration_base())
    return create_test_candidate(label=LABEL,canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan,strategy_profile_id=PROFILE_ID)
