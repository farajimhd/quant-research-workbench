"""Read-only temporal forecasting campaign monitor."""
import argparse,json,time,sys
from pathlib import Path
from rich.console import Console,Group
from rich.live import Live
from rich.text import Text
from rich.table import Table
from ..panels.phase2_training_v1 import BlockBar,number
from ..phase1_v5_raw.panel import duration


def read(path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}


def render(root):
    ledger=read(root/'campaign.json');name=ledger.get('current_experiment','dataset');p=read(root/name/'progress.json')
    manifest=read(root/name/'manifest.json');config=manifest.get('config',{})
    stage=p.get('stage','preparing');stale=stage not in ('complete','failed','stopped') and time.time()-p.get('updated',time.time())>30
    rows=[Text('BarGPT V9 · CAUSAL MULTISCALE FORECASTING',style='bold cyan'),Text(f'{name} · {stage}'+(' · stale' if stale else ''),style='yellow' if stale else 'bold'),
        Text('16 past windows → next 64 observed 1s bars · future inputs forbidden',style='dim')]
    if manifest:
        dependency='embedding-only forecast' if config.get('anchor_mode','historical')=='learned' else 'historical-anchor CONTROL'
        rows.append(Text(f'{dependency} · {config.get("embedding")} dimensions/window · encoder {manifest.get("window_encoder_parameters",0)/1e6:.2f}M parameters',style='cyan'))
    bars=Table.grid(expand=True,padding=(0,1));bars.add_column(width=17);bars.add_column(ratio=1);bars.add_column(width=21,justify='right')
    bars.add_row('Optimizer updates',BlockBar(p.get('step',0),p.get('planned_steps')),f'{number(p.get("step"))}/{number(p.get("planned_steps"))}')
    bars.add_row('Elapsed / limit',BlockBar(p.get('elapsed',0),p.get('limit_seconds')),f'{duration(p.get("elapsed"))}/{duration(p.get("limit_seconds"))}')
    if 'validation' in stage or 'evaluation' in stage:bars.add_row('Evaluation',BlockBar(p.get('evaluation_done',0),p.get('evaluation_total')),f'{number(p.get("evaluation_done"))}/{number(p.get("evaluation_total"))}')
    rows.append(bars)
    if 'validation' in stage or 'evaluation' in stage:
        rows.append(Text(f'Evaluation: {number(p.get("evaluation_origins_per_second"))} origins/s · ETA {duration(p.get("evaluation_eta_seconds"))}',style='cyan'))
    rows.append(Text(f'Training incl. checks: {number(p.get("origins_per_second") if stage=="training" and not stale else None)} origins/s · GPU peak {number(p.get("gpu_memory_bytes",0)/2**30,1)} GiB · cached inputs',style='dim'))
    rows.append(Text(f'Loss: forecast {number(p.get("forecast_loss"),4)} · reconstruction {number(p.get("reconstruction_loss"),4)}'))
    latest=read(root/name/'development-latest.json')
    for partition,metrics in latest.items():
        price=100*metrics['price_mae_skill'];activity=100*metrics['activity_mae_skill'];recon=100*metrics['reconstruction']['balanced_price_relative_mean']
        rows.append(Text(f'{partition}: forecast skill vs persistence · price {price:+.2f}% · activity {activity:+.2f}% · reconstruction price {recon:.3f}%',style='green' if price>0 and activity>0 else 'yellow'))
    rows.append(Text(f'{sum(r.get("state")=="complete" for r in ledger.get("experiments",[]))} complete · {sum(r.get("state")=="failed" for r in ledger.get("experiments",[]))} failed · {ledger.get("state","")}',style='dim'))
    if p.get('error'):rows.append(Text(p['error'],style='red'))
    return Group(*rows)


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();console=Console()
    if a.once or not console.is_terminal:console.print(render(a.root));return
    try:
        with Live(console=console,auto_refresh=False,vertical_overflow='crop') as live:
            while True:live.update(render(a.root),refresh=True);time.sleep(1)
    except KeyboardInterrupt:console.print('Viewer closed; training unchanged.')


if __name__=='__main__':main()
