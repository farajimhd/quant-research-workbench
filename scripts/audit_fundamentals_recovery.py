"""Reconcile a bounded gap scope without rerunning the completed backfill."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,time
from datetime import datetime,timezone,date
from contextlib import closing
from scripts.run_fundamentals import Client,save,lock
from pipelines.fundamentals.recovery import write_queue,validate_ids
from pipelines.fundamentals.progress import render
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scope',type=Path,required=True)
    p.add_argument('--population',type=Path,required=True)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--start',type=date.fromisoformat,required=True)
    p.add_argument('--end',type=date.fromisoformat,required=True)
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    a=p.parse_args();root=a.run_dir.resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Required runtime is unavailable')
    scope=json.loads(a.scope.read_text(encoding='utf-8-sig'));validate_ids(scope)
    population=json.loads(a.population.read_text(encoding='utf-8-sig'))
    if not set(scope)<=set(population['families']['sec_bulk']):raise ValueError('Recovery outside reviewed population')
    root.mkdir(parents=True,exist_ok=True);began=time.monotonic()
    state=dict(state='running',mode='recovery review',unit='issuers',total=len(set(scope)),queued=len(set(scope)),counts={},
        active={'review':dict(family='coverage',stage='bounded metadata and server aggregation',started=0)},workers=1)
    def progress(done,total):
        counts=dict(audited=done) if 'recovery' not in state else dict(audited=state['recovery']['updated'],unchanged=state['recovery']['unchanged'])
        state.update(counts=counts,queued=total-done,elapsed=time.monotonic()-began,updated_at=datetime.now(timezone.utc).isoformat(),
            qualification_status='Coverage review only; pending semantic decisions have no completion ETA.')
        save(root/'status.json',state);print(render(state),flush=True)
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    with lock(root/'controller.lock'),closing(Client(a.endpoint)) as client:
        progress(0,len(set(scope)))
        try:
            result=write_queue(client.query,root,scope,a.start,a.end,datetime.now(timezone.utc),save=save,progress=progress)
            state.update(state='complete',review_complete=True,full_backfill_complete=False,recovery=result,active={},blocked=result['pending_review'])
            progress(len(set(scope)),len(set(scope)))
            return 0
        except Exception as exc:
            state.update(state='failed',error=str(exc)[:2000],active={});progress(state['counts'].get('audited',0),len(set(scope)))
            raise


if __name__=='__main__':raise SystemExit(main())
