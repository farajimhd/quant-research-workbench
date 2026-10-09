"""Issued exit handoff from a complete published same-cursor native checkpoint.

Issuance performs worker-side database reads. Requiring the handoff performs
only in-memory checks; Runtime must independently reread its current financial
actors and submit through Portfolio/OMS. This is not an execution authority.
"""
from dataclasses import dataclass
from types import MappingProxyType
from weakref import WeakKeyDictionary

_ISSUED=WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class StructuralRejectionExitConfirmation:
    request: object
    run_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    boundary_ms: int
    manager_snapshot_hash: str


def confirm_structural_rejection_exits(client,session,publication,financial_capture,requests):
    from .profit_armed_structural_rejection_publication import (
        _bound,manager_head_reader,readback_manager_publication,_cursor)
    from .profit_armed_structural_rejection_financial_checkpoint import (
        require_financial_capture,verify_financial_capture)
    from .profit_armed_structural_rejection_snapshot import PARENT
    from src.backend.backtest_profit_armed_structural_rejection_management import (
        require_structural_rejection_request,require_structural_rejection_capture)
    binding=_bound(publication,client=client)
    root=binding[6][PARENT.name][0]
    owner=publication.profile.owner
    image=require_financial_capture(financial_capture,profile=publication.profile)
    captured_owner=require_structural_rejection_capture(financial_capture.state)
    boundary=root['boundary_ms']
    if (captured_owner is not owner or type(requests) is not tuple or not requests
            or requests!=owner.requests(boundary_ms=boundary)
            or financial_capture.state.structural_rejection_requests!=requests
            or financial_capture.sequence!=publication.sequence):
        raise ValueError('Structural rejection confirmation lacks exact captured pending inventory')
    for request in requests:
        require_structural_rejection_request(request,owner=owner)
    reader=manager_head_reader(session)
    before=reader.read_head(run_id=publication.run_id)
    if (before.checkpoint_sequence,before.journal_batch_id,before.snapshot_hash)!=(
            publication.sequence,publication.batch_id,root['content_hash']):
        raise ValueError('Structural rejection exit requires its exact published manager head')
    readback_manager_publication(publication,client=client)
    verify_financial_capture(financial_capture,publication,client=client,session=session)
    _cursor(client,owner,publication.sequence,publication.batch_id,boundary)
    if reader.read_head(run_id=publication.run_id)!=before:
        raise ValueError('Structural rejection manager head changed during confirmation')
    result=[]
    for request in requests:
        require_structural_rejection_request(request,owner=owner)
        account=request.financial.account_id
        if account not in image['portfolio']:
            raise ValueError('Structural rejection captured Portfolio omits requested account')
        reference=MappingProxyType(dict(
            source_manager_checkpoint_sequence=publication.sequence,
            source_manager_snapshot_id=root['snapshot_id'],source_manager_snapshot_hash=root['content_hash'],
            source_broker_snapshot_id=image['broker']['snapshot']['snapshot_id'],
            source_broker_snapshot_hash=image['broker']['snapshot']['content_hash'],
            source_oms_snapshot_id=image['oms']['root']['snapshot_id'],
            source_oms_snapshot_hash=image['oms']['root']['content_hash'],
            source_portfolio_state_hash=image['portfolio'][account]['state_hash']))
        confirmation=StructuralRejectionExitConfirmation(request,publication.run_id,
            publication.sequence,publication.batch_id,boundary,root['content_hash'])
        _ISSUED[confirmation]=(owner,request,publication,financial_capture,before,reference,
            tuple((name,getattr(confirmation,name)) for name in (
                'run_id','checkpoint_sequence','journal_batch_id','boundary_ms','manager_snapshot_hash')))
        result.append(confirmation)
    return tuple(result)


def require_structural_rejection_confirmation(confirmation,*,request=None,runtime=None):
    """No I/O and no cached financial approval; every pending fact stays fresh."""
    from src.backend.backtest_profit_armed_structural_rejection_management import require_structural_rejection_request
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from .profit_armed_structural_rejection_publication import _bound
    from .profit_armed_structural_rejection_financial_checkpoint import require_financial_capture
    from src.backend.backtest_market_data import market_day_boundary
    if type(confirmation) is not StructuralRejectionExitConfirmation or confirmation not in _ISSUED:
        raise ValueError('Unissued structural rejection exit confirmation')
    owner,original,publication,capture,head,reference,image=_ISSUED[confirmation]
    _bound(publication)
    require_financial_capture(capture,profile=publication.profile)
    require_native_structural_rejection_profile(publication.profile,owner=owner)
    require_structural_rejection_request(original,owner=owner)
    if (confirmation.request is not original or request is not None and request is not original
            or any(getattr(confirmation,name)!=value for name,value in image)
            or owner.manager.runtime.run_id!=confirmation.run_id
            or original.witness.decision_boundary_ms!=confirmation.boundary_ms
            or owner._entered.get((original.financial.account_id,original.financial.assignment_id,
                                  original.financial.ticker))!=confirmation.boundary_ms
            or runtime is not None and (owner.manager.runtime is not runtime
                or runtime.last_event_time!=market_day_boundary(runtime.config.anchor_date,confirmation.boundary_ms))):
        raise ValueError('Structural rejection exit confirmation changed request/runtime/frontier')
    return confirmation


def structural_rejection_confirmation_reference(confirmation):
    require_structural_rejection_confirmation(confirmation)
    return dict(_ISSUED[confirmation][5])
