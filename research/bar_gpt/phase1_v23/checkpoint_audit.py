"""Read-only CPU audit of completed capacity runs; writes runtime evidence only."""
import argparse
import json
from pathlib import Path
import torch
from ..phase1_v22.compare import summarize


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    root=p.parse_args().output;torch.set_num_threads(2);reports=[]
    for result_path in sorted(root.glob('e*/result.json')):
        result=json.loads(result_path.read_text());arm=result_path.parent
        last=torch.load(arm/'checkpoint_last.pt',map_location='cpu',weights_only=False)
        best=torch.load(arm/'checkpoint_best.pt',map_location='cpu',weights_only=False)
        learned=[]
        for block in range(2,result['architecture']['depth']):
            for suffix in ('projection.weight','ff.2.weight'):
                key=f'temporal.blocks.{block}.{suffix}';v=last['model'][key]
                learned.append(dict(parameter=key,initial_norm=0.,final_norm=float(v.norm()),nonzero=int(v.count_nonzero()),elements=v.numel()))
        curves=[]
        for point in last['curve']:
            m=point['metrics'];curves.append(dict(step=point['step'],seen=point['seen'],ranking=point['ranking'],summary=summarize('development',m),sub_010_median_pct=m['all_visible/audit/price_band_0_0.1/median_pct'],evaluation_seconds=m['evaluation_seconds']))
        reports.append(dict(name=result['name'],best_step=best['step'],last_step=last['step'],best_score=best['best'],added_block_updates=learned,curve=curves))
        del last,best
    (root/'checkpoint-audit.json').write_text(json.dumps(reports,indent=2))
    print(json.dumps(reports,indent=2))


if __name__=='__main__':main()
