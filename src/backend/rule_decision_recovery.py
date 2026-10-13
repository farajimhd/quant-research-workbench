"""Owned asynchronous reads of staged shared decision references.

This reader does not admit a strategy or prove its referenced market/financial
state. Publication and independent causal replay remain required.
"""
from .qmd_columnar_history_transport import _owned_worker
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.rule_decision_basis_projection import load_committed_rule_decision_page
from src.trading_runtime.rule_decision_basis_projection import verify_rule_decision_completion_record
from src.trading_runtime.arte_journal_reader import TypedJournalEvent, load_typed_event_page
from src.trading_runtime.journal_contract import JournalRecord
from src.backend.signal_stream_typed_readback import _normalize
from datetime import datetime
from src.trading_runtime.arte_intent_projection import load_committed_strategy_intent_page
from src.trading_runtime.rule_decision_basis_projection import verify_rule_decision_intent_records
from src.trading_runtime.arte_journal_writer import _rows, _literal, _committed_batch_filter


def join_decision_intents(client, prefix, rows):
    """Resolve exact semantic intent IDs in bounded committed-event queries."""
    wanted = [child["strategy_intent_id"] for _, children in rows for child in children]
    if len(wanted) > 8000 or len(set(wanted)) != len(wanted):
        raise ValueError("Rule decision page repeats intents or exceeds its bound")
    recovered = {}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start:start + 500]
        events = _rows(client, "SELECT record_id,entity_id FROM arte.trading_event_v1 "
            f"WHERE run_id={_literal(prefix.run_id)} AND sequence<={prefix.last_sequence} "
            "AND category='strategy' AND entity_type='strategy_intent' "
            f"AND entity_id IN ({','.join(_literal(value) for value in chunk)}) "
            f"{_committed_batch_filter(prefix)} LIMIT {len(chunk) + 1} FORMAT JSONEachRow")
        if (len(events) != len(chunk) or {event["entity_id"] for event in events} != set(chunk)
                or len({event["record_id"] for event in events}) != len(events)):
            raise ValueError("Rule decision committed intent identity coverage differs")
        page = load_committed_strategy_intent_page(client, prefix,
            record_ids=tuple(event["record_id"] for event in events), limit=len(chunk))
        if len(page) != len(chunk) or {record.intent.intent_id for record in page} != set(chunk):
            raise ValueError("Rule decision semantic intent coverage differs")
        recovered.update((record.intent.intent_id, record) for record in page)
    for parent, children in rows:
        verify_rule_decision_intent_records(parent, children,
            tuple(recovered[child["strategy_intent_id"]] for child in children))
    return rows


def verify_decision_completion_page(rows, events):
    """Require an exact normalized journal completion for every decision row."""
    if len(events) != len(rows):
        raise ValueError("Rule decision journal completion coverage differs")
    for (parent, children), item in zip(rows, events, strict=True):
        if (type(item) is not TypedJournalEvent
                or item.detail_family not in {"trading_strategy_signal_v1", "trading_strategy_signal_v2"}
                or item.detail is None):
            raise ValueError("Rule decision journal completion family differs")
        event, detail = item.event, item.detail
        if (any(event.get(key) != parent[key] for key in ("run_id", "batch_id", "record_id", "account_id", "event_month"))
                or any(detail.get(key) != parent[key] for key in ("run_id", "batch_id", "record_id", "account_id", "event_month"))
                or detail.get("signal_id") != parent["decision_id"]
                or detail.get("decision_status") != "completed"
                or event.get("correlation_id") != parent["decision_id"]
                or _normalize(detail.get("source_event_time"), "DateTime64(6, 'UTC')", occurrence=False) != parent["boundary_at"]):
            raise ValueError("Rule decision journal completion envelope differs")
        record = JournalRecord(event["record_id"], event["run_id"], int(event["sequence"]),
            datetime.fromisoformat(_normalize(event["event_time"], "DateTime64(6, 'UTC')", occurrence=False)),
            datetime.fromisoformat(_normalize(event["recorded_at"], "DateTime64(6, 'UTC')", occurrence=False)),
            event["category"], event["entity_type"], event["entity_id"], event["account_id"],
            dict(signal_type=detail["signal_type"], causation_id=event["causation_id"],
                metadata=dict(assignment_id=detail["decision_assignment_id"], reason_detail=detail["decision_reason_detail"])))
        verify_rule_decision_completion_record(parent, children, record)
    return rows


