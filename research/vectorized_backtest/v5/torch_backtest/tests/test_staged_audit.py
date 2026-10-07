"""Synthetic controller/audit/freeze/once-only validation contracts."""
import json
from pathlib import Path
import torch
import pytest
from research.vectorized_backtest.v5.torch_backtest import staged_search as controller,staged_validation as validation,input_audit
from research.vectorized_backtest.v5.torch_backtest.staged_audit import audit,require_frozen
from research.vectorized_backtest.v5.torch_backtest.runtime import code_hash,file_hash,write_json
from research.vectorized_backtest.v5.torch_backtest.run_search import fingerprint,state
from research.vectorized_backtest.v5.torch_backtest.input_authority import authorize_day


@pytest.fixture
def scenario(tmp_path,monkeypatch):
    entries=[]
    for i in range(36):
        folder=tmp_path/f'input_{i:02d}'
        entries.append(dict(day=f'day{i:02d}',execution_root=str(folder/'broker'),feature_root=str(folder/'features'),
                            identity_map=str(folder/'map.json'),split_certificate=str(folder/'split.json')))
    spec=tmp_path/'sessions.json';spec.write_text(json.dumps(dict(training=entries[:30],validation=entries[30:])))
    schedule=tmp_path/'schedule.json';schedule.write_text(json.dumps([dict(end_generation=1,population=10,sessions=3,archive_top=2,archive_random=1),dict(end_generation=2,population=10,sessions=6,archive_top=2,archive_random=1)]))
    qualification=tmp_path/'qualification.json';qualification.write_text(json.dumps(dict(status='passed',code_hash=code_hash(),sessions_sha256=file_hash(spec),batch_size=128)))
    witness=tmp_path/'synthetic-training-bytes';witness.write_text('synthetic bytes only')
    verified=[]
    def verify_input(session,binding,hashes):
        assert session['day']==binding['day'];verified.append(session['day']);hashes.verify(witness)
    monkeypatch.setattr(input_audit,'verify_session',verify_input)
    monkeypatch.setattr(controller,'preflight',lambda spec:None)
    monkeypatch.setattr(controller,'searchable_features',lambda *_:None)
    def binding(session):return dict(day=session['day'],execution={},feature_certificate='synthetic',prior_certificate=None,
                                    identity_map_sha256='synthetic',split_certificate_sha256='synthetic',previous_split_certificate_sha256=None)
    def loader(session,**kwargs):return None,None,None,[],binding(session)
    monkeypatch.setattr(controller,'load_session',loader);monkeypatch.setattr(validation,'load_session',loader)
    class Evaluator:
        def __init__(self,*_):pass
        def close(self):pass
        def evaluate(self,session,population,destination,prepared,emit):
            destination.mkdir(parents=True,exist_ok=True);n=len(population)
            metrics={key:[0.]*n for key in ('net_pnl','fees','open_quantity','open_positions','sold_shares','sold_share_seconds','positions_opened','drawdown','stop_risk_dollar_seconds','capital_dollar_seconds','filled_batches','fill_count','closed_positions','winning_positions','losing_positions','gross_profit','gross_loss')}
            metrics.update(cash=[10000.]*n,terminal_valid=[True]*n,closed_position_duration_samples=[[] for _ in range(n)])
            folder=destination/'batch_0000';folder.mkdir(exist_ok=True)
            torch.save(dict(ledger=torch.empty(n,0,9,dtype=torch.float64),counts=torch.zeros(n,dtype=torch.int64)),folder/'fills.pt')
            pop=fingerprint([state(v) for v in population]);source=binding(session)
            write_json(folder/'receipt.json',dict(candidate_start=0,candidate_count=n,population_sha256=pop,session_binding_sha256=fingerprint(source),metrics=metrics,ledger_sha256=file_hash(folder/'fills.pt')))
            receipt=dict(source,population_sha256=pop,profile_seconds=None,metrics=metrics,timing={'replay':0.},batch_receipts=[dict(directory='batch_0000',sha256=file_hash(folder/'receipt.json'))])
            write_json(destination/'receipt.json',receipt);return receipt
    monkeypatch.setattr(controller,'BatchedEvaluator',Evaluator);monkeypatch.setattr(validation,'BatchedEvaluator',Evaluator)
    root=tmp_path/'run'
    assert controller.main(['--sessions',str(spec),'--schedule',str(schedule),'--qualification',str(qualification),'--output',str(root),'--device','cpu','--execute'])==0
    return root,spec,entries,verified,witness


def test_staged_rng_and_full_checkpoint_audit_freeze_and_once_only_validation(scenario):
    root,spec,entries,verified,witness=scenario
    with pytest.raises((ValueError,FileNotFoundError)):authorize_day(spec,'day30')
    report=audit(root,freeze=True)
    assert report['generations']==2 and report['stages']==2 and report['full_budget_verified']
    assert set(verified)=={s['day'] for s in entries[:30]}
    identity,frozen=require_frozen(root)
    assert authorize_day(spec,'day30',root/'frozen_winner.json')==entries[30]
    assert validation.main(['--output',str(root)])==3
    for session in entries[30:]:
        for path in (Path(session['execution_root'])/'receipt.json',Path(session['execution_root'])/'tape.pt',Path(session['feature_root'])/'complete.json',Path(session['identity_map']),Path(session['split_certificate'])):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('synthetic sealed input placeholder')
    assert validation.main(['--output',str(root)])==0
    report=json.loads((root/'report.json').read_text())
    assert len(report['receipts'])==6 and not report['validation_tuning']
    assert set(verified)=={s['day'] for s in entries}
    with pytest.raises(ValueError,match='never repeat'):validation.main(['--output',str(root)])


@pytest.mark.parametrize('target',['checkpoint','panel','ledger','finalist'])
def test_audit_rejects_training_tampering(scenario,target):
    root,*_=scenario
    if target=='checkpoint':
        path=root/'checkpoint.json';value=json.loads(path.read_text());value['rng']['state']['state']+=1;write_json(path,value)
    elif target=='panel':
        path=root/'generation_000'/'generation.json';value=json.loads(path.read_text());value['selected_days'][0]='future';write_json(path,value)
    elif target=='ledger':
        path=root/'generation_000'/'session_000'/'batch_0000'/'fills.pt';path.write_bytes(b'corrupt actual fills')
    else:
        path=root/'finalist.json';value=json.loads(path.read_text());value['score']+=1;write_json(path,value)
    with pytest.raises(ValueError):audit(root)


def test_frozen_access_rejects_changed_audited_input(scenario):
    root,spec,entries,verified,witness=scenario
    audit(root,freeze=True);witness.write_text('changed audited bytes')
    with pytest.raises(ValueError,match='input changed'):require_frozen(root)
