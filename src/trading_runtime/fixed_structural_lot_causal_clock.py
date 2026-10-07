"""Declared causal OMS observation clock; execution states remain on100ms boundaries."""
from datetime import timezone
CLOCK_RULE = 'fixed-structural-lot-causal-roster-clock@1'


def selected_clock(entry_request):
    if entry_request is None:
        return False
    entry_request.verify()
    installed = entry_request.source.installed_payload
    if installed is None:
        return False
    manifest = installed['strategy']['numbered_release']
    rules = manifest['contract']['rule_set_contracts']
    if CLOCK_RULE not in rules:
        return False
    if rules.count(CLOCK_RULE) != 1:
        raise ValueError('Causal roster clock requires one exact declared rule')
    entry_request.source.require_installed_admission()
    return True


def observed_clock(updated_at, *, session_date, entry_boundary_ms, now_ms=None):
    from src.backend.backtest_market_data import market_day_boundary
    from .arte_journal_reader import _journal_instant
    instant = _journal_instant(updated_at)
    entry = market_day_boundary(session_date, entry_boundary_ms).astimezone(timezone.utc)
    if instant < entry:
        raise ValueError('Fixed lot roster clock precedes its entry')
    if now_ms is not None:
        require_completed_boundary(now_ms)
        if instant > market_day_boundary(session_date, now_ms).astimezone(timezone.utc):
            raise ValueError('Fixed lot roster clock exceeds its reached execution boundary')
    delta = instant - market_day_boundary(session_date, 0).astimezone(timezone.utc)
    elapsed_us = (delta.days *86400 +delta.seconds)*1000000 +delta.microseconds
    # The ceiling keeps comparisons with integer boundaries exact: no fractional
    # future observation can masquerade as an earlier millisecond.
    return (elapsed_us +999)//1000


def require_completed_boundary(now_ms):
    if type(now_ms) is not int or not 0 < now_ms <=57600000 or now_ms %100:
        raise ValueError('Causal roster needs an actually reached completed100ms boundary')
    return now_ms




def committed_observed_clock(client,prefix,group,*,session_date,entry_boundary_ms,now_ms=None,entry_sequence,max_fills=100000):
    """Exact ownership clock includes every committed fill of the group.

    A carried NBBO may leave OMS updated_at at entry. Completed-bar fills
    remain later facts and cannot be used at that earlier management instant.
    """
    from .arte_journal_reader import _journal_instant
    from .arte_journal_writer import _rows,_literal,_committed_batch_filter,_CONTRACTS,load_committed_execution_page
    from .arte_oms_projection import _verified_rows
    from decimal import Decimal
    if type(entry_sequence) is not int or not 1<=entry_sequence<group.sequence<=prefix.last_sequence:
        raise ValueError('Causal roster entry/OMS source sequences differ')
    if type(max_fills) is not int or not 1<=max_fills<=100000:
        raise ValueError('Causal roster fill inventory bound differs')
    observed_clock(group.group['updated_at'],session_date=session_date,entry_boundary_ms=entry_boundary_ms,now_ms=now_ms)
    latest=_journal_instant(group.group['updated_at'])
    ids=tuple(str(row['broker_order_id']) for row in group.broker_bindings)
    if not ids or len(set(ids))!=len(ids):
        raise ValueError('Causal roster broker identities differ')
    columns=','.join(column for column,_ in _CONTRACTS['trading_execution_v1'].columns)
    rows=_rows(client,f"SELECT {columns} FROM arte.trading_execution_v1 WHERE run_id={_literal(prefix.run_id)} "
        +"AND broker_order_id IN ("+','.join(_literal(value) for value in ids)+") "
        +_committed_batch_filter(prefix)+f"ORDER BY source_event_time,record_id LIMIT {max_fills+1} FORMAT JSONEachRow")
    if len(rows)>max_fills:
        raise ValueError('Causal roster committed fill inventory exceeds bound')
    _verified_rows('trading_execution_v1',rows)
    execution_ids=tuple(row['execution_id'] for row in rows)
    if len(set(execution_ids))!=len(execution_ids):
        raise ValueError('Causal roster execution identities repeat')
    verified={}
    for offset in range(0,len(execution_ids),999):
        for row in load_committed_execution_page(client,prefix,execution_ids=execution_ids[offset:offset+999],limit=1000):
            verified[row['execution_id']]=row
    for row in rows:
        envelope=verified.get(row['execution_id'])
        if (envelope is None or envelope['sequence']<=entry_sequence
                or envelope['sequence']>prefix.last_sequence
                or any(envelope.get(key)!=value for key,value in row.items())):
            raise ValueError('Causal roster execution differs from its committed source envelope')
    orders={str(binding['broker_order_id']):group.orders[binding['request_index']] for binding in group.broker_bindings}
    seen=set()
    totals={value:Decimal(0) for value in ids}
    for row in rows:
        if (row['run_id']!=prefix.run_id or row['broker_order_id'] not in ids
                or row['account_id']!=group.group['account_id']
                or row['strategy_id']!=group.group['strategy_id']
                or int(row['strategy_revision'])!=int(group.group['strategy_revision'])
                or row['ticker']!=orders[row['broker_order_id']].ticker
                or row['side'] not in ({'B','BUY'} if orders[row['broker_order_id']].side=='BUY' else {'S','SELL'})
                or row['client_order_id']!=orders[row['broker_order_id']].cOID
                or int(row['conid'])!=orders[row['broker_order_id']].conid
                or str(row['batch_id']) not in prefix.batch_ids
                or row['execution_id'] in seen or Decimal(str(row['quantity']))<=0):
            raise ValueError('Causal roster committed execution ownership differs')
        seen.add(row['execution_id'])
        quantity=Decimal(str(row['quantity']))
        if not quantity.is_finite():
            raise ValueError('Causal roster execution quantity is nonfinite')
        totals[row['broker_order_id']]+=quantity
        # Validate each fact before max; a later fact cannot hide an invalid one.
        observed_clock(row['source_event_time'],session_date=session_date,entry_boundary_ms=entry_boundary_ms,now_ms=now_ms)
        latest=max(latest,_journal_instant(row['source_event_time']))
    for binding in group.broker_bindings:
        wanted=Decimal(str(binding['filled_quantity'])) if binding['has_filled_quantity'] else Decimal(0)
        if totals[binding['broker_order_id']]!=wanted:
            raise ValueError('Causal roster committed fills differ from complete broker quantities')
    if any(row['has_filled_quantity']==1 and Decimal(str(row['filled_quantity']))>0 for row in group.broker_bindings) and not rows:
        raise ValueError('Causal roster filled ownership lacks committed executions')
    return observed_clock(latest.strftime('%Y-%m-%d %H:%M:%S.%f'),session_date=session_date,entry_boundary_ms=entry_boundary_ms,now_ms=now_ms)
