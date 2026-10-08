"""Select immutable exit references only after the same native checkpoint fence."""
from types import MappingProxyType


def confirm_liquidity_fade_checkpoint_sources(client, manager_keeper, broker_keeper, requests,
                                             receipt, *, run_id, first_price_source):
    """Bind completed requests to both attested Keeper-selected snapshot roots.

    This cold confirmation submits no orders and grants no writer admission.
    Full entry, broker/OMS and market verification remains mandatory at native
    publication. No fill time or current price substitutes for first-held state.
    """
    from src.backend.backtest_strategy_one_management import LiquidityFadeCheckpointRequest
    from src.backend.backtest_typed_publisher import TypedBacktestReceipt
    from .strategy_one_management_snapshot import (
        ManagerSnapshotHead, load_attested_manager_snapshot, load_unattested_manager_snapshot_rows,
    )
    from .strategy_one_broker_match_snapshot import BrokerMatchHead, load_attested_broker_match_snapshot
    from .strategy_liquidity_fade_source import validate_liquidity_fade_state
    from .arte_liquidity_fade_failure_v4 import validate_liquidity_checkpoint_reference
    if (type(requests) is not tuple or not 0 < len(requests) <= 65_536
            or any(type(request) is not LiquidityFadeCheckpointRequest for request in requests)
            or type(receipt) is not TypedBacktestReceipt or receipt.last_sequence <= 0
            or type(run_id) is not str or not run_id):
        raise ValueError('Liquidity confirmation requires exact pending decisions and native receipt')
    manager_head, broker_head = manager_keeper.read_head(run_id=run_id), broker_keeper.read_head(run_id=run_id)
    if (type(manager_head) is not ManagerSnapshotHead or type(broker_head) is not BrokerMatchHead
            or any(head.run_id != run_id or head.checkpoint_sequence != receipt.last_sequence
                   or head.journal_batch_id != receipt.last_batch_id for head in (manager_head, broker_head))):
        raise ValueError('Liquidity confirmation receipt differs from selected native snapshot heads')
    from .selected_checkpoint_products import current_checkpoint,checkpoint_root
    image=current_checkpoint(client,manager_keeper,run_id=run_id,
        sequence=receipt.last_sequence,first_price_source=first_price_source)
    state=(image.inherited if image is not None else load_attested_manager_snapshot(client, manager_keeper, run_id=run_id,
        checkpoint_sequence=receipt.last_sequence, first_price_source=first_price_source))
    if image is not None:
        from .strategy_one_broker_match_snapshot import load_unattested_broker_match_snapshot
        broker=load_unattested_broker_match_snapshot(client,run_id=run_id,checkpoint_sequence=receipt.last_sequence)
        manager_root=checkpoint_root(client,source=image.source,sequence=receipt.last_sequence)
    else:
        broker = load_attested_broker_match_snapshot(client, broker_keeper, run_id=run_id,
            checkpoint_sequence=receipt.last_sequence, first_price_source=first_price_source)
        manager_root = load_unattested_manager_snapshot_rows(client, run_id=run_id, checkpoint_sequence=receipt.last_sequence).snapshot
    if (manager_root['content_hash'] != manager_head.snapshot_hash
            or broker.snapshot['content_hash'] != broker_head.snapshot_hash
            or manager_root['boundary_ms'] != state.boundary_ms
            or broker.snapshot['boundary_ms'] != state.boundary_ms
            or manager_root['session_date'] != broker.snapshot['session_date']):
        raise ValueError('Liquidity confirmation snapshot roots differ from their attested decision clock')
    refs = dict(source_manager_snapshot_id=manager_root['snapshot_id'],
        source_manager_checkpoint_sequence=receipt.last_sequence,
        source_manager_snapshot_hash=manager_head.snapshot_hash,
        source_broker_snapshot_id=broker.snapshot['snapshot_id'], source_broker_snapshot_hash=broker_head.snapshot_hash)
    validate_liquidity_checkpoint_reference(refs)
    identities, result = set(), []
    for request in requests:
        financial = request.financial
        key = financial.account_id, financial.assignment_id, financial.ticker
        if key in identities or request.witness.boundary_ms != state.boundary_ms:
            raise ValueError('Liquidity confirmation repeats a position or crosses its native boundary')
        identities.add(key)
        validate_liquidity_fade_state(request.witness, state, financial)
        result.append(MappingProxyType({**request.observation_source, **refs}))
    if manager_keeper.read_head(run_id=run_id) != manager_head or broker_keeper.read_head(run_id=run_id) != broker_head:
        raise ValueError('Liquidity checkpoint heads changed before reference selection')
    return tuple(result)
