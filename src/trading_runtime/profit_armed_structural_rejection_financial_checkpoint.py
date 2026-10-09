"""Actor-issued complete financial capture and same-cursor persisted joins.

No caller financial boolean or requested quantity issues this capability.
Captures come from the actual shared actors, and every persisted inventory is
compared before selected manager head publication. Runtime still owns order
admission and must reread assignment permissions and held truth after waiting.
"""
from dataclasses import dataclass,asdict
from datetime import timezone
from weakref import WeakKeyDictionary
import json

_CAPTURES=WeakKeyDictionary()
_VERIFIED=WeakKeyDictionary()


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class StructuralRejectionFinancialCapture:
    profile: object
    state: object
    sequence: int
    image_json: str


@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class StructuralRejectionFinancialVerification:
    publication: object
    capture: StructuralRejectionFinancialCapture
    broker_head: object
    oms_head: object


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def issue_financial_capture(profile,state,*,sequence):
    """Actor-thread only, before queueing a frozen checkpoint image."""
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from src.backend.backtest_profit_armed_structural_rejection_management import require_structural_rejection_capture
    from .runtime import TradingRuntime,RunMode
    from .simulated_broker import SimulatedBrokerAdapter
    from .order_management import OrderManagementEngine
    from .portfolio import PortfolioManagementEngine
    from .arte_portfolio_snapshot import prepare_captured_portfolio_snapshot,_snapshot_rows,_state_hash
    from .strategy_one_broker_match_snapshot import project_broker_match_snapshot
    from .strategy_one_oms_observation_snapshot import project_oms_observation_snapshot
    from src.backend.backtest_market_data import market_day_boundary
    from .profit_armed_structural_rejection_snapshot import project_structural_rejection_snapshot
    require_native_structural_rejection_profile(profile)
    owner=require_structural_rejection_capture(state)
    require_native_structural_rejection_profile(profile,owner=owner)
    runtime=owner.manager.runtime
    at=market_day_boundary(runtime.config.anchor_date,state.boundary_ms)
    if (type(runtime) is not TradingRuntime or runtime.config.mode is not RunMode.BACKTEST
            or type(runtime.broker) is not SimulatedBrokerAdapter
            or type(runtime.order_manager) is not OrderManagementEngine
            or type(runtime.portfolio) is not PortfolioManagementEngine
            or runtime.last_event_time!=at or type(sequence) is not int or sequence<1
            or type(runtime.config.account_ids) is not tuple or not runtime.config.account_ids
            or len(set(runtime.config.account_ids))!=len(runtime.config.account_ids)):
        raise ValueError('Structural rejection capture lacks actual same-boundary financial actors')
    common=dict(run_id=runtime.run_id,session_date=runtime.config.anchor_date,
                checkpoint_sequence=sequence,boundary_ms=state.boundary_ms)
    broker=project_broker_match_snapshot(**common,state=runtime.broker.broker_match_snapshot_state())
    oms=project_oms_observation_snapshot(**common,groups=runtime.order_manager.capture_observed_broker_states())
    accounts={}
    for account in sorted(runtime.config.account_ids):
        captured=runtime.portfolio.capture_recovery_snapshot(account,state_revision=sequence,snapshot_at=at)
        prepared=prepare_captured_portfolio_snapshot(captured)
        families=_snapshot_rows(runtime.run_id,account,sequence,prepared.snapshot_month,prepared.rows)
        accounts[account]=dict(state_hash=_state_hash(families),snapshot_at=at.astimezone(timezone.utc).isoformat())
    image=dict(broker=asdict(broker),oms=asdict(oms),portfolio=accounts,
               run_id=runtime.run_id,sequence=sequence,boundary_ms=state.boundary_ms,
               session_date=runtime.config.anchor_date.isoformat(),
               strategy_id=runtime.config.strategy_id,revision=runtime.config.strategy_revision,
               manager_hash=project_structural_rejection_snapshot(run_id=runtime.run_id,
                   session_date=runtime.config.anchor_date,checkpoint_sequence=sequence,state=state).snapshot['content_hash'])
    result=StructuralRejectionFinancialCapture(profile,state,sequence,_canonical(image))
    _CAPTURES[result]=(profile,state,sequence,result.image_json,runtime)
    return result


def require_financial_capture(capture,*,profile=None,state=None):
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from src.backend.backtest_profit_armed_structural_rejection_management import require_structural_rejection_capture
    if type(capture) is not StructuralRejectionFinancialCapture or capture not in _CAPTURES:
        raise ValueError('Unissued structural rejection financial capture')
    issued,original,sequence,wire,runtime=_CAPTURES[capture]
    require_native_structural_rejection_profile(issued)
    require_structural_rejection_capture(original)
    if (capture.profile is not issued or capture.state is not original
            or capture.sequence!=sequence or capture.image_json!=wire
            or profile is not None and profile is not issued
            or state is not None and state is not original
            or issued.owner.manager.runtime is not runtime):
        raise ValueError('Structural rejection financial capture changed actor/source/image')
    return json.loads(wire)


