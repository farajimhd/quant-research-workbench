from types import SimpleNamespace
from dataclasses import replace
import pytest
from tests.test_backtest_native_channel_qualification import installed, unit
from tests.test_backtest_native_channel_manager_recovery import manager_case
from tests.test_native_channel_manager_reader import Reader
from src.trading_runtime import strategy_one_management_snapshot as manager
from src.backend.backtest_native_channel_inherited_checkpoint import load_current_native_inherited_checkpoint

@pytest.mark.parametrize('route', ['dedicated', 'shared'])
@pytest.mark.parametrize('defect', [None, 'prefix', 'cursor', 'hash', 'head_changed', 'pending', 'policy'])
def test_inherited_native_checkpoint_retains_attestation_and_rejects_pending(installed, monkeypatch, defect, route):
    from src.trading_runtime import arte_journal_commit_v4, arte_journal_projection
    from src.trading_runtime.native_channel_manager_reader import load_attested_native_channel_manager_snapshot
    rows, policy, contexts = manager_case(installed)
    from src.trading_runtime.native_channel_manager_snapshot import seal_native_channel_manager_rows
    if defect != 'pending':
        rows=seal_native_channel_manager_rows(rows.inherited,(),(),(),
            max_pending_requests=policy.max_pending_requests,max_rows_per_family=policy.max_rows_per_family)
    root = rows.snapshot
    batch = '88888888-8888-4888-8888-888888888888'
    prefix = SimpleNamespace(status='running', last_sequence=root['checkpoint_sequence'],
        last_batch_id=batch, batch_ids=(batch,))
    cursor = dict(run_id=root['run_id'], event_sequence=root['checkpoint_sequence'],
        batch_id=batch, boundary_ms=root['boundary_ms'], session_date=root['session_date'])
    head = manager.ManagerSnapshotHead(root['run_id'], root['checkpoint_sequence'], batch,
                                       root['content_hash'], 0)
    if defect == 'prefix': prefix.status = 'completed'
    if defect == 'cursor': cursor['boundary_ms'] += 100
    if defect == 'hash': head = replace(head, snapshot_hash='0' * 64)

    monkeypatch.setattr(arte_journal_commit_v4, 'load_verified_v4_prefix', lambda *_a, **_k: prefix)
    monkeypatch.setattr(arte_journal_projection, 'load_latest_backtest_cursor', lambda *_a: cursor)
    # Journal source attachment is a separate tested authority; this fixture
    # exercises head fencing plus the real normalized reader and recomputation.
    monkeypatch.setattr(manager, 'attach_committed_momentum_sources', lambda _c, _p, state: state)

    class Keeper:
        reads = 0
        def read_head(self, **_scope):
            self.reads += 1
            return replace(head, keeper_version=1) if defect == 'head_changed' and self.reads > 1 else head

    client=Reader(rows,policy)
    if defect=='policy':client.native_channel_management_policy='invalid-declared-policy'
    dedicated=lambda: load_current_native_inherited_checkpoint(client,Keeper(),
        run_id=root['run_id'],checkpoint_sequence=root['checkpoint_sequence'],
        policy=policy,first_price_source=None)
    def shared():
        state=manager.load_attested_manager_snapshot(client,Keeper(),run_id=root['run_id'],
            checkpoint_sequence=root['checkpoint_sequence'],first_price_source=None)
        cold=manager.load_unattested_manager_snapshot_rows(client,run_id=root['run_id'],
            checkpoint_sequence=root['checkpoint_sequence'])
        assert cold.snapshot==root
        assert cold.snapshot['content_hash']!=cold.native_rows.inherited.snapshot['content_hash']
        assert manager.restore_manager_snapshot(cold)==state
        from src.backend.backtest_native_channel_inherited_checkpoint import NativeInheritedCheckpoint
        return NativeInheritedCheckpoint(cold.native_rows,state)
    call=dedicated if route=='dedicated' else shared
    if defect is not None:
        with pytest.raises((RuntimeError, ValueError)): call()
    else:
        image=call()
        assert image.rows.snapshot==root
        assert image.inherited.boundary_ms==root['boundary_ms']
        assert not image.rows.pending


@pytest.mark.parametrize('defect',[None,'write','publisher','source_changed','history_changed'])
def test_history_reader_requires_readonly_exact_publisher_and_stable_history(installed,defect):
    from src.backend.backtest_native_channel_inherited_checkpoint import bind_native_inherited_checkpoint_history
    from src.backend.backtest_typed_publisher import BacktestTypedJournalPublisher
    from src.backend.backtest_native_channel_publisher_source import NativeChannelExitPublisherSource
    rows,policy,_=manager_case(installed)
    run=rows.snapshot['run_id']
    reader=SimpleNamespace(native_channel_management_policy=policy,
        execute=lambda sql:'0' if defect=='write' else '1')
    from tests.test_backtest_strategy_first_price_source import authority, Bars
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority, compile_certified_price_break_plan
    market,parent=authority()
    price=CertifiedPriceReadbackAuthority(run,
        compile_certified_price_break_plan(load_first_price_source(market,parent,client=Bars())))
    keeper=SimpleNamespace(read_head=lambda **scope:None)
    source=NativeChannelExitPublisherSource(run,reader,keeper,keeper,policy,price)
    publisher=BacktestTypedJournalPublisher.__new__(BacktestTypedJournalPublisher)
    publisher.journal=SimpleNamespace(run_id=run)
    publisher._first_price_source=price
    publisher._native_channel_exit_source=source
    if defect=='publisher':publisher=SimpleNamespace(**publisher.__dict__)
    if defect in ('write','publisher'):
        with pytest.raises(ValueError):
            bind_native_inherited_checkpoint_history(reader,publisher=publisher,run_id=run)
        return
    bound=bind_native_inherited_checkpoint_history(reader,publisher=publisher,run_id=run)
    assert bound.native_channel_checkpoint_history==()
    if defect=='source_changed':publisher._native_channel_exit_source=None
    if defect=='history_changed':source._contexts['unexpected']=object()
    if defect is not None:
        with pytest.raises(ValueError):_ = bound.native_channel_checkpoint_history
    else:
        assert bound.native_channel_management_policy==policy
        assert bound.execute("SELECT getSetting('readonly')")=='1'
