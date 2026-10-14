"""Certified predecessor readers for the launcher's chronological market lanes."""
from datetime import date,datetime,timedelta
from . import indicator_state,v7,source,price_inputs
from .contracts import digest
from .publication import Scope


def _context(parent,listing_key,day,recipe_hash,day_sources):
    previous=date.fromisoformat(str(parent['session_date']))
    if int(parent['listing_key'])!=listing_key or previous>=day:
        raise ValueError('Market predecessor identity/date differs')
    if source.exchange_sessions(previous+timedelta(days=1),day)!=(day,):
        raise ValueError('Market predecessor cannot skip required exchange sessions')
    context=parent.get('state_context')
    if (not context or context['recipe_hash']!=recipe_hash
            or context['source_hash']!=day_sources.get(previous,day_sources.get(str(previous)))):
        raise ValueError('Market predecessor recipe/source differs from frozen context')
    return previous,context


def indicators(query,database,claim,parent,listing_key,day,recipe_hash,day_sources,rules,*,frames=None):
    """Load the selected certified state rows, without rereading market bars."""
    from pipelines.market_sip.events.market_day_sql import FRAMES
    frames=FRAMES if frames is None else frames
    previous,context=_context(parent,listing_key,day,recipe_hash,day_sources)
    if parent['product'] not in ('technical','technical_state'):
        raise ValueError('Wrong indicator predecessor product')
    receipt=parent.get('state_receipt',parent)
    scope=Scope(str(receipt['dataset_id']),previous,listing_key,'technical_state',receipt['parent_hash'])
    loaded=indicator_state.load(query,database,claim,scope,recipe_hash,context['predecessor_hash'],
        source_hash=context['source_hash'],rules_hash=digest(rules),frames=frames)
    if loaded is None or loaded['receipt']!=receipt:
        raise ValueError('Indicator predecessor differs from its certified dependency')
    return loaded['state']


def indicator_continuation(query,database,claim,parent,listing_key,day,recipe_hash,day_sources,rules,corporate_scope,*,frames=None):
    """Resolve and scale a predecessor together with its adjustment lineage."""
    from .adjustments import resolve,indicator_state as scale
    state=indicators(query,database,claim,parent,listing_key,day,recipe_hash,day_sources,rules,frames=frames)
    previous=date.fromisoformat(str(parent['session_date']))
    adjustment=resolve(query,database,claim,corporate_scope,listing_key,previous,day)
    return dict(state=scale(state,adjustment,frames=frames),predecessor_hash=parent['output_hash'],adjustment=adjustment)


def structural(query,database,claim,parent,listing_key,day,ticker,recipe_hash,day_sources,*,
               corporate_scope=None,split_factor=None,split_evidence=()):
    """Restore prior AH and roll once; adjustments require certified evidence.

    Split resolution belongs to the corporate evidence producer. Even a
    factor of one needs its coverage proof here; absent evidence never means
    that the security had no corporate action.
    """
    previous,context=_context(parent,listing_key,day,recipe_hash,day_sources)
    if parent['product']!='v7' or context['ticker']!=ticker:
        raise ValueError('V7 predecessor ticker/product differs; a certified rename transition is required')
    adjustment=None
    if corporate_scope is not None:
        from .adjustments import resolve
        adjustment=resolve(query,database,claim,corporate_scope,listing_key,previous,day)
        split_factor=adjustment.factor;split_evidence=(adjustment.evidence_hash,)
    elif not database.startswith('certified_research_test_'):
        raise ValueError('Certified corporate adjustment scope is required')
    if not split_evidence:raise ValueError('Certified corporate adjustment coverage is required')
    close=price_inputs.calendar_close(previous)
    if datetime.fromisoformat(context['regular_close'])!=close:
        raise ValueError('Predecessor calendar boundary differs')
    scope=Scope(str(parent['dataset_id']),previous,listing_key,'v7',parent['parent_hash'])
    bars=Scope(scope.dataset_id,previous,listing_key,'bars',context['bars_parent_hash'])
    checkpoint,_=v7.load_from_bars(query,database,claim,scope,bars,recipe_hash,
        context['predecessor_hash'],ticker,'AH',regular_close=close)
    authority=v7.StartAuthority.rollover(checkpoint,day,split_factor=split_factor,split_evidence=split_evidence)
    if adjustment is not None:
        from dataclasses import replace
        authority=replace(authority,adjustment_hash=adjustment.evidence_hash)
    return authority
