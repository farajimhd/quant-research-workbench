"""Transport reconstruction fixtures; no real database authority is granted."""
import pytest
from test_arte_running_financial_checkpoint import case, RUN, BATCH
from src.trading_runtime import arte_running_financial_checkpoint_readback as subject
from src.trading_runtime.arte_running_financial_checkpoint import project_running_financial_checkpoint
from src.trading_runtime.arte_portfolio_snapshot import _snapshot_rows, _state_hash, _restore_state


@pytest.mark.parametrize('mutation', ['none', 'unpublished', 'broker_hash', 'root_batch', 'account_hash', 'missing', 'duplicate', 'portfolio_hash', 'config', 'revision_float', 'ordinal_bool'])
def test_reader_reconstructs_references_and_rejects_changed_graph(monkeypatch, mutation):
    args = case()
    projected = project_running_financial_checkpoint(**args)
    root, accounts = dict(projected.root), [dict(r) for r in projected.accounts]
    snapshots = {}
    for p in args['portfolios']:
        families = _snapshot_rows(p.run_id,p.account_id,p.state_revision,p.snapshot_month,p.rows)
        snapshots[p.account_id] = dict(state_hash=_state_hash(families),
            snapshot_at=p.rows.account['snapshot_at'], state=_restore_state(families,None))
    if mutation == 'broker_hash': root['broker_snapshot_hash'] = 'f'*64
    elif mutation == 'root_batch': root['batch_id'] = '44444444-4444-4444-8444-444444444444'
    elif mutation == 'account_hash': accounts[0]['content_hash'] = 'f'*64
    elif mutation == 'missing': accounts.pop()
    elif mutation == 'duplicate': accounts[1] = dict(accounts[0])
    elif mutation == 'portfolio_hash': snapshots['a']['state_hash'] = 'f'*64
    elif mutation == 'revision_float': accounts[0]['state_revision'] = 7.0
    elif mutation == 'ordinal_bool': accounts[0]['ordinal'] = False
    monkeypatch.setattr(subject,'load_verified_v4_prefix',lambda *a,**k: args['prefix'])
    monkeypatch.setattr(subject,'verified_batch_predecessor',lambda *a,**k: None)
    monkeypatch.setattr(subject,'load_verified_commit_v4',lambda *a,**k: (
        dict(last_sequence=7,status='running',source_cursor=args['prefix'].source_cursor),()))
    monkeypatch.setattr(subject,'load_latest_backtest_cursor',lambda *a,**k: args['cursor'])
    monkeypatch.setattr(subject,'load_typed_run_context',lambda *a,**k: dict(mode='backtest',
        account_ids=('a','b'),configuration_hash=('f'*64 if mutation=='config' else args['configuration_hash'])))
    monkeypatch.setattr(subject,'load_unattested_broker_match_snapshot',lambda *a,**k: args['broker'])
    monkeypatch.setattr(subject,'load_portfolio_snapshot',lambda *a,**k: snapshots[k['account_id']])
    queries=[]
    def rows(client,sql):
        queries.append(sql)
        return accounts if 'checkpoint_account_v1' in sql else [root]
    monkeypatch.setattr(subject,'_rows',rows)
    if mutation in ('none', 'unpublished'):
        reader = (subject.reconstruct_running_financial_products if mutation == 'unpublished'
                  else subject.load_running_financial_checkpoint)
        result=reader(object(),run_id=RUN,batch_id=BATCH,checkpoint_sequence=7)
        assert result.rows == projected
        assert all('LIMIT ' in q and 'SELECT ' in q for q in queries)
        if mutation == 'unpublished': assert queries == []
    else:
        with pytest.raises((ValueError,RuntimeError)):
            subject.load_running_financial_checkpoint(object(),run_id=RUN,batch_id=BATCH,checkpoint_sequence=7)
