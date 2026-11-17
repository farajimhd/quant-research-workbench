"""One read-only V5-style panel for the finite learned-only MAE campaign."""
import argparse,json,time,sys
from pathlib import Path
from rich.console import Console,Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
from ..panels.phase2_training_v1 import BlockBar,number
from ..phase1_v5_raw.panel import duration


def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def render(root,width=120,height=30):
    ledger=read(root/'campaign.json');name=ledger.get('current_experiment')
    p=read(root/name/'progress.json') if name else read(root/'dataset/progress.json')
    stage=p.get('stage','preparing frozen data');age=time.time()-p.get('updated',time.time())
    experiments=ledger.get('experiments',[]);complete=sum(x.get('state')=='complete' for x in experiments);failed=sum(x.get('state')=='failed' for x in experiments)
    rows=[Text('BarGPT V8 · FULLY LEARNED MAE · no copied input slots',style='bold cyan'),
          Text(f'{ledger.get("state","preparing").upper()} · {name or "Dataset preparation"} · '+('STALE · ' if age>30 else '')+stage,style='yellow' if age>30 else 'bold')]
    if p.get('error'):rows.append(Text(p['error'],style='red'))
    rows.append(Text(f'Experiments: {complete} complete · {failed} failed · {len(experiments)}/8 attempted · campaign time left {duration(ledger.get("deadline",time.time())-time.time())}',style='dim'))
    bars=Table.grid(expand=True,padding=(0,1));bars.add_column(width=16);bars.add_column(ratio=1);bars.add_column(width=22,justify='right')
    done=p.get('step',0);total=p.get('planned_steps')
    bars.add_row('Training updates',BlockBar(done,total),f'{number(done)}/{number(total)}')
    elapsed=p.get('elapsed');limit=p.get('limit_seconds')
    bars.add_row('Experiment time',BlockBar(elapsed or 0,limit),f'{duration(elapsed)}/{duration(limit)}')
    evaluating=any(s in stage for s in ('validation','all-visible','hidden-only'))
    if evaluating:bars.add_row('Evaluation',BlockBar(p.get('evaluation_done',0),p.get('evaluation_total')),f'{number(p.get("evaluation_done"))}/{number(p.get("evaluation_total"))}')
    elif stage=='tiny overfit':bars.add_row('Tiny fit',BlockBar(p.get('tiny_step',0),800),f'{p.get("tiny_step",0)}/800')
    rows.append(bars)
    rate=p.get('evaluation_windows_per_second') if evaluating else p.get('windows_per_second') if stage=='training' else None
    if age>30:rate=None
    rows.append(Text(f'{"Evaluation inference" if evaluating else "Training incl. checks"} {number(rate)} windows/s'+(f' · inference ETA {duration(p.get("evaluation_eta_seconds"))}' if evaluating and age<=30 else '')))
    memory=p.get('gpu_memory_bytes')
    rows.append(Text(f'Loss {number(p.get("loss"),5)} · GPU allocated peak {number(memory/2**30 if memory is not None else None,1)} GiB · cache mode: no per-step loader waits',style='dim'))
    latest=read(root/name/'development-latest.json') if name else {}
    rows.append(Text('Latest development · all-visible reconstruction',style='bold'))
    rows.append(Text(f'Price error {number(100*latest["balanced_price_relative_mean"] if latest else None,3)}% · worst price band {number(100*latest["worst_price_band_relative_mean"] if latest else None,2)}% · activity log MAE {number(latest.get("balanced_activity_log_mae"),4)}'))
    rows.append(Text('Hidden-only, native units, metadata and frozen probes are retained in each experiment report.',style='dim',no_wrap=True,overflow='ellipsis'))
    return Group(*rows)


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--once',action='store_true');p.add_argument('--width',type=int);p.add_argument('--height',type=int);a=p.parse_args()
    console=Console(width=a.width,height=a.height)
    if a.once or not console.is_terminal:console.print(render(a.root,console.width,console.height));return
    try:
        with Live(console=console,auto_refresh=False,vertical_overflow='crop') as live:
            while True:live.update(render(a.root,console.width,console.height),refresh=True);time.sleep(1)
    except KeyboardInterrupt:console.print('Viewer closed; experiment unchanged.')


if __name__=='__main__':main()
