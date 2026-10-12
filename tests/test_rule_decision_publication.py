import pytest

from src.trading_runtime import rule_decision_publication as module
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.rule_decision_basis_schema import STAGED_TABLES


def test_opt_in_catalog_preserves_original_v4_and_declares_shared_tables_once():
    original = writer.v4_storage_contracts()
    combined = module.rule_decision_storage_contracts()
    from src.trading_runtime.trading_price_contracts import exact_price_storage_contracts
    exact = exact_price_storage_contracts()
    from src.trading_runtime.arte_entry_acquisition_freeze import TABLES as freeze
    from src.trading_runtime.backtest_journal_price_contract import PRICE_CONTRACT
    assert len(combined) == len(original) + len(STAGED_TABLES) + len(exact) + len(freeze) + 1
    assert combined[:len(original)] == original
    assert len({table.name for table in combined}) == len(combined)
    assert {table.name for table in STAGED_TABLES}.isdisjoint(table.name for table in original)
    assert {table.name for table in exact}.isdisjoint(table.name for table in original)
    assert combined[-len(exact)-len(freeze)-1:-len(freeze)-1] == exact
    assert combined[-len(freeze)-1:-1] == freeze
    assert combined[-1] == PRICE_CONTRACT


@pytest.mark.parametrize("failure", [False, True])
def test_storage_preflight_uses_shared_audit_and_propagates_failures(monkeypatch, failure):
    client = object()
    calls = []
    def audit(actual, *, tables):
        assert actual is client
        assert tables == module.rule_decision_storage_contracts()
        calls.append(True)
        if failure:
            raise RuntimeError("Synthetic incorrect part placement")
    monkeypatch.setattr(writer, "storage_preflight", audit)
    if failure:
        with pytest.raises(RuntimeError, match="part placement"):
            module.preflight_rule_decision_storage(client)
    else:
        module.preflight_rule_decision_storage(client)
    assert calls == [True]


@pytest.mark.parametrize("lose_response", [False, True])
def test_prepared_binary_request_uses_native_registered_transport(lose_response):
    from uuid import uuid4
    from tests.test_rule_decision_basis_projection import values
    from tests.test_arte_typed_insert_dispatch import Keeper, Client, attest_direct, ZERO_BATCH, BATCH_ID
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.journal_contract import JournalRecord
    from src.trading_runtime.signals import StrategySignal
    from src.trading_runtime.arte_journal_projection import project_journal_record
    from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis, RuleDecisionBasisBatch
    scope = {**values("wait"), "run_id": "run-1", "basis_sequence": 0, "decision_sequence": 1, "batch_id": BATCH_ID}
    at = scope["boundary_at"]
    metadata = dict(assignment_id="assignment", reference_price=10., status="completed",
        reason_code="program:wait", reason_detail="wait", correlation_id=scope["decision_id"],
        causation_id=scope["source_commit_hash"])
    signal = StrategySignal(scope["decision_id"], "program_boundary_completed", "ABC", at,
        "wait", "neutral", 0., 1., "program:wait", working_timeframe="1s", metadata=metadata)
    record = JournalRecord(scope["record_id"], "run-1", 1, at, at, "strategy_decision", "signal",
        scope["decision_id"], "account", {**signal.payload(), "strategy_id": "program",
            "strategy_revision": 1, "correlation_id": scope["decision_id"], "causation_id": scope["source_commit_hash"]})
    base = project_journal_record(record, run_month=scope["event_month"], attempt_id=str(uuid4()),
        batch_id=BATCH_ID, prior_batch_id=ZERO_BATCH, source_cursor="source")
    parent, children = project_rule_decision_basis(scope, [])
    prepared = module.prepare_rule_decision_commit(RuleDecisionBasisBatch(base, parent, children), committed_at=at)
    request, = module.prepare_rule_decision_insert_requests(prepared)
    authority = TypedInsertDispatch(Keeper())
    authority.initialize_new_run("run-1")
    attest_direct(authority, "run-1")
    authority.assert_next_batch(run_id="run-1", batch_id=BATCH_ID,
        prior_batch_id=ZERO_BATCH, first_sequence=1, last_sequence=1)
    client = Client(authority, lose_response=lose_response)
    args = dict(run_id=request.run_id, table=request.table, token=request.token, sql=request.sql,
        batch_id=request.batch_id, batch_last_sequence=request.last_sequence)
    if lose_response:
        with pytest.raises(TimeoutError, match="response lost"):
            authority.execute_typed_insert(client, **args)
        assert authority.operation_present(run_id=request.run_id, table=request.table, token=request.token)
        from src.trading_runtime.keeper_ownership import KeeperUnavailable
        with pytest.raises(KeeperUnavailable, match="acknowledged"):
            authority.seal_verified_operation(**args)
    else:
        authority.execute_typed_insert(client, **args)
        authority.seal_verified_operation(**args)
    assert client.calls == [(request.sql, request.query_id)]
