"""Transport-only source caching; native trading qualification has separate tests."""
import asyncio
from concurrent.futures import Future
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.test_rule_decision_basis_projection import values
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.backend.backtest_program_publication import retain_qualified_rule_decision
from src.backend.rule_decision_reselection import QualifiedRuleDecision, _NativeDecisionEvidence, _NATIVE_DECISION_PROOF
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.rule_decision_basis_projection import project_rule_decision_basis, RuleDecisionBasisBatch, rule_decision_semantic_hash
from src.trading_runtime.rule_decision_publication import QualifiedRuleDecisionBatch
from src.trading_runtime.signals import StrategyIntent, CapitalRequest, StrategySignal


@pytest.mark.parametrize("failure", [False, True])
def test_program_intent_cache_is_populated_only_after_matching_compound_ack(failure):
    scope = values()
    at = scope["boundary_at"]
    journal = BacktestMemoryJournal(run_id="run")
    intent = StrategyIntent(str(uuid4()), "ABC", at, "enter_long", 0., 10.,
        capital_request=CapitalRequest("fixed_notional", 1000.), reason="program:entry")
    source = journal.append(run_id="run", category="strategy", entity_type="strategy_intent",
        entity_id=intent.intent_id, account_id="account", event_time=at,
        payload={**intent.payload(), "strategy_id": "program", "strategy_revision": 1})
    metadata = dict(assignment_id="assignment", reference_price=10., status="completed",
        reason_code="program:selected", reason_detail="selected", correlation_id=scope["decision_id"],
        causation_id=scope["source_commit_hash"])
    signal = StrategySignal(scope["decision_id"], "program_boundary_completed", "ABC", at,
        "wait", "neutral", 0., 1., "program:selected", working_timeframe="1s", metadata=metadata)
    completion = journal.append(run_id="run", category="strategy_decision", entity_type="signal",
        entity_id=signal.signal_id, account_id="account", event_time=at,
        payload={**signal.payload(), "strategy_id": "program", "strategy_revision": 1,
            "correlation_id": scope["decision_id"], "causation_id": scope["source_commit_hash"]})
    scope.update(record_id=completion.record_id, batch_id=completion.record_id,
        basis_sequence=0, decision_sequence=2)
    parent, children = project_rule_decision_basis(scope,
        [dict(stage="entry", strategy_intent_id=intent.intent_id)])
    journal.retain_rule_decision_basis(completion, parent, children)
    attempt = str(uuid4())
    base = project_journal_record(completion, run_month=scope["event_month"], attempt_id=attempt,
        batch_id=completion.record_id, prior_batch_id="00000000-0000-0000-0000-000000000000",
        source_cursor="start")
    digest = rule_decision_semantic_hash(parent, children)
    # Synthetic proof fixture for transport/receipt behavior only.
    proof = QualifiedRuleDecision("run", 0, digest, None,
        _NativeDecisionEvidence("run", 0, digest, _NATIVE_DECISION_PROOF))
    qualified = QualifiedRuleDecisionBatch(RuleDecisionBasisBatch(base, parent, children), proof)
    pending = Future()
    submissions = []
    def submit(unit):
        submissions.append(unit)
        return pending
    writer = SimpleNamespace(run_id="run", run_mode="backtest", coalesce_batches=False,
        journal_profile="backtest_v4", max_events_per_commit=512, submit_compound_v4=submit)
    publisher = BacktestTypedJournalPublisher(journal, writer, attempt_id=attempt,
        run_month=scope["event_month"], expected_config=dict(strategy_id="program", strategy_revision=1))
    retain_qualified_rule_decision(publisher, qualified)
    async def run_test():
        task = publisher.enqueue_pending()
        for _ in range(100):
            if submissions:
                break
            await asyncio.sleep(.001)
        assert len(submissions) == 1 and not task.done()
        assert not publisher._committed_strategy_intents and publisher.fenced_sequence == 0
        if failure:
            pending.set_exception(RuntimeError("synthetic rejected commit"))
            with pytest.raises(RuntimeError, match="rejected commit"):
                await task
            assert not publisher._committed_strategy_intents and publisher.fenced_sequence == 0
        else:
            batch = submissions[0].base
            pending.set_result(batch.batch_id)
            receipt = await task
            stored, restored = publisher._committed_strategy_intents[intent.intent_id]
            assert restored == intent and stored.batch_id == receipt.last_batch_id
            assert stored.first_sequence == stored.last_sequence == source.sequence
            assert stored.events[0]["record_id"] == source.record_id
            assert publisher.fenced_sequence == 2 and journal.pending_record_count == 0
        journal.close()
    asyncio.run(run_test())
