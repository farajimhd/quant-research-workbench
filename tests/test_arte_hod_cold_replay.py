from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

import pytest

from src.market_engine.derived_trade_policy import POLICY
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.reaction_band import CONFIG
from src.market_engine.streaming_level_book import EXTRACTION_VERSION
from src.trading_runtime.arte_hod_cold_replay import (
    MANIFEST_TABLE, HodReplayFrame, _hash, project_hod_replay_manifest,
    replay_hod_state, verify_hod_cold_replay,
)
from src.trading_runtime.historical_hod import DEFAULTS


SESSION = "2026-09-24"
TICKER = "TEST"
START = int(datetime(2026, 9, 24, 4, 0, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1_000_000)
HASH = sha256(b"certified").hexdigest()


def _seed():
    prior = dict(version="historical-level-mle-book-1",
                 source_extraction_version=EXTRACTION_VERSION,
                 band_config={**CONFIG, "coverage": .8}, ticker=TICKER,
                 session="2026-09-23", available_at=START / 1_000_000 - 60,
                 input_policy=POLICY, retrospective=True,
                 source_checkpoint_hash=HASH, levels=[])
    return {**prior, "checkpoint_hash": digest(prior)}


def _frames():
    frames = [HodReplayFrame(sequence=i, timeframe="1s",
                             completed_at_us=START + (i + 1) * 1_000_000,
                             open_int=100_000, high_int=101_000,
                             low_int=99_000, close_int=100_500,
                             volume=1000.0, macd_line=.2, macd_signal=.1,
                             execution_vwap=10.02, atr_14=.2)
              for i in range(5)]
    frames.append(HodReplayFrame(sequence=5, timeframe="5s",
                                 completed_at_us=START + 5_000_000,
                                 open_int=100_000, high_int=101_000,
                                 low_int=99_000, close_int=100_500,
                                 volume=5000.0, macd_line=.2, macd_signal=.1,
                                 execution_vwap=10.02, atr_14=.2))
    return frames


def _manifest(seed, frames, parameters, state, level_hash):
    return project_hod_replay_manifest(
        run_id="run-1", assignment_id="assignment-1", ticker=TICKER,
        session=SESSION, as_of_us=START + 5_000_000,
        market_build_id="market-build", bars_attempt_id="bars-attempt",
        indicators_attempt_id="indicator-attempt",
        liquidity_attempt_id="liquidity-attempt",
        market_source_plan_hash=HASH, market_coverage_hash=HASH,
        frame_source_revision="certified-market-day-v1",
        seed_source_plan_hash=HASH, seed_coverage_hash=HASH,
        replay_code_revision="45896f1b", seed=seed, splits=[], frames=frames,
        parameters=parameters, expected_v7_levels_hash=level_hash,
        expected_hod_state_hash=_hash(state),
    )


def test_actual_v7_and_hod_producer_replay_matches_sealed_manifest():
    seed, frames, parameters = _seed(), _frames(), {"historical_hod": dict(DEFAULTS)}
    state, level_hash = replay_hod_state(ticker=TICKER, session=SESSION,
                                        as_of_us=START + 5_000_000, seed=seed,
                                        splits=[], frames=frames, parameters=parameters)
    manifest = _manifest(seed, frames, parameters, state, level_hash)
    assert verify_hod_cold_replay(manifest, seed=seed, splits=[], frames=frames,
                                  parameters=parameters) == state
    ddl = MANIFEST_TABLE.ddl()
    assert "PARTITION BY toYYYYMM(session)" in ddl
    assert "storage_policy = 'live_market_ssd'" in ddl
    assert not any(token in ddl for token in (" Array(", " Map(", " JSON", " payload "))


@pytest.mark.parametrize("mode", ["frame", "seed", "parameters", "expected", "levels", "order", "missing"])
def test_cold_replay_fails_closed_on_source_or_parity_drift(mode):
    seed, frames, parameters = _seed(), _frames(), {"historical_hod": dict(DEFAULTS)}
    state, level_hash = replay_hod_state(ticker=TICKER, session=SESSION,
                                        as_of_us=START + 5_000_000, seed=seed,
                                        splits=[], frames=frames, parameters=parameters)
    manifest = _manifest(seed, frames, parameters, state, level_hash)
    if mode == "frame":
        frames = deepcopy(frames)
        frames[0] = replace(frames[0], close_int=100_600)
    elif mode == "seed":
        seed = {**seed, "source_checkpoint_hash": sha256(b"changed").hexdigest()}
    elif mode == "parameters":
        parameters = {"historical_hod": {**DEFAULTS, "entry_breakout_offset": 1.0}}
    elif mode == "expected":
        manifest = {**manifest, "expected_hod_state_hash": HASH}
        manifest["content_hash"] = _hash({key: value for key, value in manifest.items()
                                          if key != "content_hash"})
    elif mode == "levels":
        manifest = {**manifest, "expected_v7_levels_hash": HASH}
        manifest["content_hash"] = _hash({key: value for key, value in manifest.items()
                                          if key != "content_hash"})
    elif mode == "order":
        frames = [*frames]
        frames[0], frames[1] = frames[1], frames[0]
    else:
        frames = frames[:-1]
    with pytest.raises(ValueError):
        verify_hod_cold_replay(manifest, seed=seed, splits=[], frames=frames,
                               parameters=parameters)
