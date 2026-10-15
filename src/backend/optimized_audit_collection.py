"""Collect bounded audit evidence from the existing completed native app path.

No broker, feature producer or alternative backtest lives here. Persisted
semantic intent stages are retained; missing raw intent actions are explicit.
"""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
from types import MappingProxyType, SimpleNamespace

import polars as pl

from .qmd_columnar_history_transport import _owned_worker
from .recorded_program_comparison import read_recorded_program_session_metrics, _check_financial_ledger
from .rule_decision_recovery import read_rule_decision_observation_page_async
from .program_policy_recovery import program_policy_fingerprint
from .typed_backtest_financial_review import load_typed_backtest_financial_evidence
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.trading_runtime.filled_position_state import FilledPositionState, FilledPosition
from src.trading_runtime.program_target_state import ProgramTargetState
from src.trading_runtime.program_trail_state import ProgramTrailState
from src.trading_runtime.rule_decision_basis_projection import verify_rule_decision_prepared_source, verify_rule_decision_intent_records


def _frame(rows, order):
    # Native ledger values may exceed Decimal128 precision (80-digit weighted
    # basis). Preserve exact text instead of silently rounding to float/decimal.
    decimal_names = {name for row in rows for name, value in row.items() if isinstance(value, Decimal)}
    rows = [{name: str(value) if name in decimal_names and value is not None else value
             for name, value in row.items()} for row in rows]
    frame = pl.DataFrame(rows, infer_schema_length=None)
    if rows and decimal_names:
        # Explicit FP64 projections support comparison to the source model;
        # exact decimal text remains alongside each projection.
        frame = frame.with_columns(pl.col(name).cast(pl.Float64).alias(name + '__float64')
                                   for name in sorted(decimal_names))
    return frame.sort(order) if rows else frame


