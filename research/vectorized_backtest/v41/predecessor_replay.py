"""Freeze V40 generation-100 training leaders, then replay Aug 5 once."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for _key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[_key]='1'
import argparse,importlib,json,queue,time,hashlib
from pathlib import Path
import numpy as np
from .deferred_launch import read,write,sha,digest,live


def leaders(scores,profits,limit=20):
    """Training-only objective ranking, stable serial ties; no replay feedback."""
    ids=np.asarray(scores['ids']);values=np.asarray(scores['scores']);eligible=np.asarray(scores['eligible'],bool)
    if not (ids.shape==values.shape==eligible.shape) or len(profits)!=len(ids):raise ValueError('Training rank alignment changed')
    indexes=np.lexsort((ids,-values));indexes=indexes[eligible[indexes]]
    if len(indexes)<limit:raise ValueError('Fewer than 20 eligible training candidates')
    return indexes[:limit].tolist()


def run(job):
    old=job['predecessor'];campaign=Path(old['campaign']);local=campaign/'local/2026-08-04';root=Path(job['root'])/'diagnostics/v40-g100-aug05'
    if root.exists():raise ValueError('V40 generation100 replay evidence already exists; inspect before retry')
    if live(old['owner']) or list(campaign.rglob('owner.lock')):raise RuntimeError('V40 still owns optimizer resources')
    root.mkdir();checkpoint=read(local/'checkpoint.json');state=checkpoint['state'];contract=checkpoint['contract']
    if state['completed']!=100 or digest(state)!=checkpoint['state_sha256']:raise ValueError('V40 checkpoint invalid')
    engine=importlib.import_module('research.vectorized_backtest.v40.run_search')
    if engine.source_hash()!=contract['source_sha256'] or contract['source_sha256']!=old['owner']['source_sha256']:raise ValueError('V40 immutable source changed')
    folder=local/'generation-0100';seal=read(folder/'complete.json');receipt=read(folder/'2026-08-04/receipt.json');scores=read(folder/'scores.json')
    if sha(folder/'complete.json')!=state['seals']['100'] or sha(folder/'scores.json')!=seal['scores_sha256'] or sha(folder/'2026-08-04/receipt.json')!=seal['receipt_sha256']:raise ValueError('Training selection evidence changed')
    chosen=leaders(scores,receipt['metrics']['net_pnl']);pool={int(i):p for i,p in zip(state['ids'],state['population'])}
    from research.vectorized_backtest.v40.genome import Individual
    from research.vectorized_backtest.v40.lineage import program_key
    pedigree={int(r['serial']):r for r in state['pedigree']};records=[]
    for rank,i in enumerate(chosen,1):
        serial=int(scores['ids'][i]);payload=pool[serial];key=program_key(Individual.restore(payload))
        if pedigree[serial]['program_sha256']!=key:raise ValueError('Selected genome identity mismatch')
        records.append(dict(rank=rank,serial=serial,canonical_sha256=key,payload=payload,training_objective=scores['scores'][i],training_metrics={k:v[i] for k,v in receipt['metrics'].items()}))
    frozen=dict(generation=100,selection='top20 eligible training objective; stable serial ties',checkpoint_sha256=sha(local/'checkpoint.json'),state_sha256=checkpoint['state_sha256'],source_sha256=contract['source_sha256'],records=records,aug05_observed=False)
    write(root/'frozen.json',frozen)
    # Reuse previously copied certified data bytes, never previous replay results.
    evidence=Path(job['aug05_copy_proof'])
    if sha(evidence)!=job['aug05_copy_proof_sha256']:raise ValueError('Aug5 copy authority changed')
    for item in read(evidence)['files']:
        h=hashlib.sha256()
        with Path(item['target']).open('rb') as stream:
            for block in iter(lambda:stream.read(16*1024**2),b''):h.update(block)
        if h.hexdigest()!=item['sha256']:raise ValueError('Aug5 certified input changed')
    cfg=read(campaign/'manifest.json')['config'];output=root/'evaluation'
    import torch
    cfg.update(output=str(output),source_sha256=contract['source_sha256'],batch_size=20,split='training',reuse_results=False,retain_session_residency=False,maximum_host_sessions=2,plan_limit=1,compile_cache_root=str(root/'compiler-cache'),host_reserve_gib=12.,host_admission_gib=8.,lane_memory_bytes=int(torch.cuda.mem_get_info()[0]*.8),execution=contract['execution'],feature_cache=None)
    for key in ('inputs','history','indicator_root','enriched_root'):cfg[key]=str(Path(job['aug05_data'])/key)
    from research.vectorized_backtest.v40.session_lanes import Worker
    payload=[r['payload'] for r in records];worker=Worker(cfg,queue.Queue(maxsize=1),0);started=time.perf_counter()
    try:
        data=worker.load('2026-08-05');worker.run('2026-08-05',None,payload,1,digest(payload),len(data.listing_ids))
    finally:worker.close()
    result=output/'generation-0001/2026-08-05/receipt.json';evaluated=read(result);m=evaluated['metrics']
    if not evaluated['full_session'] or evaluated['reused_candidates']!=0 or evaluated['evaluated_candidates']!=20 or evaluated['population_sha256']!=digest(payload) or evaluated['execution']!=contract['execution']:raise ValueError('Aug5 replay incomplete')
    rows=[]
    for i,r in enumerate(records):
        profits=m['episode_profits'][i]
        if not np.isclose(sum(profits),m['net_pnl'][i],rtol=1e-10,atol=1e-7) or len(profits)!=m['closed_positions'][i] or not m['terminal_valid'][i]:raise ValueError('Aug5 ledger failed reconciliation')
        if m['maximum_position_risk'][i]>300+1e-7 or m['maximum_stop_distance'][i]>.2+1e-12:raise ValueError('Aug5 execution limits changed')
        rows.append(dict(rank=r['rank'],serial=r['serial'],aug04_profit=r['training_metrics']['net_pnl'],aug04_objective=r['training_objective'],aug05_profit=m['net_pnl'][i],aug05_drawdown=m['drawdown'][i],aug05_closed=m['closed_positions'][i],aug05_wins=m['winning_positions'][i]))
    write(root/'comparison.json',dict(rows=rows,positive_aug05=sum(r['aug05_profit']>0 for r in rows),median_aug05_profit=float(np.median(m['net_pnl'])),median_aug05_drawdown=float(np.median(m['drawdown'])),prior_project_training_exposure=True,no_reproduction=True,no_selection_feedback=True))
    write(root/'complete.json',dict(generation=100,candidates=20,source_sha256=contract['source_sha256'],receipt_sha256=sha(result),frozen_sha256=sha(root/'frozen.json'),comparison_sha256=sha(root/'comparison.json'),full_session=True,no_selection_feedback=True,ledger_verified=True,seconds=time.perf_counter()-started,epoch=time.time()))


def main():
    p=argparse.ArgumentParser();p.add_argument('--job',type=Path,required=True);a=p.parse_args();run(read(a.job))


if __name__=='__main__':main()
