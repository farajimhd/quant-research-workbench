"""Prepare an arming reference only after native checkpoint confirmation."""
from dataclasses import dataclass
from uuid import UUID
from .strategy_profit_giveback_arm import ProfitArmCandidate, profit_arm_candidate


@dataclass(frozen=True, slots=True)
class ProfitArmReference:
    candidate: ProfitArmCandidate
    snapshot_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    snapshot_hash: str


def confirm_profit_arm_reference(client, keeper, candidate, financial, receipt, *, run_id, first_price_source=None):
    """Use actual attested recovery, not projected rows or an arbitrary token.

    Caller must have awaited the native checkpoint publisher. No order is
    submitted, and native Strategy 31 capture/recovery integration is required.
    """
    from src.backend.backtest_typed_publisher import TypedBacktestReceipt
    from .strategy_one_management_snapshot import (
        ManagerSnapshotHead, load_attested_manager_snapshot,
    )
    if (type(candidate) is not ProfitArmCandidate or type(receipt) is not TypedBacktestReceipt
            or type(run_id) is not str or not run_id
            or type(receipt.last_sequence) is not int or receipt.last_sequence<=0):
        raise ValueError('Profit arming requires exact native checkpoint receipt')
    head=keeper.read_head(run_id=run_id)
    if (type(head) is not ManagerSnapshotHead or head.run_id!=run_id
            or head.checkpoint_sequence!=receipt.last_sequence
            or head.journal_batch_id!=receipt.last_batch_id):
        raise ValueError('Profit arming receipt differs from selected checkpoint')
    state=load_attested_manager_snapshot(client,keeper,run_id=run_id,
        checkpoint_sequence=receipt.last_sequence,first_price_source=first_price_source)
    actual=profit_arm_candidate(state,financial,already_checkpointed=False)
    if actual!=candidate or keeper.read_head(run_id=run_id)!=head:
        raise ValueError('Profit arming checkpoint or position changed during confirmation')
    # Snapshot identity is deterministic from the native protection root; the
    # attested reader already verified the complete manager snapshot hash.
    # A candidate boundary alone cannot supply its calendar session. The
    # writer's source cursor is intentionally not parsed as session authority.
    # Read the already verified root selected at exactly this checkpoint.
    from .strategy_one_management_snapshot import load_unattested_manager_snapshot_rows
    rows=load_unattested_manager_snapshot_rows(client,run_id=run_id,checkpoint_sequence=receipt.last_sequence)
    if rows.snapshot['content_hash']!=head.snapshot_hash or rows.snapshot['boundary_ms']!=candidate.boundary_ms:
        raise ValueError('Profit arming reference differs from attested snapshot root')
    identity=str(UUID(str(rows.snapshot['snapshot_id'])))
    if keeper.read_head(run_id=run_id)!=head:
        raise ValueError('Profit arming checkpoint changed before reference selection')
    return ProfitArmReference(candidate,identity,receipt.last_sequence,receipt.last_batch_id,head.snapshot_hash)
