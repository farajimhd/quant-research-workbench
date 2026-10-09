"""Composed worker-side exit verification; writer admission remains separate."""
from types import MappingProxyType

from .profit_armed_structural_rejection_exit import REASON
from .arte_structural_rejection_exit_v1 import EXIT,_intent_hash


def prepare_native_structural_rejection_exit_rows(client,rows,intents,events,*,verified_prefix,first_price_source):
    """Verify the entire own parent graph before returning normalized rows.

    Caller must supply the genuinely cold-verified preceding journal prefix.
    No permissions, execution lease or persistence capability is issued here.
    Checks belong in the asynchronous publisher, never the market decision loop.
    """
    from .arte_journal_writer import typed_row,_wire_row
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from .strategy_one_stateful import StrategyOneFinancialView
    from .strategy_engine import AssignmentStatus,StrategyPermissions
    from .structural_rejection_exit_financial_checkpoint import load_structural_rejection_exit_financial_checkpoint
    from .structural_rejection_exit_manager_checkpoint import load_structural_rejection_exit_manager_checkpoint
    from .structural_rejection_exit_quote_source import load_structural_rejection_exit_quote
    from .arte_strategy_one_entry_journal import load_committed_strategy_one_entry_page
    if (any(type(family) is not tuple for family in (rows,intents,events))
            or max(len(rows),len(intents),len(events))>65536):
        raise ValueError('Structural rejection exit publication requires bounded tuple families')
    parents={str(parent['record_id']):parent for parent in intents if parent.get('reason')==REASON}
    event_index={str(event['record_id']):event for event in events}
    if (len({str(parent['record_id']) for parent in intents})!=len(intents)
            or len(event_index)!=len(events) or len(rows)!=len(parents)
            or len({str(row['parent_record_id']) for row in rows})!=len(rows)
            or {str(row['parent_record_id']) for row in rows}!=set(parents)):
        raise ValueError('Structural rejection exit publication has missing or duplicate own parents')
    if not rows:
        return ()
    profile=require_native_structural_rejection_profile(getattr(client,'structural_rejection_profile',None))
    pending=[]
    for raw in rows:
        row=_wire_row(EXIT.name,raw)
        if typed_row(EXIT.name,{k:v for k,v in row.items() if k!='content_hash'})!=row:
            raise ValueError('Structural rejection exit stored row hash changed')
        parent=parents[row['parent_record_id']];event=event_index.get(row['parent_record_id'])
        if event is None or parent['account_id']!=row['account_id']:
            raise ValueError('Structural rejection exit publication lacks its exact event/account')
        financial=StrategyOneFinancialView(row['assignment_id'],row['account_id'],row['ticker'],
            AssignmentStatus.MANAGING,StrategyPermissions(),row['position_quantity'],False,False,False,1)
        pending.append((row,parent,event,financial))
    needed={row['source_entry_intent_id'] for row,_,_,_ in pending}
    entries={};cursor=0;scanned=0;pages=0
    # One bounded native original-entry scan for the complete proposed graph.
    # Avoid a complete-prefix scan for each exit.
    while True:
        page=load_committed_strategy_one_entry_page(client,verified_prefix,after_sequence=cursor,
            limit=200,first_price_source=first_price_source)
        pages+=1
        scanned+=len(page.entries)
        if pages>1000 or scanned>100000 or page.scanned_through_sequence<cursor:
            raise ValueError('Structural rejection original-entry scan exceeds ordered native bound')
        for entry in page.entries:
            if entry.intent.intent_id in needed:
                if entry.intent.intent_id in entries:
                    raise ValueError('Structural rejection original entry is duplicated in committed prefix')
                entries[entry.intent.intent_id]=entry
        if page.exhausted:
            break
        if page.scanned_through_sequence<=cursor:
            raise ValueError('Structural rejection original-entry scan made no progress')
        cursor=page.scanned_through_sequence
    if set(entries)!=needed:
        raise ValueError('Structural rejection exit lacks exact committed original entries')
    result=[]
    for row,parent,event,financial in pending:
        load_structural_rejection_exit_financial_checkpoint(client,verified_prefix,row,parent,event,financial,
            first_price_source=first_price_source)
        manager=load_structural_rejection_exit_manager_checkpoint(client,verified_prefix,row,financial,
            first_price_source=first_price_source)
        entry=entries[row['source_entry_intent_id']]
        if (not entry.sequence<row['source_manager_checkpoint_sequence']
                or entry.proposal.assignment_id!=row['assignment_id']
                or entry.proposal.account_id!=row['account_id'] or entry.intent.ticker!=row['ticker']
                or entry.intent!=manager.original_entry):
            raise ValueError('Structural rejection original entry differs from certified manager source')
        load_structural_rejection_exit_quote(client,manager.witness,profile=profile)
        # Intent hash binds execution policy, quantity, clock and metadata.
        # Reconstruction from normalized parent is shared with all strategies.
        from .arte_intent_projection import ProjectedIntent,project_strategy_intent,restore_strategy_intent
        from datetime import datetime,timezone
        at=datetime.fromisoformat(str(event['event_time']).replace('Z','+00:00'))
        if at.tzinfo is None:
            at=at.replace(tzinfo=timezone.utc)
        keys=set(project_strategy_intent(entry.intent).core)-{'event_time'}
        intent=restore_strategy_intent(ProjectedIntent(
            {**{key:parent[key] for key in keys},'event_time':at.astimezone(timezone.utc).isoformat()},()))
        from .profit_armed_structural_rejection_exit import replay_structural_rejection_exit_intent
        expected=replay_structural_rejection_exit_intent(run_id=row['run_id'],
            source_entry_intent_id=row['source_entry_intent_id'],witness=manager.witness,financial=financial)
        if intent!=expected or _intent_hash(intent)!=row['intent_hash']:
            raise ValueError('Structural rejection parent differs from complete saved factory intent')
        result.append(MappingProxyType(row))
    return tuple(result)
