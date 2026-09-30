"""Strategy 12 seals one entry-age rule and rejects any different parent authority."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest

from test_strategy_eight_configuration import prepared_eight
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
from pipelines.strategy_one.strategy_twelve_configuration import compile_strategy_twelve_configuration
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_twelve_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, FOLLOWTHROUGH_POLICY, ENTRY_SCOPE_POLICY, RECENT_BOS_POLICY,
    verify_strategy_twelve_manifest,
)
from src.trading_runtime.strategy_registry import numbered_strategy_parent, numbered_strategy, fixed_strategy_executor
from test_fixed_numbered_registry import assignment


def pinned_parent():
    # Synthetic metadata for compiler contract tests, not DB certification.
    from test_strategy_eleven_configuration import pinned_parent as tenth_parent, compile_eleven
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    envelope = compile_eleven(tenth_parent())
    return CertifiedStrategyOneConfiguration(PARENT_REVISION_ID.split(":")[1],
        PARENT_PAYLOAD_HASH, envelope["node_hash"], envelope["source_candidate_id"],
        envelope["source_candidate_hash"], "test-only", envelope["payload"])


def compile_twelve(parent):
    return compile_strategy_twelve_configuration(parent, approved_code_commit="d" * 40,
        approved_code_fingerprint="e" * 64, approval_reference="approved-single-failure-exit")


def test_twelfth_seal_preserves_parent_parameters_policies_and_immutability():
    parent = pinned_parent()
    previous = deepcopy(parent.payload)
    envelope = compile_twelve(parent)
    _verified_numbered_envelope(envelope)
    payload = envelope["payload"]
    assert parent.payload == previous
    assert payload["strategy"]["parameters"] == previous["strategy"]["parameters"]
    manifest = payload["strategy"]["numbered_release"]
    assert manifest["source_revision_id"] == PARENT_REVISION_ID
    assert manifest["source_payload_hash"] == PARENT_PAYLOAD_HASH
    for key in ("activation_policy", "session_policy", "add_policy", "trailing_policy", "target_policy", "entry_price_policy"):
        assert manifest[key] == previous["strategy"]["numbered_release"][key]
    assert manifest["followthrough_policy"] == FOLLOWTHROUGH_POLICY
    assert manifest["entry_scope_policy"] == ENTRY_SCOPE_POLICY
    assert manifest["entry_scope_policy"] == previous["strategy"]["numbered_release"]["entry_scope_policy"]
    inherited_failure = previous["strategy"]["numbered_release"]["followthrough_policy"]
    assert {k: manifest["followthrough_policy"][k] for k in inherited_failure} == inherited_failure
    assert manifest["followthrough_policy"]["early_failure_window_ms"] == 60_000
    assert manifest["followthrough_policy"] == inherited_failure
    assert manifest["recent_bos_policy"] == RECENT_BOS_POLICY
    assert numbered_strategy_parent(12) == 11
    assert numbered_strategy(12).number == 12


@pytest.mark.parametrize("changes", [{"attempt_id": "00000000-0000-0000-0000-000000000008"}, {"payload_hash": "0" * 64}])
def test_different_tenth_parent_rejected(changes):
    with pytest.raises(ValueError, match="exact pinned"):
        compile_twelve(replace(pinned_parent(), **changes))


@pytest.mark.parametrize("field,value", [("publication_mode", "live"), ("source_revision_id", "strategy-one-8:00000000-0000-0000-0000-000000000008"), ("source_payload_hash", "0" * 64), ("followthrough_policy", {}), ("entry_scope_policy", {}), ("recent_bos_policy", {})])
def test_resealed_policy_or_parent_override_rejected(field, value):
    strategy = compile_twelve(pinned_parent())["payload"]["strategy"]
    manifest = strategy["numbered_release"]
    manifest[field] = value
    manifest["manifest_hash"] = sha256(canonical_json({k: v for k, v in manifest.items() if k != "manifest_hash"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="sealed contract"):
        verify_strategy_twelve_manifest(strategy)


def test_twelfth_runtime_remains_backtest_only():
    release = numbered_strategy(12)
    registration = fixed_strategy_executor(release.executor_strategy_id, 12)
    selected = replace(assignment(), strategy_revision=12)
    assert registration.build([selected], mode="backtest").contract.strategy_number == 12
    with pytest.raises(ValueError, match="Backtest-only"):
        registration.build([selected], mode="live")


def test_twelfth_certificate_binds_completed_rule_and_normalized_source_route():
    from src.backend.backtest_fixed_v4_certification import (
        certify_numbered_fixed_v4_projection, certify_followthrough_failure_v4_source, certify_empty_exclusion_entry_scope_source, certify_early_followthrough_failure_v4_source,
    )
    assert len(certify_numbered_fixed_v4_projection(12)) == 64
    assert len(certify_followthrough_failure_v4_source()) == 64
    assert len(certify_empty_exclusion_entry_scope_source()) == 64
    assert len(certify_early_followthrough_failure_v4_source()) == 64


@pytest.mark.parametrize('before,after', [
    ('row.candidate_count != 0', 'row.candidate_count < 0'),
    ('original_activations.rows != activations.rows', 'False'),
    ('full = certify_candidate_plan(', 'full = unavailable_candidate_plan('),
])
def test_twelfth_scope_source_guard_mutation_rejected(tmp_path, before, after):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_empty_exclusion_entry_scope_source
    path = Path(__file__).parents[1] / 'src/backend/backtest_strategy_one_entry_store.py'
    altered = tmp_path / 'altered_entry_store.py'
    altered.write_text(path.read_text().replace(before, after))
    with pytest.raises(ValueError, match='Strategy 10 scope'):
        certify_empty_exclusion_entry_scope_source(source_path=altered)


@pytest.mark.parametrize('before,after', [
    ('EARLY_FAILURE_WINDOW_MS = 60_000', 'EARLY_FAILURE_WINDOW_MS = 60_001'),
    ('> EARLY_FAILURE_WINDOW_MS', '>= EARLY_FAILURE_WINDOW_MS'),
    ('witness.first_held_boundary_ms', 'witness.boundary_ms'),
])
def test_twelfth_early_window_source_mutation_rejected(tmp_path, before, after):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_early_followthrough_failure_v4_source
    path = Path(__file__).parents[1] / 'src/trading_runtime/strategy_early_followthrough_failure.py'
    altered = tmp_path / 'altered_early_failure.py'
    altered.write_text(path.read_text().replace(before, after))
    with pytest.raises(ValueError, match='Strategy 11'):
        certify_early_followthrough_failure_v4_source(source_path=altered)


@pytest.mark.parametrize("before,after", [("30_000", "30_001"), ("<= MAX_BOS_ENTRY_AGE_MS", "< MAX_BOS_ENTRY_AGE_MS"), ("breaks > 0", "breaks >= 0")])
def test_twelfth_recent_bos_source_guard_rejects_mutation(tmp_path, before, after):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_recent_bos_entry_source
    source = Path("src/trading_runtime/strategy_recent_bos_entry.py")
    altered = tmp_path / "altered_recent_bos.py"
    altered.write_text(source.read_text().replace(before, after))
    with pytest.raises(ValueError, match="Strategy 12"):
        certify_recent_bos_entry_source(source_path=altered)


@pytest.mark.parametrize("path,key,before,after", [
    ("backtest_strategy_one_static_gate.py", "static_path", "strategy_number in (12, 13)", "strategy_number in (11, 12, 13)"),
    ("backtest_strategy_one_stateful.py", "adapter_path", "strategy_number in (12, 13)", "strategy_number == 11"),
    ("backtest_strategy_one_coordinator.py", "coordinator_path", "strategy_number=strategy_number", "strategy_number=11"),
])
def test_twelfth_source_guard_rejects_route_mutation(tmp_path, path, key, before, after):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_recent_bos_entry_source
    original = Path("src/backend") / path
    assert before in original.read_text()
    altered = tmp_path / path
    altered.write_text(original.read_text().replace(before, after))
    with pytest.raises(ValueError, match="Strategy 12"):
        certify_recent_bos_entry_source(**{key: altered})


def test_twelfth_inherited_early_rule_manager_route_is_source_bound(tmp_path):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_early_followthrough_failure_v4_source
    original = Path("src/backend/backtest_strategy_one_management.py")
    altered = tmp_path / original.name
    assert "self.contract.strategy_number in (11, 12, 13)" in original.read_text()
    altered.write_text(original.read_text().replace("self.contract.strategy_number in (11, 12, 13)", "self.contract.strategy_number == 11"))
    with pytest.raises(ValueError, match="Strategy 11"):
        certify_early_followthrough_failure_v4_source(management_path=altered)


@pytest.mark.parametrize("number", [11, 12])
def test_inherited_normalized_failure_inclusive_first_minute(number):
    from dataclasses import replace
    from src.trading_runtime.arte_followthrough_failure_v4 import validate_numbered_failure
    from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailure
    witness = FollowThroughFailure(95_000, 35_000, 10.01, 9.89, 99400,
                                  -.02, -.01, 9.94, 9.95, 100000)
    validate_numbered_failure(witness, number)
    with pytest.raises(ValueError, match="first-minute"):
        validate_numbered_failure(replace(witness, first_held_boundary_ms=34_900), number)
    # Original 9/10 remain unbounded and retain exactly the same witness contract.
    validate_numbered_failure(replace(witness, first_held_boundary_ms=34_900), 9)
    validate_numbered_failure(replace(witness, first_held_boundary_ms=34_900), 10)


@pytest.mark.parametrize("bos", [None, 1_000])
def test_forged_twelfth_proposal_rejected_before_portfolio_and_normalized_projection(bos):
    import asyncio
    from datetime import date
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import UUID
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    from src.trading_runtime.arte_strategy_one_entry_journal import project_strategy_one_entry_evidence
    from test_strategy_one_intent import _proposal
    # 30100ms stale BOS is a valid original11 proposal but cannot enter under12.
    original = replace(_proposal(), boundary_ms=31_100, bos_break_boundary_ms=1_000, strategy_number=11)
    intent = strategy_one_entry_intent(original, session_date=date(2026, 8, 18))
    forged = replace(original, strategy_number=12, bos_break_boundary_ms=bos)
    runtime = object.__new__(TradingRuntime)
    runtime.config = SimpleNamespace(mode=RunMode.BACKTEST, strategy_id="early-squeeze-strategy",
        strategy_revision=12, account_ids=("DU1",), anchor_date=date(2026, 8, 18))
    runtime.run_id = str(UUID(int=501))
    runtime.journal = BacktestMemoryJournal(run_id=runtime.run_id)
    runtime.portfolio = SimpleNamespace(approve=AsyncMock())
    runtime._execute_intents = AsyncMock()
    with pytest.raises(ValueError, match="recent supported BOS"):
        asyncio.run(runtime.submit_strategy_one_proposal(forged))
    runtime.portfolio.approve.assert_not_awaited()
    runtime._execute_intents.assert_not_awaited()
    with pytest.raises(ValueError, match="recent supported BOS"):
        project_strategy_one_entry_evidence(forged, intent, session_date=date(2026, 8, 18),
            run_id=runtime.run_id, batch_id=str(UUID(int=502)), parent_record_id=str(UUID(int=503)))


def test_twelfth_intent_source_guard_rejects_missing_factory_gate(tmp_path):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_recent_bos_entry_source
    source = Path("src/trading_runtime/strategy_one_intent.py")
    altered = tmp_path / source.name
    assert "proposal.strategy_number in (12, 13)" in source.read_text()
    altered.write_text(source.read_text().replace("proposal.strategy_number in (12, 13)", "proposal.strategy_number == 11"))
    with pytest.raises(ValueError, match="Strategy 12"):
        certify_recent_bos_entry_source(intent_path=altered)


@pytest.mark.parametrize("number,raises", [(11, False), (12, True)])
def test_raw_normalized_late_bos_child_is_rejected_only_by_twelfth(number, raises):
    from datetime import date
    from uuid import UUID
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    from src.trading_runtime.arte_strategy_one_entry_journal import project_strategy_one_entry_evidence
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_journal_writer import _sealed_families
    from src.trading_runtime.arte_journal_commit_v4 import _sealed_strategy_one_entry_rows
    from test_strategy_one_intent import _proposal
    proposal = replace(_proposal(), boundary_ms=31_100, bos_break_boundary_ms=1_000, strategy_number=11)
    intent = strategy_one_entry_intent(proposal, session_date=date(2026, 8, 18))
    batch = strategy_intent_batch(intent, run_id="run-12", run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=str(UUID(int=701)), batch_id=str(UUID(int=702)),
        prior_batch_id=str(UUID(int=0)), sequence=1, source_cursor="cursor",
        run_status="running", recorded_at=intent.event_time)
    row = project_strategy_one_entry_evidence(proposal, intent, session_date=date(2026, 8, 18),
        run_id=batch.run_id, batch_id=batch.batch_id, parent_record_id=batch.intents[0]["record_id"])
    row["strategy_number"] = number
    if raises:
        with pytest.raises(ValueError, match="recent supported BOS"):
            _sealed_strategy_one_entry_rows(batch, _sealed_families(batch), (row,))
    else:
        assert _sealed_strategy_one_entry_rows(batch, _sealed_families(batch), (row,))


def test_twelfth_raw_entry_source_guard_rejects_missing_gate(tmp_path):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_recent_bos_entry_source
    source = Path("src/trading_runtime/arte_journal_commit_v4.py")
    altered = tmp_path / source.name
    assert 'row["strategy_number"] in (12, 13)' in source.read_text()
    altered.write_text(source.read_text().replace('row["strategy_number"] in (12, 13)', 'row["strategy_number"] == 11'))
    with pytest.raises(ValueError, match="Strategy 12"):
        certify_recent_bos_entry_source(commit_path=altered)



def test_cold_entry_page_rejects_resealed_stale_twelfth_child():
    from datetime import date
    from uuid import UUID
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    from src.trading_runtime.arte_strategy_one_entry_journal import (
        ENTRY_EVIDENCE, project_strategy_one_entry_evidence, load_committed_strategy_one_entry_page,
    )
    from src.trading_runtime.arte_intent_projection import strategy_intent_batch
    from src.trading_runtime.arte_journal_commit_v4 import _publish_typed_batch_v4, load_verified_v4_prefix
    from src.trading_runtime.arte_journal_writer import typed_row
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    from test_strategy_one_intent import _proposal
    proposal = replace(_proposal(), boundary_ms=31_100, bos_break_boundary_ms=1_000, strategy_number=11)
    session = date(2026, 8, 18)
    intent = strategy_one_entry_intent(proposal, session_date=session)
    batch = strategy_intent_batch(intent, run_id="cold-12", run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=str(UUID(int=801)), batch_id=str(UUID(int=802)),
        prior_batch_id=str(UUID(int=0)), sequence=1, source_cursor="cursor",
        run_status="running", recorded_at=intent.event_time)
    row = project_strategy_one_entry_evidence(proposal, intent, session_date=session,
        run_id=batch.run_id, batch_id=batch.batch_id, parent_record_id=batch.intents[0]["record_id"])
    client = attached_v4_client()
    _publish_typed_batch_v4(client, batch, strategy_one_entry_rows=(row,))
    prefix = load_verified_v4_prefix(client, batch.run_id)
    assert load_committed_strategy_one_entry_page(client, prefix).entries[0].proposal == proposal
    # Even a self-consistent scalar row hash cannot bypass cold factory semantics.
    stored = dict(client.tables[ENTRY_EVIDENCE.name][0])
    stored.pop("content_hash")
    stored["strategy_number"] = 12
    client.tables[ENTRY_EVIDENCE.name][0] = typed_row(ENTRY_EVIDENCE.name, stored)
    with pytest.raises(ValueError, match="recent supported BOS"):
        load_committed_strategy_one_entry_page(client, prefix)
