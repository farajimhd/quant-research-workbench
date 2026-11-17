"""One read-only Rich campaign panel; exits when the campaign is terminal."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,time
from pathlib import Path
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table


def render(root):
    ledger_path=root/'campaign.json'
    ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    current=ledger.get('current_experiment')
    progress={}
    if current and (root/current/'progress.json').exists():
        try:progress=json.loads((root/current/'progress.json').read_text())
        except (OSError,ValueError):pass
    table=Table.grid(padding=(0,2));table.add_column();table.add_column()
    table.add_row('Campaign',ledger.get('state','preparing'))
    table.add_row('Experiment',current or 'Data preparation')
    stale=time.time()-progress.get('updated',time.time())
    table.add_row('Stage',progress.get('stage','Waiting for measured progress')+(' - STALE' if stale>30 else ''))
    table.add_row('Deadline',f"{progress.get('elapsed',0)/60:.1f} / {progress.get('limit_seconds',3600)/60:.0f} min")
    table.add_row('Updates',f"{progress.get('step',0):,} / {progress.get('planned_steps',0):,}")
    table.add_row('Throughput',f"{progress.get('windows_per_second',0):,.0f} windows/s")
    table.add_row('Loss',str(progress.get('loss','n/a')))
    table.add_row('Latest relative error',str(progress.get('latest_evaluation',ledger.get('latest_evaluation','n/a'))))
    table.add_row('Completed / limit',f"{len(ledger.get('experiments',[]))} / 8")
    return Panel(table,title='BarGPT encoder research',border_style='yellow' if stale>30 else 'cyan'),ledger.get('state')


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args()
    console=Console()
    if a.once or not console.is_terminal:console.print(render(a.root)[0]);return
    with Live(console=console,refresh_per_second=1) as live:
        while True:
            view,state=render(a.root);live.update(view)
            if state in ('complete','stopped','failed'):break
            time.sleep(1)


if __name__=='__main__':main()
