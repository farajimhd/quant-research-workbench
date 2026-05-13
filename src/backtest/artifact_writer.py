from __future__ import annotations

import copy
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from src.backtest.equity_candles import build_portfolio_candles
from src.backtest.results import write_json, write_table


class ArtifactWriter:
    """Serializes backtest artifacts on a background thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest-artifacts")
        self._futures: list[Future] = []

    def write_json(self, path: Path, data: dict[str, Any]) -> None:
        payload = copy.deepcopy(data)
        self._submit(write_json, path, payload)

    def write_table(self, path: Path, rows: list[dict[str, Any]]) -> None:
        snapshot = [dict(row) for row in rows]
        self._submit(write_table, path, snapshot)

    def write_text(self, path: Path, text: str) -> None:
        payload = str(text)
        self._submit(_write_text, path, payload)

    def write_portfolio_candles(self, path: Path, portfolio_rows: list[dict[str, Any]], *, initial_cash: float) -> None:
        snapshot = [dict(row) for row in portfolio_rows]
        self._submit(_write_portfolio_candles, path, snapshot, float(initial_cash))

    def wait(self) -> None:
        pending = self._futures
        self._futures = []
        first_error: Exception | None = None
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

    def _submit(self, fn, *args) -> None:
        self._futures.append(self._executor.submit(fn, *args))

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
