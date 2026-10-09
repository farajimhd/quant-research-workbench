"""Issued exact native exit-row image for a selected Backtest writer.

Issuance/reverification perform full cold reads. Requiring an image only
checks identity and every descendant; it is not a lease or order capability.
"""
from dataclasses import dataclass
from weakref import WeakKeyDictionary

_ISSUED=WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class VerifiedStructuralRejectionExitPublication:
    unit: object
    profile: object
    rows: tuple


def _current(client,unit,prefix,first_price_source):
    from .arte_journal_commit_v4 import load_writer_v4_snapshot_prefix,V4CommittedPrefix,verified_batch_predecessor
    if (type(prefix) is not V4CommittedPrefix or prefix.status!='running'
            or prefix.run_id!=unit.base.run_id or not prefix.batch_ids
            or prefix.last_batch_id!=prefix.batch_ids[-1]
            or prefix.last_sequence+1!=unit.base.first_sequence
            or prefix.last_batch_id!=unit.base.prior_batch_id):
        raise ValueError('Structural rejection exit lacks exact verified preceding journal head')
    actual=load_writer_v4_snapshot_prefix(client,unit.base.run_id,first_price_source=first_price_source)
    if actual is not None and unit.base.batch_id in actual.batch_ids:
        actual=verified_batch_predecessor(client,actual,unit.base.batch_id)
    if actual!=prefix:
        raise ValueError('Structural rejection exit predecessor differs from current native writer head')


def _verified_rows(client,unit,prefix,first_price_source):
    from .structural_rejection_exit_publication import prepare_native_structural_rejection_exit_rows
    _current(client,unit,prefix,first_price_source)
    rows=prepare_native_structural_rejection_exit_rows(client,(dict(unit.evidence),),
        unit.base.intents,unit.base.events,verified_prefix=prefix,first_price_source=first_price_source)
    if type(rows) is not tuple or len(rows)!=1 or dict(rows[0])!=dict(unit.evidence):
        raise ValueError('Structural rejection native verifier changed exact issued row inventory')
    _current(client,unit,prefix,first_price_source)
    return rows


def issue_verified_structural_rejection_exit_publication(client,unit,*,verified_prior_prefix,first_price_source):
    from .structural_rejection_exit_transport import require_structural_rejection_exit_batch
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from src.backend.backtest_management_structural_guard import capture_management_structural_guard
    require_structural_rejection_exit_batch(unit)
    profile=require_native_structural_rejection_profile(getattr(client,'structural_rejection_profile',None))
    if (first_price_source is not profile.owner.price_authority
            or unit.base.run_id!=profile.owner.manager.runtime.run_id
            or unit.evidence['strategy_number']!=profile.owner.manager.contract.strategy_number):
        raise ValueError('Structural rejection exit publication differs from selected certified profile')
    rows=_verified_rows(client,unit,verified_prior_prefix,first_price_source)
    result=VerifiedStructuralRejectionExitPublication(unit,profile,rows)
    _ISSUED[result]=(client,unit,profile,rows,verified_prior_prefix,first_price_source,
        capture_management_structural_guard(rows),capture_management_structural_guard(verified_prior_prefix))
    return result


def require_verified_structural_rejection_exit_publication(context,*,client):
    from .structural_rejection_exit_transport import require_structural_rejection_exit_batch
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from src.backend.backtest_management_structural_guard import require_management_structural_guard
    if type(context) is not VerifiedStructuralRejectionExitPublication or context not in _ISSUED:
        raise ValueError('Unissued structural rejection exit publication')
    issued_client,unit,profile,rows,prefix,source,row_guard,prefix_guard=_ISSUED[context]
    require_structural_rejection_exit_batch(unit)
    require_native_structural_rejection_profile(profile)
    require_management_structural_guard(row_guard,rows)
    require_management_structural_guard(prefix_guard,prefix)
    if (client is not issued_client or context.unit is not unit or context.profile is not profile
            or context.rows is not rows or getattr(client,'structural_rejection_profile',None) is not profile
            or profile.owner.price_authority is not source):
        raise ValueError('Structural rejection exit publication changed exact client/source/image')
    return context


def reverify_structural_rejection_exit_publication(context,*,client):
    require_verified_structural_rejection_exit_publication(context,client=client)
    _,unit,_,rows,prefix,source,*_=_ISSUED[context]
    current=_verified_rows(client,unit,prefix,source)
    if current!=rows:
        raise ValueError('Structural rejection native exit evidence changed before publication')
    require_verified_structural_rejection_exit_publication(context,client=client)
    return context


def verify_structural_rejection_exit_insert(context,*,client,rows,run_id,sequence,batch_id):
    """Exact authorized companion only; generic Keeper dispatch owns the fence."""
    reverify_structural_rejection_exit_publication(context,client=client)
    unit=context.unit
    if (type(rows) is not tuple or rows!=context.rows
            or (run_id,sequence,batch_id)!=(unit.base.run_id,unit.base.last_sequence,unit.base.batch_id)):
        raise ValueError('Structural rejection exit INSERT differs from verified complete row image')
    return context