def verify_financial_capture(capture,publication,*,client,session):
    """Worker/cold boundary; verify full roots/inventories before own head."""
    from .profit_armed_structural_rejection_publication import require_manager_publication,_cursor
    from .profit_armed_structural_rejection_snapshot import PARENT
    from .strategy_one_broker_match_snapshot import ManagedBrokerMatchHeadReader,load_attested_broker_match_snapshot
    from .strategy_one_oms_observation_snapshot import ManagedOmsObservationHeadReader,load_attested_oms_observation_snapshot
    from .arte_portfolio_snapshot import load_portfolio_snapshot
    from .arte_journal_writer import load_typed_run_context
    image=require_financial_capture(capture,profile=publication.profile)
    families=require_manager_publication(publication,client=client)
    root=families[PARENT.name][0];owner=publication.profile.owner
    if (publication.sequence!=capture.sequence or image['run_id']!=publication.run_id
            or image['boundary_ms']!=root['boundary_ms'] or image['session_date']!=root['session_date']
            or image['manager_hash']!=root['content_hash']):
        raise ValueError('Structural rejection financial capture differs from manager cursor')
    _cursor(client,owner,publication.sequence,publication.batch_id,root['boundary_ms'])
    context=load_typed_run_context(client,publication.run_id)
    accounts=tuple(sorted(image['portfolio']))
    if (context.get('run_id')!=publication.run_id or context.get('mode')!='backtest'
            or context.get('strategy_id')!=image['strategy_id']
            or context.get('strategy_revision')!=image['revision']
            or context.get('session_date')!=image['session_date']
            or type(context.get('account_ids')) is not tuple
            or tuple(sorted(context['account_ids']))!=accounts):
        raise ValueError('Structural rejection financial context differs from actual configured accounts')
    broker_reader=ManagedBrokerMatchHeadReader(session);oms_reader=ManagedOmsObservationHeadReader(session)
    broker_head=broker_reader.read_head(run_id=publication.run_id)
    oms_head=oms_reader.read_head(run_id=publication.run_id)
    if any(head is None or (head.checkpoint_sequence,head.journal_batch_id)!=(publication.sequence,publication.batch_id)
           for head in (broker_head,oms_head)):
        raise ValueError('Structural rejection financial heads differ from manager cursor')
    broker=load_attested_broker_match_snapshot(client,broker_reader,run_id=publication.run_id,
        checkpoint_sequence=publication.sequence,first_price_source=owner.price_authority)
    oms=load_attested_oms_observation_snapshot(client,oms_reader,run_id=publication.run_id,
        checkpoint_sequence=publication.sequence,first_price_source=owner.price_authority)
    if (_canonical(asdict(broker))!=_canonical(image['broker'])
            or _canonical(asdict(oms))!=_canonical(image['oms'])):
        raise ValueError('Structural rejection complete Broker/OMS inventory changed from actual actors')
    for account in accounts:
        actual=load_portfolio_snapshot(client,run_id=publication.run_id,account_id=account,
                                      state_revision=publication.sequence)
        if (actual is None or actual['state_hash']!=image['portfolio'][account]['state_hash']
                or actual['snapshot_at']!=image['portfolio'][account]['snapshot_at']):
            raise ValueError('Structural rejection complete Portfolio state differs from actual actor capture')
    _cursor(client,owner,publication.sequence,publication.batch_id,root['boundary_ms'])
    if (broker_reader.read_head(run_id=publication.run_id)!=broker_head
            or oms_reader.read_head(run_id=publication.run_id)!=oms_head):
        raise ValueError('Structural rejection financial heads changed during verification')
    result=StructuralRejectionFinancialVerification(publication,capture,broker_head,oms_head)
    _VERIFIED[result]=(publication,capture,client,session,broker_head,oms_head)
    return result


def require_financial_verification(receipt,*,publication,client,session):
    if type(receipt) is not StructuralRejectionFinancialVerification or receipt not in _VERIFIED:
        raise ValueError('Unissued structural rejection financial verification')
    expected=_VERIFIED[receipt]
    if (receipt.publication is not publication or receipt.capture is not expected[1]
            or expected[0] is not publication or expected[2] is not client or expected[3] is not session
            or receipt.broker_head!=expected[4] or receipt.oms_head!=expected[5]):
        raise ValueError('Foreign structural rejection financial verification')
    # A prior successful verification cannot authorize a later boundary:
    # reread the journal cursor, run context and all three complete actor
    # inventories immediately before publication, including Portfolio rows.
    fresh=verify_financial_capture(receipt.capture,publication,client=client,session=session)
    if fresh.broker_head!=receipt.broker_head or fresh.oms_head!=receipt.oms_head:
        raise ValueError('Structural rejection financial heads changed before publication')
    return receipt
