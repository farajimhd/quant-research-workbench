"""Relational verification of central decision families in a V4 commit."""
from .rule_decision_basis_schema import BASIS, INTENT
from .rule_decision_basis_projection import _seal, verify_rule_decision_basis


def verify_rule_decision_commit_graph(families, *, run_id, batch_id):
    from src.backend.rule_decision_recovery import verify_decision_completion_page
    from .arte_journal_reader import TypedJournalEvent
    parents = tuple(_seal(BASIS, {key: value for key, value in row.items() if key != "content_hash"})
                    for row in families.get(BASIS.name, ()))
    children = tuple(_seal(INTENT, {key: value for key, value in row.items() if key != "content_hash"})
                     for row in families.get(INTENT.name, ()))
    raw_signals = tuple(families.get("trading_strategy_signal_v2", ()))
    exact_signals = tuple(families.get("trading_strategy_signal_exact_price_v2", ()))
    if exact_signals:
        from .trading_price_rows import restore_exact_price_row
        raw_signals += tuple(restore_exact_price_row("trading_strategy_signal_v2", row)
            for row in exact_signals)
    signals = tuple(row for row in raw_signals
                    if row["signal_type"] == "program_boundary_completed")
    if not parents and not children and not signals:
        return
    parent_ids = {row["record_id"] for row in parents}
    if (len(parent_ids) != len(parents) or len({row["record_id"] for row in signals}) != len(signals)
            or parent_ids != {row["record_id"] for row in signals}
            or any(row["parent_record_id"] not in parent_ids for row in children)
            or len({row["record_id"] for row in children}) != len(children)
            or len({row["strategy_intent_id"] for row in children}) != len(children)):
        raise ValueError("Rule commit has missing, duplicate or orphan decision coverage")
    events = {row["record_id"]: row for row in families.get("trading_event_v1", ())}
    details = {row["record_id"]: row for row in signals}
    grouped = {identity: [] for identity in parent_ids}
    for child in children:
        grouped[child["parent_record_id"]].append(child)
    for parent in parents:
        if parent["run_id"] != run_id or parent["batch_id"] != batch_id:
            raise ValueError("Rule commit decision scope differs")
        selected = tuple(sorted(grouped[parent["record_id"]], key=lambda row: row["ordinal"]))
        verify_rule_decision_basis(parent, selected)
        event = events.get(parent["record_id"])
        if event is None:
            raise ValueError("Rule commit completion event is missing")
        verify_decision_completion_page(((parent, selected),),
            (TypedJournalEvent(event, "trading_strategy_signal_v2", details[parent["record_id"]]),))
