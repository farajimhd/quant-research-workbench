"""Repair closed-row logging with preserved optimizer steps and complete artifacts."""
import argparse,json
from pathlib import Path
from ..v5.shards.phase1_contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--runs',type=Path,nargs='+',required=True);p.add_argument('--source-revision',required=True);a=p.parse_args()
    import wandb
    for root in a.runs:
        result=json.loads((root/'result.json').read_text())
        if result['state']!='complete':raise ValueError('Only completed research runs can be backfilled')
        identity=json.loads((root/'wandb.json').read_text());step=result['step'];metrics={}
        for mode,m in json.loads((root/'development.json').read_text()).items():
            for key in ('balanced_price_relative_mean','balanced_activity_log_mae','worst_price_band_relative_mean'):metrics[f'final/{mode}/{key}']=m[key]
            for frame,record in m['frames'].items():
                for channel,values in record['channels'].items():
                    if values['mae'] is not None:metrics[f'raw_mae/{mode}/{frame}/{channel}']=values['mae']
        if (root/'probes.json').exists():
            probes=json.loads((root/'probes.json').read_text())
            for variant in ('trained','untrained_same_architecture','raw_summary_control'):
                for label,value in probes[variant].items():
                    for metric in ('mae','r2'):metrics[f'probes/{variant}/{label}/{metric}']=value[metric]
        run=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v11-median',id=identity['id'],resume='must',dir=str(root))
        try:
            run.log(dict(metrics,optimizer_step=step,postrun_evaluation_backfill=True),step=max(run.step,step+1));run.summary.update(metrics)
            run.summary.update(dict(log_repair_source=a.source_revision,log_repair='Complete evaluation/probe metrics and artifacts; no model/optimizer updates or scientific change.',failure=None))
            artifact=wandb.Artifact(root.name+'-complete-evaluation',type='evaluation')
            for name in ('contract.json','result.json','development.json','training-evaluation.json','probes.json','profile.json','tiny.json','learning-curves.json'):
                if (root/name).exists():artifact.add_file(str(root/name))
            run.log_artifact(artifact)
            write_json(root/'log-repair.json',dict(source_revision=a.source_revision,optimizer_step=step,metric_count=len(metrics),artifact=artifact.name,reason='Preserve metrics that shared an already committed W&B SDK step; scientific run unchanged.'))
        finally:run.finish()


if __name__=='__main__':main()
