"""Evaluate the preselected finalist and assembly on the fixed audit partition."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,gc,json,time
from pathlib import Path
import numpy as np
import torch
from .data import load
from .model import restore
from .gates import check
from ..phase0_v3.experiment import cache
from ..phase1_v11.data import verify
from ..phase1_v25.metrics import evaluate,summary
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import write_json,file_hash


def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--winner',required=True)
    p.add_argument('--revision',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.device='cuda';a.source_revision=a.revision;a.resume=False;preflight(a);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest');start=time.time()
    with lease(a.output):
        selection=json.loads((a.campaign/'retained-comparison.json').read_text())
        if selection['winner']!=a.winner:raise ValueError('Freeze selection before opening audit data')
        train,dev,_,provenance=load(Path('D:/TradingML/runtimes/bar_gpt'))
        dataset=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset');manifest=verify(dataset,('audit',))
        write_json(a.output/'preselection.json',dict(winner=a.winner,source=a.revision,audit_sha=manifest['files']['audit.npy'],
            policy='Audit is excluded from this campaign training and selection. Historical use by earlier campaigns is not asserted absent. No retuning or reselection on this audit.'))
        audit=cache(np.load(dataset/'audit.npy',mmap_mode='r'));results={}
        for name in (selection['rows'][0]['name'],a.winner):
            arm=a.campaign/name;result=json.loads((arm/'result.json').read_text());cp=arm/'checkpoint_best.pt'
            if file_hash(cp)!=result['best_checkpoint_sha']:raise ValueError('Checkpoint changed')
            state=torch.load(cp,map_location='cpu',weights_only=False)
            if state['contract']['data_hash']!=provenance['data_hash']:raise ValueError('Data identity')
            model=restore(state).cuda();detail,metrics=evaluate(model,audit)
            write_json(a.output/(name+'-audit-detail.json'),detail);write_json(a.output/(name+'-audit-metrics.json'),metrics)
            record=dict(checkpoint_sha=result['best_checkpoint_sha'],step=result['best_step'],metrics=metrics,summary=summary(metrics))
            if name==a.winner:
                _,training=evaluate(model,tuple(t[:2048] for t in train[:4]));_,development=evaluate(model,dev[:4])
                write_json(a.output/'retained-train-metrics.json',training);write_json(a.output/'retained-development-metrics.json',development)
                curve=json.loads((arm/'curve.json').read_text());previous=next(v['metrics'] for v in curve if v['step']==result['best_step'])
                differences={k:abs(v-previous[k]) for k,v in development.items() if k in previous and not k.startswith('evaluation_')}
                record['development_readback_max_error']=max(differences.values());record['train_summary']=summary(training);record['development_summary']=summary(development)
                record['audit_retention']=check(metrics,results[selection['rows'][0]['name']]['metrics'])
            results[name]=record;print(name,record['summary'],flush=True);del model,state;gc.collect();torch.cuda.empty_cache()
        write_json(a.output/'result.json',dict(winner=a.winner,audit_windows=len(audit[0]),data_hash=manifest['hash'],results=results,seconds=time.time()-start))


if __name__=='__main__':main()
