"""Same-population, full-session GPU qualification for the capture-cache fix."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('OMP_NUM_THREADS','1')
import argparse,json,subprocess,sys
from pathlib import Path


def qualify(campaign,output):
    from .run_search import source_hash,digest
    from .diversity import VERSION
    from research.vectorized_backtest.v6.torch_backtest.runtime import require_runtime,file_hash,write_json
    campaign=Path(campaign);output=require_runtime(output)
    if (campaign/'owner.lock').exists() or (campaign/'controller.lock').exists():raise ValueError('Stop campaign before profiling')
    checkpoint_hash=file_hash(campaign/'checkpoint.json')
    saved=json.loads((campaign/'checkpoint.json').read_text());c=saved['contract']
    old=Path(json.loads((campaign/'active.json').read_text())['code']);new=Path(__file__).resolve().parents[3]
    # Only cache residency/diagnostics change. Bind all financial, feature,
    # history, rule-program and strategy-generation engines byte-for-byte.
    names=['evaluator.py','replay_graph.py','genome.py','features.py','data.py','resident_features.py','swing_projection.py','io_pipeline.py','diversity.py']
    unchanged={name:file_hash(new/'research/vectorized_backtest/v7'/name) for name in names}
    if any(file_hash(old/'research/vectorized_backtest/v7'/name)!=checksum for name,checksum in unchanged.items()):raise ValueError('Semantic execution engine changed')
    script=new/'research/vectorized_backtest/v7/qualify_diversity.py'
    for label,code in [('old',old),('new',new)]:
        subprocess.run([sys.executable,'-B','-P',str(script),'--campaign',str(campaign),
            '--output',str(output/label),'--baseline'],cwd=code,
            env=dict(os.environ,PYTHONPATH=str(code),PYTHONDONTWRITEBYTECODE='1'),check=True)
    reports={label:json.loads((output/label/'baseline.json').read_text()) for label in ('old','new')}
    if reports['old']['source_sha256']!=c['source_sha256'] or reports['new']['source_sha256']!=source_hash():raise ValueError('Source changed')
    totals={}
    for label in ('old','new'):
        rows=[]
        for repeat in (1,2):
            for day in reports['old']['days']:
                root=output/label/f'generation-{repeat:04d}'/day
                rows.extend(json.loads((root/'timings.json').read_text()))
                left=json.loads((output/'old'/f'generation-{repeat:04d}'/day/'receipt.json').read_text())
                right=json.loads((output/'new'/f'generation-{repeat:04d}'/day/'receipt.json').read_text())
                if left!=right:raise ValueError('Full-session metric/input receipt mismatch')
        totals[label]={key:sum(row.get(key,0) for row in rows) for key in
            ('rule_seconds','replay_seconds','capture_builds','capture_hits','capture_evictions','capture_seconds')}
        totals[label]['maximum_capture_bytes']=max(row['capture_bytes'] for row in rows)
    if digest(saved['population'])!=saved['population_sha256']:raise ValueError('Population changed')
    if file_hash(campaign/'checkpoint.json')!=checkpoint_hash:raise ValueError('Checkpoint changed')
    old_wall=reports['old']['seconds'][-1];new_wall=reports['new']['seconds'][-1]
    passed=new_wall<old_wall*.9
    result=dict(status='passed' if passed else 'failed',no_slowdown=passed,exact_metrics=True,full_session=True,
        validation_opened=False,source_sha256=source_hash(),old_source_sha256=c['source_sha256'],
        checkpoint_sha256=checkpoint_hash,population_sha256=saved['population_sha256'],
        candidates=c['population'],batch_size=c['batch_size'],session_workers=c['session_workers'],execution=c['execution'],
        diversity_version=VERSION,unchanged_engines=unchanged,old_seconds=reports['old']['seconds'],new_seconds=reports['new']['seconds'],
        speedup=old_wall/new_wall,diagnostics=totals,training_days=reports['old']['days'],minimum_speedup=1/.9)
    write_json(output/'qualification.json',result)
    print(json.dumps(result),flush=True)
    if not passed:raise ValueError('Cache fix did not pass speedup gate; launch forbidden')


def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();qualify(a.campaign,a.output)


if __name__=='__main__':main()
