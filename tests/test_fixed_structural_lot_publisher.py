"""Real queued writer/publication path; explicit uninstalled admission seam."""
import asyncio
from uuid import uuid4
import pytest
from test_fixed_structural_lot_entry_v4 import packet
from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
from src.trading_runtime.fixed_structural_lot_entry_v4 import FixedStructuralLotPublicationContext
from src.trading_runtime import arte_journal_writer as writer_module
from tests.test_arte_journal_commit_v4 import attached_v4_client


def test_actual_queued_source_gate_and_publisher_receipt(monkeypatch):
    async def run():
        source,request,journal,record,unit=packet(monkeypatch)
        client=attached_v4_client()
        # Controlled database metadata transport seam only; no table install.
        monkeypatch.setattr(writer_module,'storage_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_module,'journal_permission_preflight',lambda *a,**k:None)
        monkeypatch.setattr(writer_module,'_verify_run_identity',lambda *a:dict(mode='backtest',account_ids=(request.entry.proposal.account_id,)))
        writer=writer_module.ArteJournalWriter(client,run_id=source.run_id,journal_profile='backtest_v4',coalesce_batches=False)
        config=dict(mode='backtest',strategy_id=request.strategy_id,strategy_revision=request.revision,
            parent_configuration_hash=source.parent_payload_hash,selected_configuration_hash=source.selected_configuration_hash)
        publisher=BacktestTypedJournalPublisher(journal,writer,attempt_id=str(uuid4()),
            run_month=source.session_date.replace(day=1),expected_config=config)
        try:
            with pytest.raises(ValueError,match='installed'):
                publisher.bind_fixed_structural_lot_source(source)
            with pytest.raises(ValueError,match='installed'):
                writer.submit_fixed_structural_lot_entry_v4(FixedStructuralLotPublicationContext(unit,record,source))
            assert not client.inserts and writer.metrics()['queue_depth']==0
            monkeypatch.setattr(PreparedFixedStructuralLotSource,'require_installed_admission',lambda self:None)
            # Uninstalled controlled transport fixture; no real selected profile exists.
            from src.trading_runtime import fixed_structural_lot_profile as profile_module
            monkeypatch.setattr(profile_module,'require_fixed_structural_lot_client_context',lambda *args:None)
            publisher.bind_fixed_structural_lot_source(source)
            receipt=await publisher._drain(target_sequence=1)
            assert receipt.last_sequence==1
            assert writer.metrics()['committed_units']==1
            assert writer.metrics()['committed_event_rows']==1
            assert client.inserts[-1]=='trading_commit_v4'
            assert len(client.fixed_structural_lot_contexts)==1
            assert publisher._committed_fixed_lot_units[request.intent.intent_id].base.batch_id==receipt.last_batch_id
            assert publisher._committed_strategy_intents[request.intent.intent_id][1]==request.intent
        finally:
            writer.close();journal.close()
    asyncio.run(run())
