from __future__ import annotations

import copy
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any

from src.backtest.equity_candles import build_portfolio_candles
from src.backtest.results import write_json, write_table


class ArtifactWriter:
    """Serializes backtest artifacts on a background thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest-artifacts")
        self._futures: list[Future] = []
        self._pending_paths: set[Path] = set()
        self._completed_errors: list[BaseException] = []
        self._lock = Lock()

    def write_json(self, path: Path, data: dict[str, Any], *, coalesce: bool = False) -> bool:
        if coalesce and not self._reserve_path(path):
            return False
        payload = copy.deepcopy(data)
        self._submit(write_json, path, payload, tracked_path=path if coalesce else None)
        return True

    def write_table(self, path: Path, rows: list[dict[str, Any]], *, coalesce: bool = False) -> bool:
        if coalesce and not self._reserve_path(path):
            return False
        snapshot = [dict(row) for row in rows]
        self._submit(write_table, path, snapshot, tracked_path=path if coalesce else None)
        return True

    def write_text(self, path: Path, text: str, *, coalesce: bool = False) -> bool:
        if coalesce and not self._reserve_path(path):
            return False
        payload = str(text)
        self._submit(_write_text, path, payload, tracked_path=path if coalesce else None)
        return True

    def write_portfolio_candles(
        self,
        path: Path,
        portfolio_rows: list[dict[str, Any]],
        *,
        initial_cash: float,
        coalesce: bool = False,
    ) -> bool:
        if coalesce and not self._reserve_path(path):
            return False
        snapshot = [dict(row) for row in portfolio_rows]
        self._submit(_write_portfolio_candles, path, snapshot, float(initial_cash), tracked_path=path if coalesce else None)
        return True

    def wait(self) -> None:
        self._prune_done()
        pending = self._futures
        self._futures = []
        first_error: BaseException | None = None
        if self._completed_errors:
            first_error = self._completed_errors.pop(0)
        for future in pending:
            try:
                future.result()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def close(self) -> None:
        try:
            self.wait()
        finally:
            self._executor.shutdown(wait=True)

    def _submit(self, fn, *args, tracked_path: Path | None = None) -> None:
        self._prune_done()
        future = self._executor.submit(fn, *args)
        if tracked_path is not None:
            future.add_done_callback(lambda done, path=tracked_path: self._release_path(path, done))
        self._futures.append(future)

    def _reserve_path(self, path: Path) -> bool:
        self._prune_done()
        normalized = path.resolve()
        with self._lock:
            if normalized in self._pending_paths:
                return False
            self._pending_paths.add(normalized)
            return True

    def _release_path(self, path: Path, future: Future) -> None:
        with self._lock:
            self._pending_paths.discard(path.resolve())

    def _prune_done(self) -> None:
        if not self._futures:
            return
        pending: list[Future] = []
        for future in self._futures:
            if not future.done():
                pending.append(future)
                continue
            exception = future.exception()
            if exception is not None:
                self._completed_errors.append(exception)
        self._futures = pending

    def __enter__(self) -> ArtifactWriter:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_portfolio_candles(path: Path, portfolio_rows: list[dict[str, Any]], initial_cash: float) -> None:
    write_table(path, build_portfolio_candles(portfolio_rows, initial_cash=initial_cash))
