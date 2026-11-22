"""Read-only CPU audit of completed capacity runs; writes runtime evidence only."""
import argparse
import json
from pathlib import Path
import torch
from ..phase1_v22.compare import summarize


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--additional-root',type=Path,action='append',default=[]);a=p.parse_args()
    root=a.output;torch.set_num_threads(2);reports=[]
    for result_path in sorted(f for folder in [root]+a.additional_root for f in folder.glob('e*/result.json')):
        result=json.loads(result_path.read_text());arm=result_path.parent
        last=torch.load(arm/'checkpoint_last.pt',map_location='cpu',weights_only=False)
        best=torch.load(arm/'checkpoint_best.pt',map_location='cpu',weights_only=False)
        learned=[]
        for block in range(6,result['architecture']['depth']):
            for suffix in ('projection.weight','ff.2.weight'):
                key=f'temporal.blocks.{block}.{suffix}';v=last['model'][key]
                learned.append(dict(parameter=key,initial_norm=0.,final_norm=float(v.norm()),nonzero=int(v.count_nonzero()),elements=v.numel()))
        curves=[]
        for point in last['curve']:
            m=point['metrics'];curves.append(dict(step=point['step'],seen=point['seen'],ranking=point['ranking'],summary=summarize('development',m),sub_010_median_pct=m['all_visible/audit/price_band_0_0.1/median_pct'],evaluation_seconds=m['evaluation_seconds']))
        width=result['architecture']['width'];factor=width//384;replicas=[]
        if factor>1:
            for block in range(6):
                # Fixed corresponding Q-projection slice, identical at expansion.
                q=last['model'][f'temporal.blocks.{block}.qkv.weight'][:width,:384].reshape(factor,384,384)
                centered=q-q.mean(0,keepdim=True)
                replicas.append(dict(block=block,relative_rms_divergence=float(centered.square().mean().sqrt()/q.square().mean().sqrt().clamp_min(1e-12))))
        reports.append(dict(name=result['name'],best_step=best['step'],last_step=last['step'],best_score=best['best'],checkpoint_accounted_seconds=last['elapsed_seconds'],timing_includes_engineering_retry=bool(last['contract'].get('qualification_elapsed_seconds',0)),added_block_updates=learned,replicated_q_weight_divergence=replicas,curve=curves))
        del last,best
    (root/'checkpoint-audit.json').write_text(json.dumps(reports,indent=2))
    print(json.dumps(reports,indent=2))


if __name__=='__main__':main()
