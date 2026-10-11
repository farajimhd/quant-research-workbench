"""Observer data isolation, exact candidate pairing and real offline SDK path."""
import json
from pathlib import Path
import subprocess
import sys
import os
import pytest
from research.vectorized_backtest.v8.wandb_metrics import packet
from research.vectorized_backtest.v6.torch_backtest.runtime import write_json


def fixture(root):
    train=root/'training/generation-0001';valid=root/'validation/generation-0001'
    train.mkdir(parents=True);valid.mkdir(parents=True)
    def row(serial,rank,profit):
        return dict(serial=serial,rank=rank,candidate_id=f'G001-{serial}',score=profit,
            metrics=dict(complexity=20,total_pnl=profit,trimmed_profit=profit*.9,
                trade_tail_loss=2,session_tail_mean_pnl=-5,worst_drawdown=10,
                profitable_day_fraction=.5,active_sessions=2,closed_positions=4,reused_sessions=1),
            private_trade_log='MUST_NOT_UPLOAD')
    rows=[row(100,1,300),row(200,2,100)]
    observations=[row(200,1,1000),row(100,2,-60)]
    observations[0]['training_rank']=2;observations[1]['training_rank']=1
    write_json(train/'ranking.json',rows);write_json(valid/'ranking.json',observations)
    write_json(train/'complete.json',dict(validation_feedback=False,session_receipts={str(i):'hash' for i in range(30)}))
    write_json(valid/'complete.json',dict(validation_feedback=False,display_only=True,session_receipts={str(i):'hash' for i in range(6)}))
    write_json(train/'diversity.json',dict(crossovers=6,unrelated_mate_fallbacks=2))
    write_json(train/'wall.json',dict(full_generation_seconds=15,validation_seconds=3))
    write_json(root/'status.json',dict(status='complete',completed_generations=1))
    return root


def test_metrics_same_training_leader_and_allowlist(tmp_path):
    data=packet(fixture(tmp_path/'campaign'),1)
    assert data['train/net_profit_per_session']==10
    assert data['validation/net_profit_per_session']==-10  # Not the validation winner.
    assert data['leader/id']=='G001-100'
    assert data['selection/unrelated_mate_fallback_rate']==.25
    assert data['train/shortlist_median_trimmed_per_session']==6
    assert 'MUST_NOT_UPLOAD' not in json.dumps(data)
    assert len(data)<=32


def test_partial_generation_not_published(tmp_path):
    root=fixture(tmp_path/'campaign');(root/'training/generation-0001/wall.json').unlink()
    assert packet(root,1) is None


def test_split_identity_fails_closed(tmp_path):
    root=fixture(tmp_path/'campaign');p=root/'validation/generation-0001/ranking.json'
    rows=json.loads(p.read_text());rows[1]['candidate_id']='other';write_json(p,rows)
    with pytest.raises(ValueError,match='identity'):packet(root,1)


def test_offline_sdk_cli_no_optimizer_or_extra_uploads(tmp_path):
    root=fixture(tmp_path/'campaign');output=tmp_path/'observer'
    command=[sys.executable,'-B','-m','research.vectorized_backtest.v8.wandb_observer',
             '--campaign',str(root),'--output',str(output),'--mode','offline']
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',WANDB_MODE='offline')
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    assert json.loads((output/'status.json').read_text())['last_generation']==1
    assert len(list((output/'summaries').glob('*.json')))==1
    assert not list((output/'sdk').rglob('output.log'))
    assert not list((output/'sdk').rglob('requirements.txt'))
    assert not list((output/'sdk').rglob('wandb-metadata.json'))
    assert not (output/'observer.lock').exists()


def test_sdk_failure_isolated_and_summary_retained(tmp_path,monkeypatch):
    from research.vectorized_backtest.v8.wandb_observer import upload
    import wandb
    root=fixture(tmp_path/'campaign');output=tmp_path/'observer'
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    def fail(**kwargs):raise TimeoutError('PRIVATE_KEY_VALUE')
    monkeypatch.setattr(wandb,'init',fail)
    with pytest.raises(RuntimeError,match='local summaries retained'):
        upload(root,output)
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert (output/'summaries/generation-0001.json').exists()
    assert 'PRIVATE_KEY_VALUE' not in (output/'status.json').read_text()


def test_observer_import_has_no_compute_dependency():
    result=subprocess.run([sys.executable,'-B','-c',
        "import sys; import research.vectorized_backtest.v8.wandb_observer; assert 'torch' not in sys.modules; assert 'wandb' not in sys.modules"],
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


def test_resume_uses_sdk_history_cursor(tmp_path,monkeypatch):
    import shutil
    import wandb
    from research.vectorized_backtest.v8.wandb_observer import upload
    root=fixture(tmp_path/'campaign')
    for role in ('training','validation'):
        shutil.copytree(root/role/'generation-0001',root/role/'generation-0002')
    write_json(root/'status.json',dict(status='complete',completed_generations=2))
    class Run:
        step=2;url='https://example.invalid/run'
        def define_metric(self,*a,**kw):pass
        def log(self,value,step,commit):logged.append(step)
        def finish(self,**kw):pass
    logged=[];monkeypatch.setattr(wandb,'init',lambda **kwargs:Run())
    upload(root,tmp_path/'observer')
    assert logged==[2]
