"""Issued frozen exit transport; no native persistence or order capability."""
from dataclasses import dataclass
from types import MappingProxyType
from weakref import WeakKeyDictionary

from .arte_structural_rejection_exit_v1 import (
    require_buffered_structural_rejection_exit,project_buffered_structural_rejection_exit)

_ISSUED=WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class V4StructuralRejectionExitBatch:
    base: object
    evidence: object


def prepare_structural_rejection_exit_batch(base,buffered):
    from .arte_journal_writer import TypedJournalBatch,_FAMILIES
    from .arte_intent_projection import ProjectedIntent,project_strategy_intent,restore_strategy_intent
    from src.backend.backtest_management_structural_guard import capture_management_structural_guard
    from datetime import datetime,timezone
    require_buffered_structural_rejection_exit(buffered)
    if (type(base) is not TypedJournalBatch or base.status!='running'
            or len(base.events)!=1 or len(base.intents)!=1
            or base.first_sequence!=base.last_sequence
            or any(getattr(base,family) for _,family,_,_ in _FAMILIES if family not in ('events','intents'))
            or base.v4_command_lineages):
        raise ValueError('Structural rejection batch requires one exact running typed exit')
    event,parent=base.events[0],base.intents[0]
    fields=buffered.fields
    if (base.run_id!=fields['run_id'] or event['sequence']!=base.first_sequence
            or fields['source_manager_checkpoint_sequence']>=base.first_sequence
            or event['category']!='strategy' or event['entity_type']!='strategy_intent'
            or event['entity_id']!=buffered.intent.intent_id or parent['intent_id']!=event['entity_id']
            or parent['record_id']!=event['record_id']
            or any(row['run_id']!=base.run_id or row['batch_id']!=base.batch_id
                or row['account_id']!=fields['account_id'] or row['event_month']!=fields['event_month']
                for row in (event,parent))):
        raise ValueError('Structural rejection typed envelope differs from exact frozen evidence')
    at=datetime.fromisoformat(str(event['event_time']).replace('Z','+00:00'))
    if at.tzinfo is None:
        at=at.replace(tzinfo=timezone.utc)
    keys=set(project_strategy_intent(buffered.intent).core)-{'event_time'}
    restored=restore_strategy_intent(ProjectedIntent(
        {**{key:parent[key] for key in keys},'event_time':at.astimezone(timezone.utc).isoformat()},()))
    if restored!=buffered.intent:
        raise ValueError('Structural rejection typed intent differs from exact frozen factory')
    row=project_buffered_structural_rejection_exit(buffered,
        parent_record_id=event['record_id'],batch_id=base.batch_id)
    result=V4StructuralRejectionExitBatch(base,MappingProxyType(row))
    _ISSUED[result]=(base,result.evidence,buffered,capture_management_structural_guard(base),
        capture_management_structural_guard(result.evidence))
    return result


def require_structural_rejection_exit_batch(unit):
    from src.backend.backtest_management_structural_guard import require_management_structural_guard
    if type(unit) is not V4StructuralRejectionExitBatch or unit not in _ISSUED:
        raise ValueError('Unissued structural rejection exit batch')
    base,evidence,buffered,base_guard,evidence_guard=_ISSUED[unit]
    require_buffered_structural_rejection_exit(buffered)
    require_management_structural_guard(base_guard,base)
    require_management_structural_guard(evidence_guard,evidence)
    if unit.base is not base or unit.evidence is not evidence:
        raise ValueError('Structural rejection exit batch changed immutable envelope')
    return unit
