"""Bounded SELECT-only v5 recovery; no Keeper-head or order authority."""
import json

from .arte_journal_writer import _literal, typed_contract_row, verify_typed_contract_row
from .arte_native_channel_pending_schema import PENDING, PENDING_SOURCE_ROW, PENDING_EXECUTION_ROW
from .native_channel_manager_snapshot_schema import parent_contract
from .native_channel_manager_snapshot import NativeChannelManagerSnapshotRows, verify_native_channel_manager_rows
from .native_channel_writer_profile import validate_channel_writer_profile
from .original_risk_pending_snapshot import selected_parent_contract
from .strategy_one_management_snapshot import _load_manager_snapshot_children


def load_unattested_native_channel_manager_rows(client, *, run_id, checkpoint_sequence, policy):
    from src.backend.backtest_market_data import assert_select_only
    risk = getattr(client, 'confirmed_original_risk_policy', None)
    validate_channel_writer_profile(policy, risk_policy=risk,
        entry_spread_risk=getattr(client, 'entry_spread_risk_profile', False),
        automatic_ladder=getattr(client, 'automatic_ladder_profile', False),
        ladder_geometry_policy=getattr(client, 'ladder_geometry_policy', None),
        fixed_structural_lot_profile=getattr(client, 'fixed_structural_lot_profile', None))
    if (policy is None or getattr(client, 'native_channel_management_policy', None) != policy
            or type(run_id) is not str or not run_id
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1):
        raise ValueError('Cold channel read requires exact declared client, run and cursor')

    def read(contract, predicate, limit):
        columns = ','.join(f'toString({name}) AS {name}' if 'Decimal(' in kind else name
                           for name, kind in contract.columns)
        sql = assert_select_only(f'SELECT {columns} FROM arte.{contract.name} WHERE {predicate} '
                                 f'LIMIT {limit} FORMAT JSONEachRow')
        return tuple(json.loads(line) for line in client.execute(sql).splitlines() if line.strip())

    scope = f'run_id={_literal(run_id)} AND checkpoint_sequence={checkpoint_sequence}'
    roots = read(parent_contract(), scope, 2)
    if len(roots) != 1:
        raise ValueError('Cold channel read requires exactly one versioned root')
    root = verify_typed_contract_row(parent_contract(), roots[0], stored_utc=True)
    if root['run_id'] != run_id or root['checkpoint_sequence'] != checkpoint_sequence:
        raise ValueError('Cold channel root differs from requested cursor')
    # The inherited image is derived from the v5 root; no v4 root is fabricated
    # as transport authority or selected in Keeper.
    inherited_contract = selected_parent_contract()
    inherited_root = typed_contract_row(inherited_contract,
        {key: root[key] for key, _ in inherited_contract.columns if key != 'content_hash'})
    inherited = _load_manager_snapshot_children(read, client, inherited_root,
        run_id=run_id, checkpoint_sequence=checkpoint_sequence, selected_policy=risk)
    predicate = f"run_id={_literal(run_id)} AND parent_record_id=toUUID({_literal(root['snapshot_id'])})"
    children = []
    for contract, prefix, bound in ((PENDING, 'pending', policy.max_pending_requests),
            (PENDING_SOURCE_ROW, 'source', policy.max_rows_per_family),
            (PENDING_EXECUTION_ROW, 'execution', policy.max_rows_per_family)):
        count = root[f'native_channel_{prefix}_count']
        if type(count) is not int or not 0 <= count <= bound:
            raise ValueError('Cold channel root exceeds declared complete-family bounds')
        observed = read(contract, predicate, count + 1)
        if len(observed) != count:
            raise ValueError('Cold channel family is missing or contains extra rows')
        restored = []
        for row in observed:
            canonical = verify_typed_contract_row(contract, row, stored_utc=True)
            # Restore explicit UTC offsets for the in-memory schema contract;
            # append text to preserve all DateTime64 fractional digits.
            for name, kind in contract.columns:
                if kind.startswith('DateTime64('):
                    canonical[name] += '+00:00'
            restored.append(canonical)
        children.append(tuple(restored))
    return verify_native_channel_manager_rows(NativeChannelManagerSnapshotRows(root, inherited, *children), policy)


def load_attested_native_channel_manager_snapshot(client, keeper, *, run_id,
        checkpoint_sequence, policy, contexts, first_price_source=None,
        native_channel_contexts=()):
    """Attest the journal and stable Keeper head, then reprove all pending facts.

    Returns the image, inherited state and requests. Broker checkpoint and
    declared strategy/OMS admission still remain separate requirements.
    """
    from .strategy_one_management_snapshot import _load_attested_manager_image, restore_manager_snapshot
    from src.backend.backtest_native_channel_manager_recovery import reprove_native_channel_manager_snapshot
    return _load_attested_manager_image(client, keeper, run_id=run_id,
        checkpoint_sequence=checkpoint_sequence, first_price_source=first_price_source,
        native_channel_contexts=native_channel_contexts,
        load_rows=lambda client, **scope: load_unattested_native_channel_manager_rows(
            client, policy=policy, **scope),
        restore_rows=lambda rows: restore_manager_snapshot(rows.inherited),
        verify_pending=lambda rows: reprove_native_channel_manager_snapshot(rows, policy, contexts))