def project_native_execution_audit(*, prefix, packet, blocks, source, financial, decisions,
                                   recorded_metrics, maximum_rows, intent_records=None):
    """Project already read native facts; exact source/ledger checks fail closed.

    Position snapshots replay committed fills/commissions in journal sequence,
    never from semantic intents. OMS orders are observed snapshots, not a claim
    to contain every historical order lifecycle event.
    """
    if type(maximum_rows) is not int or maximum_rows < 1:
        raise ValueError('Native audit requires explicit positive row bound')
    actor = packet.actor
    if program_policy_fingerprint(actor) != packet.state_hash:
        raise ValueError('Native audit recovered policy changed')
    _check_financial_ledger(financial, actor, prefix)
    expected = 2 * len(financial['fills']) + len(financial['commissions']) + 4 * len(decisions)
    if expected > maximum_rows:
        raise ValueError('Native audit exceeds explicit evidence row bound')
    by_listing = {}
    for block in blocks:
        for listing in block.history_hashes:
            if listing in by_listing and by_listing[listing] is not block:
                raise ValueError('Native audit prepared listing is ambiguous')
            by_listing[listing] = block
    events = []
    for kind, rows in (('fill', financial['fills']), ('fee', financial['commissions'])):
        events.extend((int(row['sequence']), kind, row) for row in rows)
    if len({sequence for sequence, _, _ in events}) != len(events):
        raise ValueError('Native audit repeats financial event sequence')
    events.sort(key=lambda row: row[0])
    ledger = FilledPositionState(maximum_executions=max(1, len(financial['fills'])))
    targets = ProgramTargetState(actor.program_release.header['target_fraction'])
    trails = ProgramTrailState()
    event_offset = 0
    fills, fees, transitions, positions, rules, selected, orders, observations = [], [], [], [], [], [], [], []
    def apply_event(event):
        sequence, kind, row = event
        identity = row['execution_id']
        if kind == 'fill':
            fact = actor.execution_state._fills[identity]
            before = ledger.positions.get(fact.key) or FilledPosition(*fact.key, fact.ticker)
            after = ledger.apply_fill(fact)
            targets.apply_new_fill(fact, before, after)
            trails.apply_new_fill(fact, before, after)
            fills.append(dict(sequence=sequence, **asdict(fact)))
            transitions.append(dict(sequence=sequence, listing_id=fact.listing_id,
                execution_id=identity, at=fact.filled_at,
                before_quantity=before.quantity,
                after_quantity=after.quantity, after_average_price=after.average_price,
                realized_gross_pnl=after.realized_gross_pnl))
        else:
            fact = actor.execution_state._fees[identity]
            ledger.apply_fee(fact)
            fees.append(dict(sequence=sequence, **asdict(fact)))
    intent_records = {} if intent_records is None else intent_records
    actions = []
    previous = 0
    for parent, children, observation in decisions:
        if not previous < parent['decision_sequence'] <= prefix.last_sequence:
            raise ValueError('Native audit decisions are unordered or overlap')
        previous = parent['decision_sequence']
        assignment = actor._by_assignment.get(parent['assignment_id'])
        block = by_listing.get(parent['listing_id'])
        if assignment is None or block is None:
            raise ValueError('Native audit decision lacks prepared assignment')
        verify_rule_decision_prepared_source(parent, children, release=actor.program_release,
            block=block, assignment=assignment, source=source)
        # The basis sequence is the pre-selection financial state authority.
        while event_offset < len(events) and events[event_offset][0] <= parent['basis_sequence']:
            apply_event(events[event_offset])
            event_offset += 1
        key = (assignment.account_id, actor.strategy_id, assignment.assignment_id, parent['listing_id'])
        position = ledger.positions.get(key) or FilledPosition(*key, assignment.ticker)
        if position.last_management_fill_at is not None and position.last_management_fill_at > observation.observed_at:
            raise ValueError('Native audit sequence includes a future fill at decision boundary')
        common = dict(decision_id=parent['decision_id'], listing_id=parent['listing_id'],
            assignment_id=assignment.assignment_id, account_id=assignment.account_id,
            boundary_at=observation.observed_at, basis_sequence=parent['basis_sequence'],
            decision_sequence=parent['decision_sequence'])
        positions.append(dict(**common, quantity=position.quantity, average_price=position.average_price,
            adds=position.adds, opened_at=position.opened_at,
            last_management_fill_at=position.last_management_fill_at,
            realized_gross_pnl=position.realized_gross_pnl, fees=position.fees,
            net_realized_pnl=position.net_realized_pnl))
        market, mask = block.row_for(parent['listing_id'], parent['observed_ordinal'])
        boundary = SimpleNamespace(close=market['reference_close'], available_at=market['available_at'],
            boundary_at=observation.observed_at,
            rules={stage: mask[stage] for stage in actor.program_release.programs},
            validity={stage: mask[stage + '__valid'] for stage in actor.program_release.programs})
        trails.observe(boundary, position,
            minimum_age_seconds=actor.program_release.minimum_age_seconds['trail'])
        positions[-1].update(target_price=targets.target_for(position),
            causal_trail_water=trails.water_for(position))
        rules.append(dict(**common, available_at=market['available_at'],
            reference_close=market['reference_close'],
            **{name: mask[name] for stage in actor.program_release.programs for name in (stage, stage + '__valid')}))
        linked = verify_rule_decision_intent_records(parent, children,
            tuple(intent_records[child['strategy_intent_id']] for child in children))
        for child, record in zip(children, linked, strict=True):
            intent = record.intent
            actions.append(dict(**common, intent_id=intent.intent_id, stage=child['stage'],
                action=intent.action, intent_sequence=record.sequence,
                quantity=intent.quantity, reference_price=intent.reference_price,
                reason=intent.reason))
        selected.append(dict(**common, terminal=bool(parent['terminal']),
            selection_status=parent['selection_status'], stages=[child['stage'] for child in children],
            intent_ids=[child['strategy_intent_id'] for child in children]))
        basis_identity = sha256(json.dumps(dict(position=asdict(position),
            oms=asdict(observation), release_hash=block.release_hash,
            target_price=targets.target_for(position), causal_trail_water=trails.water_for(position),
            target_entry_intent=targets._entry_intents.get(key),
            target_advanced_intents=sorted(targets._advanced_intents.get(key, ())),
            add_intents=sorted(ledger._add_intents.get(key, ())),
            execution_risk_hash=(actor.execution_risk.header['content_hash'] if actor.execution_risk is not None else None),
            basis_sequence=parent['basis_sequence'], decision_id=parent['decision_id']),
            default=str, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        selected[-1].update(native_basis_identity=basis_identity,
            state_identity_complete=True,
            state_identity_scope='native pre-selection ledger, OMS observations, target/trail shared replay and immutable policy/risk hashes; no cross-model state equivalence implied')
        observations.append(dict(**common, pending_acquisition=observation.pending_acquisition,
            pending_reduction=observation.pending_reduction,
            unobserved_order_ids=list(observation.unobserved_order_ids),
            high_water_price=observation.high_water_price))
        orders.extend(dict(**common, **asdict(order)) for order in observation.working_orders)
        if expected + len(orders) > maximum_rows:
            raise ValueError('Native audit order snapshots exceed explicit row bound')
    for event in events[event_offset:]:
        apply_event(event)
    # Compare replayed terminal ledger to its previously verified recovered actor.
    if (dict(ledger.finalized_positions()) != dict(actor.execution_state.finalized_positions())
            or targets._targets != actor.target_state._targets
            or targets._entry_intents != actor.target_state._entry_intents
            or targets._advanced_intents != actor.target_state._advanced_intents
            or trails._water != actor.trail_state._water):
        raise ValueError('Native audit financial sequence replay differs from recovered state')
    if expected + len(orders) + len(actions) > maximum_rows:
        raise ValueError('Native audit semantic actions exceed explicit row bound')
    return MappingProxyType(dict(run_id=prefix.run_id, committed_sequence=prefix.last_sequence,
        complete=True, recorded_metrics=recorded_metrics,
        rule_outputs=_frame(rules, ['boundary_at', 'listing_id']),
        decisions=_frame(selected, ['boundary_at', 'listing_id']),
        intent_actions=_frame(actions, ['boundary_at', 'listing_id', 'intent_sequence']),
        pre_decision_positions=_frame(positions, ['boundary_at', 'listing_id']),
        oms_observations=_frame(observations, ['boundary_at', 'listing_id']),
        observed_orders=_frame(orders, ['boundary_at', 'listing_id', 'broker_order_id']),
        fills=_frame(fills, ['sequence']), fees=_frame(fees, ['sequence']),
        position_transitions=_frame(transitions, ['sequence']),
        unavailable=dict(membership='use independently certified Signal Stream membership; not reconstructed from intents',
            full_order_lifecycle='working order snapshots only'),
        qualification='native recorded evidence; no optimizer-to-broker causal attribution'))


async def collect_native_execution_audit(reader_factory, prefix, packet, blocks, source, reference, *,
        day, financial_page_size, maximum_financial_rows, decision_page_size,
        maximum_decisions, maximum_observation_children, maximum_measure_rows,
        maximum_audit_rows):
    """Verify native completed session first, then collect its exact fixed prefix."""
    recorded = await read_recorded_program_session_metrics(reader_factory, prefix, packet, blocks, source,
        reference, day=day, financial_page_size=financial_page_size,
        maximum_financial_rows=maximum_financial_rows, decision_page_size=decision_page_size,
        maximum_decisions=maximum_decisions, maximum_observation_children=maximum_observation_children,
        maximum_measure_rows=maximum_measure_rows)
    def financial_read():
        client = reader_factory()
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Native audit requires SELECT-only client')
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError('Native audit prefix changed before read')
            result = load_typed_backtest_financial_evidence(client, prefix.run_id,
                page_size=financial_page_size, maximum_rows=maximum_financial_rows)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError('Native audit prefix changed after read')
            return result
        finally:
            client.close()
    financial = await _owned_worker(financial_read)
    decisions, after, children = [], 0, 0
    while True:
        page = await read_rule_decision_observation_page_async(reader_factory, prefix,
            after_sequence=after, limit=decision_page_size, maximum_children=maximum_observation_children)
        if len(decisions) + len(page) > maximum_decisions:
            raise ValueError('Native audit decision bound exceeded')
        for parent, _, observation in page:
            if not after < parent['decision_sequence'] <= prefix.last_sequence:
                raise ValueError('Native audit page cursor is unordered')
            after = parent['decision_sequence']
            children += len(observation.working_orders) + len(observation.unobserved_order_ids)
            if children > maximum_observation_children:
                raise ValueError('Native audit observation bound exceeded')
        decisions.extend(page)
        if len(page) < decision_page_size:
            break
    intent_records = await _owned_worker(_read_intent_records, reader_factory, prefix, tuple(decisions), maximum_audit_rows)
    source_cursor = source.typed_source_cursor()
    result = await _owned_worker(project_native_execution_audit, prefix=prefix, packet=packet,
        blocks=blocks, source=source, financial=financial, decisions=tuple(decisions),
        recorded_metrics=recorded, maximum_rows=maximum_audit_rows, intent_records=intent_records)
    if (source.typed_source_cursor() != source_cursor
            or program_policy_fingerprint(packet.actor) != packet.state_hash):
        raise ValueError('Native audit authorities changed during collection')
    return result



def _read_intent_records(reader_factory, prefix, decisions, maximum_rows):
    """Resolve actual normalized intent actions; stages never stand in for actions."""
    from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
    from src.trading_runtime.arte_journal_writer import _rows, _literal, _committed_batch_filter
    wanted = [child['strategy_intent_id'] for _, children, _ in decisions for child in children]
    if len(wanted) > maximum_rows or len(wanted) != len(set(wanted)):
        raise ValueError('Native audit semantic intent bound or uniqueness differs')
    if not wanted:
        return {}
    client = reader_factory()
    try:
        if client.execute("SELECT getSetting('readonly')").strip() != '1':
            raise ValueError('Native semantic audit requires SELECT-only client')
        if load_verified_v4_prefix(client, prefix.run_id) != prefix:
            raise ValueError('Native intent prefix changed before read')
        recovered = {}
        for offset in range(0, len(wanted), 500):
            chunk = wanted[offset:offset + 500]
            events = _rows(client, "SELECT record_id,entity_id FROM arte.trading_event_v1 "
                f"WHERE run_id={_literal(prefix.run_id)} AND sequence<={prefix.last_sequence} "
                "AND category='strategy' AND entity_type='strategy_intent' "
                f"AND entity_id IN ({','.join(_literal(value) for value in chunk)}) "
                f"{_committed_batch_filter(prefix)} LIMIT {len(chunk) + 1} FORMAT JSONEachRow")
            if (len(events) != len(chunk) or {row['entity_id'] for row in events} != set(chunk)
                    or len({row['record_id'] for row in events}) != len(events)):
                raise ValueError('Native audit exact intent event coverage differs')
            records = load_committed_strategy_intent_page(client, prefix,
                record_ids=tuple(row['record_id'] for row in events), limit=len(chunk))
            if len(records) != len(chunk) or {row.intent.intent_id for row in records} != set(chunk):
                raise ValueError('Native audit normalized intent coverage differs')
            recovered.update((row.intent.intent_id, row) for row in records)
        for parent, children, _ in decisions:
            verify_rule_decision_intent_records(parent, children,
                tuple(recovered[child['strategy_intent_id']] for child in children))
        if load_verified_v4_prefix(client, prefix.run_id) != prefix:
            raise ValueError('Native intent prefix changed after read')
        return recovered
    finally:
        client.close()
