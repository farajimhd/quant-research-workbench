from __future__ import annotations

import datetime as dt
import shutil
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(slots=True)
class StageProgress:
    name: str
    total: int
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    rows: int = 0
    status: str = "queued"
    started_at: float = 0.0

    @property
    def processed(self) -> int:
        return self.completed + self.skipped


@dataclass(frozen=True, slots=True)
class RecentResult:
    stage: str
    unit: str
    status: str
    rows: int
    elapsed_seconds: float


class NewsReactionProgress:
    """Operator-facing progress for the bounded news-reaction build.

    Rich mode uses a stable live display. Text mode emits durable, readable
    lifecycle lines for redirected output, CI, and terminals without Rich.
    Progress advances only after a unit and its completion checkpoint succeed.
    """

    def __init__(
        self,
        *,
        stage_totals: Sequence[tuple[str, int]],
        run_id: str,
        run_root: str,
        layout: str = "auto",
        refresh_per_second: float = 2.0,
        log_lines: int = 8,
    ) -> None:
        self.run_id = run_id
        self.run_root = run_root
        self.layout = layout
        self.refresh_per_second = max(0.5, float(refresh_per_second))
        self.log_lines = max(3, int(log_lines))
        self.started_at = time.perf_counter()
        self.stages = {name: StageProgress(name=name, total=total) for name, total in stage_totals}
        self.current_stage = "preflight" if "preflight" in self.stages else next(iter(self.stages), "-")
        self.current_unit = "-"
        self.current_query = "-"
        self.current_query_id = "-"
        self.current_query_started_at = 0.0
        self.last_error = ""
        self.outcome = "starting"
        self.recent_results: deque[RecentResult] = deque(maxlen=10)
        self.messages: deque[str] = deque(maxlen=self.log_lines)
        self._console: Any = None
        self._live: Any = None
        self._lock = threading.Lock()
        self._stop_refresh = threading.Event()
        self._refresh_thread: threading.Thread | None = None

    @property
    def rich_active(self) -> bool:
        return self._live is not None

    def __enter__(self) -> "NewsReactionProgress":
        use_rich = self.layout == "rich" or (self.layout == "auto" and sys.stdout.isatty())
        if use_rich:
            try:
                from rich.console import Console
                from rich.live import Live

                self._console = Console()
                self._live = Live(
                    self._render(),
                    console=self._console,
                    refresh_per_second=self.refresh_per_second,
                    transient=False,
                    auto_refresh=False,
                    vertical_overflow="crop",
                )
                self._live.start(refresh=True)
                self._start_refresh_thread()
            except Exception as exc:  # noqa: BLE001
                if self.layout == "rich":
                    raise RuntimeError(f"Rich progress requested but unavailable: {exc!r}") from exc
                self._live = None
                self._text(f"WARN rich progress unavailable; using text: {exc!r}")
        if self._refresh_thread is None:
            self._start_refresh_thread()
        self.message("news reaction build started")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc is not None and not self.last_error and self.outcome != "interrupted":
            self.fail(str(exc))
        self._stop_refresh.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=2.0)
        if self._live is not None:
            self._refresh()
            self._live.stop()

    def stage_start(self, stage: str) -> None:
        state = self.stages[stage]
        self.current_stage = stage
        self.current_unit = "-"
        state.status = "active"
        if state.started_at == 0.0:
            state.started_at = time.perf_counter()
        self.message(f"stage start {stage} units={state.total:,}")

    def chunk_start(self, stage: str, unit: str) -> None:
        if self.current_stage != stage or self.stages[stage].status != "active":
            self.stage_start(stage)
        self.current_unit = unit
        self.message(f"unit start stage={stage} unit={unit}")

    def unit_done(self, stage: str, unit: str, *, status: str, rows: int = 0, elapsed_seconds: float = 0.0) -> None:
        state = self.stages[stage]
        if status == "skipped":
            state.skipped += 1
        else:
            state.completed += 1
        state.rows += max(0, int(rows))
        state.status = "complete" if state.processed >= state.total else "active"
        self.recent_results.append(RecentResult(stage, unit, status, int(rows), float(elapsed_seconds)))
        self.current_unit = "-" if state.status == "complete" else unit
        if status != "skipped" or state.skipped in {1, state.total} or state.skipped % 100 == 0:
            self.message(
                f"unit {status} stage={stage} unit={unit} rows={int(rows):,} "
                f"elapsed={float(elapsed_seconds):.1f}s progress={state.processed:,}/{state.total:,}"
            )
        else:
            self._refresh()

    def unit_failed(self, stage: str, unit: str, exc: BaseException) -> None:
        state = self.stages[stage]
        state.failed += 1
        state.status = "failed"
        self.current_stage = stage
        self.current_unit = unit
        self.fail(f"{stage} {unit}: {exc}")

    def unit_interrupted(self, stage: str, unit: str) -> None:
        state = self.stages[stage]
        state.status = "interrupted"
        self.current_stage = stage
        self.current_unit = unit
        if self.outcome != "interrupted":
            self.interrupted()

    def query_start(self, label: str, query_id: str) -> None:
        self.current_query = label
        self.current_query_id = query_id
        self.current_query_started_at = time.perf_counter()
        self.message(f"query start {label} query_id={query_id}")

    def query_done(self, label: str) -> None:
        elapsed = self.query_elapsed_seconds
        self.current_query = "-"
        self.current_query_id = "-"
        self.current_query_started_at = 0.0
        self.message(f"query done {label} elapsed={elapsed:.1f}s")

    def query_failed(self, label: str, exc: BaseException) -> None:
        self.last_error = f"query {label}: {exc}"
        self.message(f"ERROR {self.last_error}")

    def interrupted(self) -> None:
        self.outcome = "interrupted"
        self.message(f"INTERRUPT cancelling query_id={self.current_query_id}")

    def fail(self, message: str) -> None:
        self.outcome = "failed"
        self.last_error = message
        self.message(f"FAILED {message}")

    def finish(self, outcome: str = "complete") -> None:
        self.outcome = outcome
        if outcome == "plan_validated":
            for state in self.stages.values():
                if state.status == "queued":
                    state.status = "planned"
        self.current_query = "-"
        self.current_query_id = "-"
        self.current_query_started_at = 0.0
        self.message("news reaction plan validated" if outcome == "plan_validated" else "news reaction build complete")

    def message(self, text: str) -> None:
        stamp = dt.datetime.now().astimezone().strftime("%H:%M:%S")
        line = f"{stamp} {text}"
        self.messages.append(line)
        if self._live is None:
            self._text(line)
        else:
            self._refresh()

    @property
    def query_elapsed_seconds(self) -> float:
        if self.current_query_started_at == 0.0:
            return 0.0
        return max(0.0, time.perf_counter() - self.current_query_started_at)

    def _render(self) -> object:
        from rich import box
        from rich.console import Group
        from rich.panel import Panel
        from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn
        from rich.table import Table
        from rich.text import Text

        elapsed = max(0.001, time.perf_counter() - self.started_at)
        state = self.stages.get(self.current_stage)
        query_elapsed = self.query_elapsed_seconds
        title_status = self.outcome if self.outcome != "starting" else (state.status if state else "starting")
        summary = Table(box=None, expand=True, show_header=False, pad_edge=False)
        summary.add_column("Metric", style="cyan", no_wrap=True, width=13)
        summary.add_column("Value", overflow="fold")
        summary.add_row("Run", self.run_id)
        summary.add_row("Status", f"{title_status.upper()}  elapsed={_duration(elapsed)}")
        summary.add_row("Current", f"{self.current_stage} / {self.current_unit}")
        summary.add_row("Query", f"{self.current_query}  elapsed={_duration(query_elapsed)}")
        if self.last_error:
            summary.add_row("Error", f"[red]{self.last_error}[/red]")

        current_progress = Progress(
            TextColumn("[cyan]{task.description}"),
            BarColumn(bar_width=None),
            TextColumn("{task.completed:,.0f}/{task.total:,.0f}"),
            TimeRemainingColumn(),
            expand=True,
        )
        if state is not None:
            current_progress.add_task(state.name, total=max(1, state.total), completed=state.processed)

        height = shutil.get_terminal_size((120, 40))[1]
        border = "red" if self.last_error else ("green" if self.outcome == "complete" else "cyan")
        header = Panel(
            summary,
            title=f"News Reaction Labels - {title_status.upper()}",
            subtitle=f"output={self.run_root}",
            border_style=border,
            box=box.ROUNDED,
            padding=(0, 1),
        )

        messages = Table(title="Messages", box=box.SIMPLE, expand=True, show_header=False, pad_edge=False)
        messages.add_column("Message")
        message_limit = 3 if height < 26 else self.log_lines
        for line in list(self.messages)[-message_limit:]:
            messages.add_row(Text(line, overflow="fold"))

        if height < 26:
            return Group(header, current_progress, messages)

        stages = Table(title="Stages", box=box.SIMPLE, expand=True, header_style="bold cyan", pad_edge=False)
        stages.add_column("Stage")
        stages.add_column("Status")
        stages.add_column("Progress", justify="right")
        stages.add_column("Skipped", justify="right")
        stages.add_column("Failed", justify="right")
        stages.add_column("Rows", justify="right")
        for item in self.stages.values():
            style = "red" if item.failed else ("green" if item.status == "complete" else "yellow" if item.status == "active" else "dim")
            stages.add_row(
                item.name,
                f"[{style}]{item.status}[/{style}]",
                f"{item.processed:,}/{item.total:,}",
                f"{item.skipped:,}",
                f"{item.failed:,}",
                f"{item.rows:,}",
            )

        recent = Table(title="Recent durable units", box=box.SIMPLE, expand=True, header_style="bold cyan", pad_edge=False)
        recent.add_column("Stage")
        recent.add_column("Unit")
        recent.add_column("Status")
        recent.add_column("Rows", justify="right")
        recent.add_column("Sec", justify="right")
        recent_limit = 4 if height < 36 else 8
        for result in list(self.recent_results)[-recent_limit:]:
            recent.add_row(result.stage, result.unit, result.status, f"{result.rows:,}", f"{result.elapsed_seconds:.1f}")
        return Group(header, current_progress, stages, recent, messages)

    def _start_refresh_thread(self) -> None:
        def refresh_loop() -> None:
            interval = 1.0 / self.refresh_per_second if self._live is not None else 15.0
            while not self._stop_refresh.wait(interval):
                if self._live is not None:
                    self._refresh()
                elif self.current_query_started_at:
                    stamp = dt.datetime.now().astimezone().strftime("%H:%M:%S")
                    self._text(
                        f"{stamp} query active {self.current_query} query_id={self.current_query_id} "
                        f"elapsed={_duration(self.query_elapsed_seconds)}"
                    )

        self._refresh_thread = threading.Thread(target=refresh_loop, name="news-reaction-progress", daemon=True)
        self._refresh_thread.start()

    def _refresh(self) -> None:
        if self._live is None:
            return
        with self._lock:
            self._live.update(self._render(), refresh=True)

    @staticmethod
    def _text(line: str) -> None:
        print(line, flush=True)


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
