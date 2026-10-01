"""Compare a performance variant with a saved, Polars-verified trajectory."""

import json
from pathlib import Path

import numpy as np
import polars as pl

from research.vectorized_backtest.v1.strategy_encoding.core import EncodingError

from .v7_cache import file_hash


def compare_saved(path, report, accounts, state):
    path = Path(path)
    baseline = json.loads(path.read_text(encoding="utf-8"))
    if not (baseline.get("reference") or {}).get("full_account_and_final_state_parity"):
        raise EncodingError(
            "Comparison baseline must already be fully validated against Polars"
        )
    for key in ("source_key", "program_fingerprint", "candidate_parameters"):
        if baseline["backtest"][key] != report["backtest"][key]:
            raise EncodingError(f"Baseline input differs: {key}")
    if baseline["broker"] != report["broker"]:
        raise EncodingError("Baseline broker differs")
    earlier = pl.read_parquet(path.parent / "accounts.parquet")
    if not accounts["time_us"].equals(earlier["time_us"]):
        raise EncodingError("Baseline account clocks differ")
    columns = ["cash", "realized_pnl", "equity", "market_value"]
    actual = accounts.select(columns).to_numpy()
    expected = earlier.select(columns).to_numpy()
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-7)
    previous = pl.read_parquet(path.parent / "final_state.parquet").sort("listing_id")
    for key in ("listing_id", "quantity", "remaining", "side", "submitted_us"):
        if not state[key].equals(previous[key]):
            raise EncodingError(f"Baseline integer state differs: {key}")
    for key in ("book_cost", "stop", "target", "mark"):
        np.testing.assert_allclose(
            state[key].to_numpy(), previous[key].to_numpy(), rtol=0, atol=1e-7
        )
    for key in ("filled_shares", "objective", "fees"):
        np.testing.assert_allclose(
            report["backtest"][key], baseline["backtest"][key], rtol=0, atol=1e-7
        )
    return {
        "full_account_and_final_state_parity": True,
        "atol": 1e-7,
        "max_account_difference": float(np.max(np.abs(actual - expected))),
        "baseline_report": str(path),
        "baseline_report_sha256": file_hash(path),
    }