async def read_rule_decision_page_async(reader_factory, prefix, *, after_sequence=0, limit=200):
    """Keep database work off-loop and drain the owned client on cancellation."""
    if (not callable(reader_factory) or type(prefix) is not V4CommittedPrefix
            or not prefix.batch_ids or type(after_sequence) is not int
            or not 0 <= after_sequence <= prefix.last_sequence
            or type(limit) is not int or not 1 <= limit <= 500):
        raise ValueError("Rule decision recovery requires a bounded exact V4 prefix")

    def read():
        client = reader_factory()
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != "1":
                raise ValueError("Rule decision recovery requires a SELECT-only client")
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Rule decision prefix changed before readback")
            rows = load_committed_rule_decision_page(client, prefix,
                after_sequence=after_sequence, limit=limit)
            if rows:
                events = load_typed_event_page(client, prefix, after_sequence=after_sequence,
                    limit=len(rows), selected_sequences=tuple(parent["decision_sequence"] for parent, _ in rows))
                verify_decision_completion_page(rows, events)
                join_decision_intents(client, prefix, rows)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Rule decision prefix changed after readback")
            return rows
        finally:
            client.close()

    return await _owned_worker(read)


def load_decision_observation_page(client, prefix, rows, *, maximum_children):
    """Join complete normalized OMS observations to already verified decisions.

    Missing observation roots are explicit failures, including old releases
    which did not preserve this evidence. Never borrow their later checkpoint.
    ``prefix`` must have been verified by the owning read operation.
    """
    from src.trading_runtime.rule_decision_basis_schema import OBSERVATION, OBSERVED_ORDER, UNOBSERVED_ORDER
    from src.trading_runtime.rule_decision_observation import restore_decision_observation
    if (type(prefix) is not V4CommittedPrefix or type(maximum_children) is not int
            or not 1 <= maximum_children <= 1000000 or len(rows) > 500):
        raise ValueError('Decision observation read requires explicit bounded committed scope')
    if not rows:
        return ()
    bases = {parent['record_id']: parent for parent, _ in rows}
    if len(bases) != len(rows) or any(parent['run_id'] != prefix.run_id for parent in bases.values()):
        raise ValueError('Decision observation page repeats or changes its run identity')
    ids = ','.join(_literal(identity) for identity in bases)
    common = f'WHERE run_id={_literal(prefix.run_id)} {_committed_batch_filter(prefix)} '
    roots = _rows(client, f'SELECT * FROM arte.{OBSERVATION.name} {common}'
        f'AND record_id IN ({ids}) LIMIT {len(rows) + 1} FORMAT JSONEachRow')
    if len(roots) != len(rows) or {root['record_id'] for root in roots} != set(bases):
        raise ValueError('Committed decision observation root coverage differs')
    children = []
    remaining = maximum_children
    for table in (OBSERVED_ORDER, UNOBSERVED_ORDER):
        page = _rows(client, f'SELECT * FROM arte.{table.name} {common}'
            f'AND parent_record_id IN ({ids}) LIMIT {remaining + 1} FORMAT JSONEachRow')
        if len(page) > remaining:
            raise ValueError('Committed decision observations exceed their explicit child bound')
        remaining -= len(page)
        grouped = {identity: [] for identity in bases}
        for child in page:
            if child['parent_record_id'] not in grouped:
                raise ValueError('Committed decision observation child has a foreign parent')
            grouped[child['parent_record_id']].append(child)
        children.append(grouped)
    roots = {root['record_id']: root for root in roots}
    return tuple((parent, intents, restore_decision_observation(parent, roots[parent['record_id']],
        tuple(sorted(children[0][parent['record_id']], key=lambda row: row['ordinal'])),
        tuple(sorted(children[1][parent['record_id']], key=lambda row: row['ordinal']))))
        for parent, intents in rows)


async def read_rule_decision_observation_page_async(reader_factory, prefix, *,
        after_sequence=0, limit=200, maximum_children):
    """Read decision, completion, intent and observed-OMS facts on one worker."""
    if (not callable(reader_factory) or type(prefix) is not V4CommittedPrefix or not prefix.batch_ids
            or type(after_sequence) is not int or not 0 <= after_sequence <= prefix.last_sequence
            or type(limit) is not int or not 1 <= limit <= 500
            or type(maximum_children) is not int or not 1 <= maximum_children <= 1000000):
        raise ValueError('Recorded observation recovery requires a bounded exact V4 prefix')
    def read():
        client = reader_factory()
        try:
            if client.execute("SELECT getSetting('readonly')").strip() != '1':
                raise ValueError('Recorded observation recovery requires a SELECT-only client')
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError('Recorded observation prefix changed before readback')
            rows = load_committed_rule_decision_page(client, prefix, after_sequence=after_sequence, limit=limit)
            if rows:
                events = load_typed_event_page(client, prefix, after_sequence=after_sequence, limit=len(rows),
                    selected_sequences=tuple(parent['decision_sequence'] for parent, _ in rows))
                verify_decision_completion_page(rows, events)
                join_decision_intents(client, prefix, rows)
            result = load_decision_observation_page(client, prefix, rows, maximum_children=maximum_children)
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError('Recorded observation prefix changed after readback')
            return result
        finally:
            client.close()
    return await _owned_worker(read)
