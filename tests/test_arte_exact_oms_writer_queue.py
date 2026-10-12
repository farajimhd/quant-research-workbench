from threading import Event, get_ident

import pytest

from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime import arte_oms_exact_prices as exact_module
from src.trading_runtime import rule_decision_writer_preflight as audit
from src.trading_runtime.arte_journal_commit_v4 import publish_base_typed_batch_v4
from src.trading_runtime.arte_oms_projection import oms_group_state_batch
from tests.test_arte_oms_exact_prices import prepared
from tests.test_arte_journal_commit_v4 import attached_v4_client


@pytest.mark.parametrize('failure', [False, True])
def test_exact_oms_queue_is_nonblocking_bounded_and_worker_owned(monkeypatch, failure):
    group, kwargs, _ = prepared()
    unit = oms_group_state_batch(group, **kwargs, exact_prices=True)
    client = attached_v4_client()
    publish_base_typed_batch_v4(client, kwargs['published_intent_batch'])
    monkeypatch.setattr(writer_module, '_v4_preflight', lambda actual: None)
    monkeypatch.setattr(writer_module, '_verify_run_identity', lambda actual, run: {
        'mode': 'backtest', 'account_ids': ('DU1',)})
    monkeypatch.setattr(audit, 'require_rule_decision_writer_audit', lambda actual: None)
    entered, release = Event(), Event()
    owner = get_ident()
    workers = []
    publish = exact_module.publish_exact_oms_state_batch_v4
    def gated(actual, queued):
        workers.append(get_ident())
        entered.set()
        assert release.wait(5), 'Test publication gate was not released'
        if failure:
            raise RuntimeError('synthetic exact OMS writer failure')
        return publish(actual, queued)
    monkeypatch.setattr(exact_module, 'publish_exact_oms_state_batch_v4', gated)
    writer = writer_module.ArteJournalWriter(client, run_id=unit.base.run_id,
        journal_profile='backtest_v4', capacity=1, coalesce_batches=False)
    try:
        receipt = writer.submit_exact_oms_v4(unit)
        assert entered.wait(2)
        assert not receipt.done()
        pending = writer.submit_exact_oms_v4(unit)
        with pytest.raises(writer_module.JournalQueueFull):
            writer.submit_exact_oms_v4(unit)
        assert not pending.done()
        assert 'trading_oms_group_state_exact_price_v1' not in client.tables
        release.set()
        if failure:
            with pytest.raises(RuntimeError, match='synthetic exact OMS'):
                receipt.result(timeout=5)
            with pytest.raises(RuntimeError, match='failed earlier'):
                pending.result(timeout=5)
            with pytest.raises(RuntimeError, match='closed or failed'):
                writer.submit_exact_oms_v4(unit)
            assert 'trading_oms_group_state_exact_price_v1' not in client.tables
        else:
            assert receipt.result(timeout=5) == unit.base.batch_id
            assert pending.result(timeout=5) == unit.base.batch_id
        assert workers and all(thread != owner for thread in workers)
    finally:
        release.set()
        if failure:
            with pytest.raises(RuntimeError):
                writer.close()
        else:
            writer.close()
