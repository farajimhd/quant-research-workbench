"""Read-only repair campaign panel; no dataset scans or mutation on refresh."""

import argparse
import json
from pathlib import Path
import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def render(state, width=100):
    """Render bounded campaign/worker evidence at normal and compact widths."""
    table = Table(expand=True, box=None, padding=(0, 1))
    for name in ("Dataset", "State", "Receipts", "Changed bars", "Quarantined"):
        table.add_column(name)
    for item in state.get("datasets", []):
        counts = item.get("counts", {})
        table.add_row(
            item["name"],
            item.get("state", "queued"),
            f"{item.get('completed',0):,}/{item.get('total',0):,}",
            f"{counts.get('changed_bars',0):,}",
            f"{counts.get('quarantined',0):,}",
        )
    freshness = max(0, time.time() - state.get("updated", 0))
    summary = Text(
        f"{state.get('state','waiting')} | updated {freshness:.0f}s ago | training continues",
        style="bold",
    )
    if freshness > 30:
        summary.append(" | STALE", style="bold red")
    details = Text(
        state.get("failure")
        or "Original shards retained. Source rejects and bar occurrences are counted separately."
    )
    return Panel(
        Group(summary, table, details), title="BarGPT quote repair", width=width
    )


def main():
    """Refresh only on changed evidence; redirected output is a bounded snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/quote-repair-setup"),
    )
    args = parser.parse_args()
    console = Console()
    previous = None
    with Live(
        console=console, auto_refresh=False, screen=False, vertical_overflow="crop"
    ) as live:
        while True:
            path = args.root / "campaign.json"
            try:
                state = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                state = dict(state="waiting for campaign evidence", updated=0)
            signature = json.dumps(state, sort_keys=True)
            if signature != previous:
                live.update(render(state, min(console.width, 120)), refresh=True)
                previous = signature
            if not console.is_terminal or state.get("state") in (
                "complete",
                "failed",
                "stopped",
            ):
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
