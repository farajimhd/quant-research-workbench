"""Certify and cache selected premarket prefixes from saved source identities.

The saved run selects source products, not actions or financial outcomes.
No app orders/fills enter the search. Database access is SELECT-only. This
retains the frozen release funnel; it cannot search outside that population.
"""

import json
from contextlib import closing
from pathlib import Path

from scripts.clickhouse.report_strategy_one_trades import SelectOnly
from src.backend.backtest_v3_clients import v3_client
from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env

from .strategy_one_inputs import load_replay_frames, load_saved_inputs

RUNTIME = Path("D:/TradingML/runtimes")
PREMARKET_START = 14_400_000
PREMARKET_END = 34_200_000


def certified_cache(run_id, directory, *, existing=None, progress=print):
    """Verify current seals even for reused parquet, then prepare its hash proof."""
    with (
        closing(v3_client("read")) as market,
        closing(backtest_v4_operator_client_from_env()) as journal,
    ):
        fixed, release, saved, context, _, timings = load_saved_inputs(
            SelectOnly(journal), SelectOnly(market), run_id, progress=progress
        )
    definition = saved["definition"]
    if (
        definition["start_local_ms"] != PREMARKET_START
        or definition["end_local_ms"] < PREMARKET_END
        or definition["activation_delay_us"] != 0
        or definition["simulation_profile"] != "baseline"
        or context["strategy_revision"] != 1
        or context["configuration_hash"] != release.payload_hash
    ):
        raise RuntimeError(
            "Saved source must cover the no-delay Strategy 1 premarket contract"
        )
    through = PREMARKET_END - PREMARKET_START
    cache = Path(existing or directory).resolve()
    if not cache.is_relative_to(RUNTIME.resolve()) or not RUNTIME.is_dir():
        raise ValueError("Session cache must belong to the required runtime root")
    if (cache / "manifest.json").exists():
        manifest = json.loads((cache / "manifest.json").read_text())
        for key, value in (
            ("market_token", fixed.market.token),
            ("seed_token", fixed.seeds.token),
            ("entry_token", fixed.entry.token),
            ("price_token", fixed.prices.token),
            ("through_ms", through),
        ):
            if manifest[key] != value:
                raise RuntimeError(
                    "Cached session differs from current certificate: " + key
                )
        timings["cache_reused"] = True
    else:
        if cache.exists() and any(cache.iterdir()):
            raise RuntimeError(
                "Incomplete cache retained; select a new output directory"
            )
        _, _, _, seconds = load_replay_frames(
            fixed, through_ms=through, output=cache, progress=progress
        )
        timings["fetch_seconds"] = seconds
    return {
        "run_id": run_id,
        "cache": str(cache),
        "session_date": str(fixed.market.sessions[0]),
        "configuration_hash": release.payload_hash,
        "timings": timings,
        "saved_app_initial_cash": float(definition["initial_cash"]),
        "search_initial_cash": 10000,
        "financial_journal_used_for_objective": False,
        "selected_start_local_ms": PREMARKET_START,
        "selected_end_local_ms": PREMARKET_END,
    }


def validate_split(training, validation):
    """A session date has one role even if two saved run IDs refer to it."""
    train = [s["session_date"] for s in training]
    valid = [s["session_date"] for s in validation]
    if (
        not train
        or not valid
        or len(set(train)) != len(train)
        or len(set(valid)) != len(valid)
    ):
        raise ValueError("Training/validation require nonempty unique session dates")
    if set(train) & set(valid) or min(valid) <= max(train):
        raise ValueError(
            "Validation must be disjoint and later than all training dates"
        )
    if len({s["configuration_hash"] for s in training + validation}) != 1:
        raise ValueError("Session source configurations differ")
