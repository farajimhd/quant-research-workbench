"""Operator dashboard for a bounded, restart-safe multi-session search.

Durable status is authoritative; rendering runs at one refresh/second. Full
generation receipts retain every session/candidate. The panel shows completed
metrics separately from the active replay cursor, never provisional P&L.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import monotonic, sleep

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .progress import duration, safe_diagnostic
from .runtime import write_json


def render_search(s, elapsed, *, width=110, height=28):
    status = s.get("status", "Starting")
    failed = status in ("failed", "interrupted")
    color = "red" if failed else "green" if status == "completed" else "cyan"
    config = s.get("config", {})
    progress = s.get("progress", {})
    best = s.get("best_metrics") or {}
    age = max(
        0,
        datetime.now(timezone.utc).timestamp()
        - s.get("updated_epoch", datetime.now(timezone.utc).timestamp()),
    )
    rows = [
        Text(
            f"{status.upper()}  ·  {s.get('stage', 'Starting')}  ·  elapsed {duration(elapsed)}",
            style="bold " + color,
        ),
        Text(s.get("focus", "Discovering certified sessions")),
        Text(
            f"Generation {s.get('completed_generations', 0)}/{config.get('generations', '?')} complete  |  "
            f"population {config.get('population', '?')}  |  training {config.get('training_sessions', '?')}  ·  validation {config.get('validation_sessions', '?')}"
        ),
        Text(
            f"Candidate-session replays {s.get('candidate_session_replays', 0):,}  |  objective evaluations {s.get('objective_evaluations', 0):,}  |  unique tensors {s.get('unique_candidates', 0):,}"
        ),
    ]
    gpu_line = Text(
        f"GPU {s.get('gpu_gib', 0):.1f} GiB | resident {s.get('resident_sessions', 0)} | "
        f"prefetched {s.get('prefetched_sessions', 0)} | copy wait {s.get('transfer_wait_seconds', 0):.2f}s"
    )
    if width >= 95:
        gpu_line.append(f" | updated {age:.0f}s ago")
    pipeline = s.get("pipeline")
    if pipeline:
        rows.append(Text(
            f"PREP ready {pipeline['ready']}/{pipeline['total']} | active {pipeline['active']} "
            f"queued {pipeline['queued']} failed {pipeline['failed']} | data wait {pipeline['data_wait_seconds']:.1f}s"
        ))
        rows.append(gpu_line)
        rows.append(Text(
            f"Preparing: {s.get('preparation_focus', 'waiting for producer')}  |  "
            f"{s.get('preparation_progress', {}).get('stage', 'queued')}  |  "
            f"host {pipeline['host_gib']:.1f} + reserved {pipeline['reserved_gib']:.1f} GiB"
        ))
        if pipeline.get("waiting_session") is not None:
            rows.append(Text(f"GPU WAITING for certified session {pipeline['waiting_session'] + 1}", style="yellow"))
    if progress.get("total_seconds"):
        rows.append(
            Text(
                f"Active session clock: {progress.get('completed_seconds', 0):,}/{progress['total_seconds']:,} one-second slots"
            )
        )
    elif progress.get("total"):
        rows.append(
            Text(
                f"Source preparation: {progress.get('completed', 0):,}/{progress['total']:,} {progress.get('stage', 'units')}"
            )
        )
    else:
        rows.append(
            Text(
                s.get("message")
                or (
                    "Last completed training metrics below; active work in progress"
                    if best
                    else "No completed candidate metrics yet"
                )
            )
        )
    if best:
        rows.extend(
            [
                Text(
                    f"BEST FEASIBLE  objective {s['best_score']:.6f}  |  P&L ${best['total_pnl']:,.2f}  |  worst session ${best['worst_pnl']:,.2f}",
                    style="bold green",
                ),
                Text(
                    f"Open {best['open']:,}  |  worst drawdown ${best['worst_drawdown']:,.2f}  |  batches {best['batches']:,}  ·  positions {best['positions']:,}  ·  fills {best['fills']:,}"
                ),
                Text(
                    f"Sold-share weighted hold {best['mean_hold_seconds']:.1f}s  |  stop-risk hours {best.get('stop_risk_hours', 0):.3f}  |  capital hours {best.get('capital_hours', 0):.3f}"
                ),
            ]
        )
        costs = best.get('objective_components')
        if costs:
            rows.append(Text(f"Costs: DD {costs['drawdown_penalty']:.4f} · downside {costs['downside_penalty']:.4f} · stop risk {costs['stop_risk_penalty']:.4f} · holding {costs['capital_time_penalty']:.4f}"))
    else:
        rows.extend(
            [
                Text(
                    "BEST FEASIBLE  awaiting a candidate satisfying EVERY training session",
                    style="bold yellow",
                ),
                Text(
                    f"Closest constraint violation: {s.get('closest_violation', 'unknown')}  |  feasible {s.get('feasible_candidates', 0)}/{config.get('population', '?')}"
                ),
                Text(
                    "Constraint scores guide search; infeasible P&L is never called an optimum."
                ),
            ]
        )
        sample = s.get("last_session_metrics")
        if sample:
            rows.append(
                Text(
                    f"Last completed session, lane 0: P&L ${sample['total_pnl']:,.2f}  ·  batches {sample['batches']}  ·  fills {sample['fills']}  ·  open {sample['open']}"
                )
            )
    last = s.get("last_completed") or {}
    mutation = config.get('mutation') or {}
    if mutation:
        percentages = lambda key: '/'.join(f'{100*v:g}' for v in mutation[key])
        mutation_line = (f"Mutation: mix {percentages('probabilities')}% | "
                         f"gene rates {percentages('coordinate_rates')}% | "
                         f"steps {percentages('scales')}%")
    else:
        mutation_line = 'Mutation: legacy settings' if config else 'Mutation: not used during qualification'
    rows.extend(
        [
            Text(
                f"Last generation: wall {last.get('end_to_end_seconds', 0):.1f}s  ·  replay {last.get('replay_seconds', 0):.1f}s  ·  data wait {last.get('data_wait_seconds', 0):.1f}s  ·  compile {last.get('compile_seconds', 0):.1f}s"
            ),
            Text(
                f"Rejected candidates {s.get('invalid_candidates', 0)}  |  stagnant generations {s.get('stagnant_generations', 0)}  |  ETA {s.get('eta', 'measuring')}"
            ),
            Text(
                f"GA: seed {config.get('seed', '?')} · elites 2 · tournament 3 · immigrants {s.get('immigrant_percent', 20)}%"
            ),
            Text(mutation_line),
            Text(
                f"Constraints: hold >=3s (risk exits exempt)  ·  batches/session >= {config.get('minimum_training_entries', 1)}  ·  soft maximum {config.get('maximum_training_batches', 20)}"
            ),
            Text(
                f"Holding 3s–{config.get('maximum_position_hold_seconds', 3600)}s; stop-risk admission {100*config.get('maximum_stop_risk_fraction', .02):g}%; daily cash reset"
            ),
            *([] if pipeline else [gpu_line]),
            Text("Checkpoint: " + s.get("checkpoint", "not applicable during qualification" if s.get('status') == 'profiling' else "pending optimizer initialization")),
            Text("Run: " + s.get("output", "pending")),
        ]
    )
    # Fit complete important rows first; truncate only secondary detail. A short
    # terminal always retains status, progress, outcome and actionable failure.
    capacity = max(1, height - 2)
    if failed:
        rows = rows[: min(5, capacity - 2)] + [
            Text(safe_diagnostic(s.get("message", "Failed")), style="bold red"),
            Text("Details: " + s.get("output", "") + "/error.json"),
        ]
    else:
        rows = rows[:capacity]
    for row in rows:
        row.no_wrap, row.overflow = True, "ellipsis"
    return Panel(
        Group(*rows),
        title="GPU STRATEGY SEARCH · v3",
        border_style=color,
        width=width,
        padding=(0, 1),
    )


class SearchPanel:
    def __init__(self, output, *, plain=False, console=None, read_only=False):
        self.output = Path(output)
        self.console = console or Console(no_color=bool(os.environ.get("NO_COLOR")))
        self.state = dict(output=str(output), status="starting")
        previous = self.output / "status.json"
        if previous.exists():
            self.state.update(json.loads(previous.read_text(encoding="utf-8")))
        epoch = datetime.now(timezone.utc).timestamp()
        self.state.setdefault("started_epoch", epoch)
        self.started, self.last_plain = (
            monotonic() - max(0, epoch - self.state["started_epoch"]),
            0,
        )
        self.interactive = self.console.is_interactive and not plain
        self.live = None
        self.read_only = read_only
        self.lock = RLock()

    def view(self):
        return render_search(
            self.state,
            monotonic() - self.started,
            width=self.console.size.width,
            height=self.console.size.height,
        )

    def __enter__(self):
        if self.interactive:
            self.live = Live(
                get_renderable=self.view,
                console=self.console,
                refresh_per_second=1,
                transient=False,
                vertical_overflow="crop",
            )
            self.live.start()
        return self

    def emit(self, event):
        # Source-fetch progress can arrive from bounded background workers.
        # One owner serializes state, snapshot replacement and event append.
        with self.lock:
            self._emit(event)

    def _emit(self, event):
        if self.read_only:
            raise RuntimeError("Monitor cannot write worker state")
        self.state.update(event, updated_epoch=datetime.now(timezone.utc).timestamp())
        import torch

        if torch.cuda.is_initialized():
            self.state["gpu_gib"] = torch.cuda.memory_allocated() / 1024**3
        write_json(self.output / "status.json", self.state)
        with (self.output / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    dict(event, updated_epoch=self.state["updated_epoch"]),
                    allow_nan=False,
                )
                + "\n"
            )
        if self.live:
            self.live.update(self.view())
        elif (
            monotonic() - self.last_plain >= 15
            or "stage" in event
            or "completed_generations" in event
        ):
            self.console.print(
                f"{self.state['status']} | {self.state.get('stage', '')} | {self.state.get('focus', '')} | "
                f"generation {self.state.get('completed_generations', 0)} | replays {self.state.get('candidate_session_replays', 0)} | "
                f"best {self.state.get('best_score')} | {self.state.get('message', '')}",
                markup=False,
            )
            self.last_plain = monotonic()

    def __exit__(self, kind, value, tb):
        if kind and not self.read_only:
            import traceback

            state = "interrupted" if issubclass(kind, KeyboardInterrupt) else "failed"
            write_json(
                self.output / "error.json",
                dict(
                    type=kind.__name__,
                    reason=safe_diagnostic(value),
                    traceback=safe_diagnostic(
                        "".join(traceback.format_exception(kind, value, tb))
                    ),
                ),
            )
            self.emit(
                dict(
                    status=state,
                    stage="Checkpoint retained",
                    message=safe_diagnostic(value),
                )
            )
        self.close_live()

    def close_live(self):
        """Release console ownership before handing off to another live panel."""
        if self.live:
            self.live.update(self.view(), refresh=True)
            self.live.stop()
            self.live = None


def monitor(output):
    """Reopen the same panel without owning or restarting the worker."""
    output = Path(output)
    with SearchPanel(output, read_only=True) as panel:
        while True:
            try:
                state = json.loads((output / "status.json").read_text(encoding="utf-8"))
                panel.state = state
                if panel.live:
                    panel.live.update(panel.view())
                elif state != getattr(panel, "previous", None):
                    panel.console.print(
                        render_search(
                            state,
                            0,
                            width=panel.console.size.width,
                            height=panel.console.size.height,
                        )
                    )
                panel.previous = state
                if state.get("status") in ("completed", "failed", "interrupted"):
                    break
            except FileNotFoundError:
                pass
            sleep(1)
