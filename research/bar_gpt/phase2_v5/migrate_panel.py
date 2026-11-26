"""Fixed-height progress view for the offline Phase 2 migration."""
import argparse
import json
from pathlib import Path
import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.text import Text

from ..v5.shards.contract import write_json


def status(root, values):
    write_json(root/"progress.json",dict(values,updated=time.time()))


def view(values):
    done, total = values.get("completed",0), values.get("total",0)
    state = values.get("state","waiting")
    error = " ".join(values.get("error","").splitlines())
    elapsed = max(1,values.get("updated",0)-values.get("started",0))
    remaining = f"{(total-done)*elapsed/done/3600:.1f} h" if done and total and state=="running" else "—"
    lines = [
        f"{state.upper()}  |  {values.get('phase','Waiting for migration')}",
        f"Sessions  {done:,} / {total:,}" if total else "Sessions  Waiting for source inventory",
        f"Verified storage  {values.get('bytes',0)/1e9:,.2f} GB  |  Session rate  {done/elapsed:,.1f}/s  |  ETA  {remaining}",
        f"Source occurrences  {values.get('occurrences',0):,}  |  Unique origins  {values.get('unique',0):,}",
        error or "Source shards retained during migration.",
    ]
    return Panel(Group(Text(lines[0], no_wrap=True, overflow="ellipsis"),
                       ProgressBar(total=total or None,completed=done,pulse=not total),
                       *(Text(line,no_wrap=True,overflow="ellipsis") for line in lines[1:])),
                 title="Phase 2 unique-origin migration",border_style="red" if error else "cyan")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run",type=Path,required=True)
    p.add_argument("--once",action="store_true")
    args=p.parse_args()
    from ..phase1_v29.panel import enable_virtual_terminal, maximize_console
    import sys
    console=Console()
    interactive=console.is_terminal and not args.once
    if interactive:
        maximize_console()
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    vt=interactive and enable_virtual_terminal()
    if vt:
        console=Console(legacy_windows=False)
    def read(previous):
        try:
            return json.loads((args.run/"progress.json").read_text())
        except (ValueError,OSError):
            return previous
    current=read({})
    if not interactive:
        console.print(view(current))
        return
    with Live(view(current),console=console,screen=vt,auto_refresh=False) as live:
        while True:
            current=read(current)
            live.update(view(current),refresh=True)
            time.sleep(1)


if __name__=="__main__":
    main()
