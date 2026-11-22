"""Reconcile final versus retained checkpoints without additional GPU inference."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from .gates import check
from ..phase1_v25.metrics import summary
from ..v5.shards.phase1_contract import file_hash,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=a.output
    results=json.loads((root/'comparison.json').read_text());reference=results[0]['metrics'];rows=[]
    for r in results:
        arm=root/r['name'];curve=json.loads((arm/'curve.json').read_text())
        retained=next(v for v in curve if v['step']==r['best_step']);gate=check(retained['metrics'],reference)
        if not gate['accepted']:raise ValueError('Retained checkpoint violates frozen gates')
        cp=arm/'checkpoint_best.pt'
        if file_hash(cp)!=r['best_checkpoint_sha']:raise ValueError('Changed retained checkpoint')
        metrics=retained['metrics'];final_gate=check(r['metrics'],reference)
        def mean(prefix,suffix):
            values=[v for k,v in metrics.items() if k.startswith(prefix) and k.endswith(suffix)]
            return sum(values)/len(values) if values else None
        rows.append(dict(name=r['name'],mode=r['mode'],seed=r['seed'],status=r['status'],updates=r['steps'],best_step=r['best_step'],seen=r['seen'],
            seconds=r['seconds'],speed=r['speed'],parameters=r['parameters'],decoder_parameters=r['decoder_parameters'],peaks=r['peaks'],
            summary=summary(metrics),hidden8=summary(metrics,'hidden8'),hidden48=summary(metrics,'hidden48'),gate=gate,last_gate=final_gate,
            activity_moving=mean('activity/all_visible/','/moving_direction_pct'),activity_flat=mean('activity/all_visible/','/flat_recall_pct'),
            activity_log_mae=mean('activity/all_visible/','/log_mae'),checkpoint=str(cp),checkpoint_sha=r['best_checkpoint_sha'],wandb=r['wandb_url'],
            structural={k:v for k,v in metrics.items() if '/audit/' in k and k.endswith(('/nonfinite','/negative','/ohlc_violations','/quote_crossings','/constant_embedding_coordinates'))}))
    eligible=[r for r in rows if r['best_step']>0]
    winner=min(eligible or rows,key=lambda r:r['gate']['score'])
    write_json(root/'retained-comparison.json',dict(rows=rows,winner=winner['name'],selection='Lowest balanced family raw-MAE ratio among checkpoints satisfying all frozen retention gates',
        limits='Development-selected reconstruction evidence; no future-label probe or sealed audit; do not claim forecasting improvement.'))
    text=['# V26 preservation campaign','',
        'Retained checkpoints are shown below. Rejected arms retain their earlier passing checkpoint; their rejected final state is never promoted.','',
        '| Experiment | Updates / retained | Status | Close MAE $ | Candle % | Range % | Activity balanced % | Activity MAE ratio | Windows/s |',
        '|---|---:|---|---:|---:|---:|---:|---:|---:|']
    for r in rows:
        s=r['summary'];text.append(f"| {r['name']} | {r['updates']} / {r['best_step']} | {r['status']} | {s['close_mae']:.5f} | {s['candle']:.3f} | {s['range']:.3f} | {s['activity']:.3f} | {r['gate']['ratios']['activity']['geomean']:.4f} | {r['speed']:,.0f} |")
    text+=['',f"Provisional retained winner: **{winner['name']}**, step{winner['best_step']}.",f"Checkpoint: `{winner['checkpoint']}`",f"SHA256: `{winner['checkpoint_sha']}`",'',
        'All models export64x64 learned slots; the price path receives its original all-feature inputs. Decoder has792 stored parameters, with six unused original price-head activity rows retained for exact numerical parity.',
        'Activity balanced direction averages down/flat/up recalls per group; moving direction excludes target-flat pairs. Candle direction compares reconstructed open and close, not future returns.',
        'No forecasting-generalization conclusion follows from these reconstruction metrics. Hidden8 and hidden48 are separately retained in JSON, with raw errors, directional metrics, variance and structure diagnostics.']
    for r in rows:
        if not r['last_gate']['accepted']:text+=['',r['name']+' rejected final state: '+ '; '.join(r['last_gate']['reasons'])]
    (root/'comparison-report.md').write_text('\n'.join(text)+'\n',encoding='utf-8');print(json.dumps(dict(winner=winner['name'],rows=len(rows))))


if __name__=='__main__':main()
