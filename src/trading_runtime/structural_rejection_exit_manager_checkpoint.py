"""Own historical Manager witness replay against certified native sources.

This is a worker-side read contract. Financial quantity/pending-order checks,
quote source verification, complete preceding-prefix attestation and writer
admission remain independent obligations. No mutable live state is restored.
"""
from dataclasses import dataclass

from .profit_armed_structural_rejection_snapshot import (
    load_unattested_structural_rejection_snapshot,restore_structural_rejection_snapshot)
from .profit_armed_structural_rejection_checkpoint import StructuralRejectionCheckpointBinding,_tree


@dataclass(frozen=True,slots=True)
class StructuralRejectionManagerExitCheckpoint:
    snapshot_id: str
    snapshot_hash: str
    witness: object
    original_entry: object


def load_structural_rejection_exit_manager_checkpoint(client,prefix,row,financial,*,first_price_source):
    from .profit_armed_structural_rejection_profile import require_native_structural_rejection_profile
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .strategy_one_management_snapshot import restore_manager_snapshot
    from src.backend.backtest_profit_armed_structural_rejection_management import (
        certified_structural_rejection_resistance,_price)
    from src.backend.backtest_strategy_episode_activity_source import certified_episode_entry_intent
    profile=require_native_structural_rejection_profile(getattr(client,'structural_rejection_profile',None))
    owner=profile.owner
    sequence=row['source_manager_checkpoint_sequence']
    if (type(prefix) is not V4CommittedPrefix or prefix.status!='running'
            or prefix.run_id!=row['run_id'] or prefix.run_id!=owner.manager.runtime.run_id
            or not sequence<=prefix.last_sequence or first_price_source is not owner.price_authority
            or row['strategy_number']!=owner.manager.contract.strategy_number):
        raise ValueError('Structural rejection manager exit lacks its declared preceding run source')
    rows=load_unattested_structural_rejection_snapshot(client,run_id=prefix.run_id,checkpoint_sequence=sequence)
    root=rows.snapshot
    if (root['snapshot_id']!=row['source_manager_snapshot_id']
            or root['content_hash']!=row['source_manager_snapshot_hash']
            or root['boundary_ms']!=row['boundary_ms']
            or root['session_date']!=owner.manager.runtime.config.anchor_date.isoformat()):
        raise ValueError('Structural rejection manager exit differs from exact own checkpoint reference')
    inherited=restore_manager_snapshot(rows.inherited)
    proposals=dict(inherited.submitted)
    held=dict(inherited.first_held_boundaries)
    bindings={};entries={};geometries={}
    for current in rows.states:
        if current['state_kind']!='current':
            continue
        key=current['account_id'],current['assignment_id'],current['ticker']
        proposal=proposals.get(key);first=held.get(key)
        if proposal is None or type(first) is not int or first<=proposal.boundary_ms:
            raise ValueError('Structural rejection manager lacks original held entry anchors')
        entry=certified_episode_entry_intent(first_price_source,proposal,
            session_date=owner.manager.runtime.config.anchor_date)
        entries[key]=entry
        bindings[key]=StructuralRejectionCheckpointBinding(owner.lookup.sources[key[2]],
            owner.declaration.policy,entry.intent_id,key[0],owner.origin_us,first,
            _price(proposal.reference_ask),_price(proposal.initial_stop),sequence,current['last_decision_boundary_ms'])
    cross_index={}
    for link in rows.links:
        if link['state_kind']=='current' and link['bar_role']=='cross':
            key=link['account_id'],link['assignment_id'],link['ticker']
            cross_index.setdefault(key,[]).append(link['boundary_ms'])
    for level in rows.levels:
        key=level['account_id'],level['assignment_id'],level['ticker']
        crosses=cross_index.get(key,())
        if len(crosses)!=1:
            raise ValueError('Structural rejection frozen level lacks exact source cross clock')
        selected=certified_structural_rejection_resistance(owner,ticker=key[2],cross_boundary_ms=crosses[0])
        if selected is None:
            raise ValueError('Structural rejection level lacks certified causal crossed geometry')
        geometries[key]=selected[1]
    decoded=restore_structural_rejection_snapshot(rows,expected_bindings=bindings,
        declaration=owner.declaration,expected_geometries=geometries)
    # Full role observations are verified, including winners' prior arm/cross
    # history and activity. A valid final candle cannot hide altered history.
    for key,state,_,witness,_,_ in decoded:
        observations=(state.last_bar,state.arm,state.cross,state.cross_prior,*state.rejections,
            *(witness.activity if witness is not None else ()))
        if witness is not None:
            predecessor=witness.predecessor
            observations+= (predecessor.last_bar,predecessor.arm,predecessor.cross,
                predecessor.cross_prior,*predecessor.rejections)
        for bar in observations:
            if bar is not None:
                expected=owner.lookup.bar_at(key[2],bar.boundary_ms)
                if expected is None or _tree(expected)!=_tree(bar):
                    raise ValueError('Structural rejection saved observation differs from certified completed source')
    key=financial.account_id,financial.assignment_id,financial.ticker
    selected=tuple(item for item in decoded if item[0]==key)
    if (len(selected)!=1 or selected[0][3] is None
            or entries[key].intent_id!=row['source_entry_intent_id']
            or selected[0][3].decision_boundary_ms!=row['boundary_ms']
            or selected[0][3].quote.bid_int!=row['reference_bid_int']):
        raise ValueError('Structural rejection exit lacks its exact replayed firing witness')
    return StructuralRejectionManagerExitCheckpoint(root['snapshot_id'],root['content_hash'],
        selected[0][3],entries[key])
