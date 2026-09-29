from datetime import date
import json

import pytest

from research.rl_trading.v1.common import file_hash
from research.rl_trading.v6.replay_artifacts import save_replay
from research.rl_trading.v6.replay_metrics import ReplayJournal
from research.rl_trading.v6.oms import BracketAccount
from research.rl_trading.v6.session_data import PackedSession


def test_replay_save_is_hash_bound_and_sealed_selection_is_required(tmp_path):
    checkpoint = tmp_path / 'model.pt'
    checkpoint.write_bytes(b'test checkpoint')
    evidence = tmp_path / 'quote-complete.json'
    evidence.write_text(json.dumps({'status': 'complete',
                                    'day': '2026-07-31'}))
    journal = ReplayJournal(BracketAccount())
    journal.mark(1_000_000, {}, {})
    session = PackedSession(date(2026, 7, 31), 'train',
        tmp_path / 'bank', 'bank-hash', None, None, ('A',))
    args = dict(runtime_root=tmp_path, source_commit='abcdef123456',
                quote_evidence_certificate=evidence)
    root, report = save_replay(journal, session, checkpoint, **args)
    assert report['modeled_net_profit'] == 0
    assert (root / 'orders.parquet').exists()
    assert save_replay(journal, session, checkpoint, **args)[0] == root
    (root / 'metrics.json').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        save_replay(journal, session, checkpoint, **args)
    heldout = PackedSession(date(2026, 8, 26), 'sealed_test',
        tmp_path / 'heldout', 'heldout-hash', None, None, ('A',))
    evidence.write_text(json.dumps({'status': 'complete',
                                    'day': '2026-08-26'}))
    with pytest.raises(ValueError, match='selection'):
        save_replay(journal, heldout, checkpoint, **args)
    chosen = tmp_path / 'selected.json'
    chosen.write_text(json.dumps({'status': 'selected_on_development',
                                  'checkpoint_sha256': file_hash(checkpoint)}))
    root, _ = save_replay(journal, heldout, checkpoint,
                          selection_certificate=chosen, **args)
    assert root.exists()
