"""One compact finite-campaign Rich panel; exits when the experiment ends."""
import argparse,json,time
from pathlib import Path
from rich.live import Live
from rich.table import Table
from rich.panel import Panel


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with Live(refresh_per_second=1) as live:
        while True:
            try:value=json.loads((a.output/'progress.json').read_text())
            except (OSError,ValueError):value=dict(stage='waiting for owned trainer')
            table=Table.grid(padding=(0,2));table.add_column();table.add_column()
            table.add_row('Experiment',a.output.name);table.add_row('Stage',value['stage'])
            elapsed=value.get('elapsed_seconds',0);budget=value.get('deadline_seconds',0)
            table.add_row('Elapsed / deadline',f'{elapsed/60:.1f} / {budget/60:.1f} min')
            if 'step' in value:table.add_row('Optimizer step',str(value['step']))
            if 'loss' in value:table.add_row('Loss',f"{value['loss']:.5f}")
            if 'done' in value:table.add_row('Evaluated',f"{value['done']:,} / {value['total']:,}")
            if 'windows_per_second' in value:
                speed=value['windows_per_second'];table.add_row('Speed',f'{speed:,.0f} windows/s')
                if 'done' in value:table.add_row('Evaluation ETA',f"{(value['total']-value['done'])/max(speed,1):.0f} s")
            try:
                curve=json.loads((a.output/'learning-curves.json').read_text())[-1]
                table.add_row('Latest dev · masked / full',f"{100*curve['masked']['balanced_price_relative_mean']:.2f}% / {100*curve['all_visible']['balanced_price_relative_mean']:.2f}% price error")
                table.add_row('Reference · masked / full',f"{100*curve['masked']['reference_price_relative']['mean']:.2f}% / {100*curve['all_visible']['reference_price_relative']['mean']:.2f}% error")
            except (OSError,ValueError,IndexError,KeyError):pass
            live.update(Panel(table,title='BarGPT V11 · median-centered MAE',border_style='cyan'))
            if value['stage']=='complete' or (a.output/'failure.json').exists():break
            time.sleep(1)


if __name__=='__main__':main()
