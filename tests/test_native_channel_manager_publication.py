from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from tests.test_backtest_native_channel_manager_recovery import manager_case
from tests.test_native_channel_manager_reader import Reader
from src.trading_runtime.native_channel_manager_publication import (
    issue_native_channel_manager_publication, require_native_channel_manager_publication,
    publication_contracts,
)


def issuance_case(installed, monkeypatch):
    from src.trading_runtime import arte_journal_commit_v4, arte_journal_projection
    from src.trading_runtime import strategy_one_management_snapshot as manager
    rows, policy, contexts = manager_case(installed)
    root, batch = rows.snapshot, rows.pending[0]['batch_id']
    prefix = SimpleNamespace(status='running', last_sequence=root['checkpoint_sequence'], last_batch_id=batch)
    cursor = dict(run_id=root['run_id'], event_sequence=root['checkpoint_sequence'], batch_id=batch,
        boundary_ms=root['boundary_ms'], session_date=root['session_date'])
    monkeypatch.setattr(arte_journal_commit_v4, 'load_writer_v4_snapshot_prefix', lambda *_a, **_k: prefix)
    monkeypatch.setattr(arte_journal_projection, 'load_latest_backtest_cursor', lambda *_a: cursor)
    attached = []
    monkeypatch.setattr(manager, 'attach_committed_momentum_sources',
                        lambda _c, _p, state, **_kw: attached.append(state))
    return Reader(rows, policy), rows, policy, contexts, batch, prefix, cursor, attached


def test_issued_image_is_complete_root_last_client_bound_and_immutable(installed, monkeypatch):
    client, rows, policy, contexts, batch, _, _, attached = issuance_case(installed, monkeypatch)
    image = issue_native_channel_manager_publication(client, rows, policy=policy, contexts=contexts,
        journal_batch_id=batch)
    saved = require_native_channel_manager_publication(image, client=client)
    assert set(saved) == {t.name for t in publication_contracts()}
    assert publication_contracts()[-1].name == 'trading_strategy_one_manager_snapshot_v5'
    assert attached and len(saved['strategy_native_channel_pending_v1']) == 1
    saved['strategy_native_channel_pending_v1'].clear()
    assert len(require_native_channel_manager_publication(image, client=client)['strategy_native_channel_pending_v1']) == 1
    with pytest.raises(ValueError): require_native_channel_manager_publication(image, client=Reader(rows, policy))
    with pytest.raises(ValueError): require_native_channel_manager_publication(replace(image), client=client)
    with pytest.raises(ValueError): require_native_channel_manager_publication(replace(image, sequence=image.sequence+1), client=client)


@pytest.mark.parametrize('defect', ['prefix', 'cursor', 'evidence', 'batch', 'policy'])
def test_issuance_requires_full_independent_evidence_at_actual_frontier(installed, monkeypatch, defect):
    client, rows, policy, contexts, batch, prefix, cursor, _ = issuance_case(installed, monkeypatch)
    if defect == 'prefix': prefix.last_sequence += 1
    if defect == 'cursor': cursor['boundary_ms'] += 100
    if defect == 'evidence': contexts = {}
    if defect == 'batch': batch = '88888888-8888-4888-8888-888888888888'
    if defect == 'policy': client.native_channel_management_policy = None
    with pytest.raises(ValueError):
        issue_native_channel_manager_publication(client, rows, policy=policy, contexts=contexts, journal_batch_id=batch)
