"""CPU-only preparation through the serving source and native v3 target contract."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from zoneinfo import ZoneInfo

import torch

from research.bar_gpt.v3.schema import FEATURE_INDEX
from research.bar_gpt.v3.targets import build_physical_horizon_targets
from .cache import RawBar
from .evaluation_store import atomic_json, bind_manifest, digest, encoded, file_hash
from .sources import HistoricalBootstrap

NY = ZoneInfo("America/New_York")


def clock(day: str, local_time: str) -> int:
    return int(dt.datetime.fromisoformat(f"{day}T{local_time}").replace(tzinfo=NY).timestamp() * 1_000_000)


def read_packet(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def make_packet(path: Path, bars: list[RawBar], ticker: str, day: str,
                start_us: int, end_us: int, horizons: tuple[int, ...], revision: dict) -> dict:
    """All future rows remain on disk; replay admits only availability <= clock."""
    one = sorted((bar for bar in bars if bar.view == "1s"), key=lambda bar: bar.available_at_us)
    if not one or len({bar.available_at_us for bar in one}) != len(one):
        raise RuntimeError("missing or duplicate one-second support")
    origins = [i for i, bar in enumerate(one) if start_us <= bar.available_at_us < end_us
               and bar.values[FEATURE_INDEX["origin_eligible"]] > 0
               and bar.values[FEATURE_INDEX["trade_present"]] > 0]
    if not origins:
        raise RuntimeError(f"no eligible origins for {ticker} {day}")
    raw = torch.tensor([bar.values for bar in one], dtype=torch.float32)
    available = torch.tensor([bar.available_at_us for bar in one], dtype=torch.long)
    temporary = path.with_suffix(".building.sqlite")
    if temporary.exists():
        temporary.unlink()  # Unpublished, task-owned interrupted preparation only.
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""
            CREATE TABLE bars (available INTEGER, view TEXT, start INTEGER, payload TEXT,
                              PRIMARY KEY(view,start));
            CREATE INDEX bars_clock ON bars(available);
            CREATE TABLE targets (origin INTEGER PRIMARY KEY, payload TEXT);
            CREATE TABLE metadata (payload TEXT);
        """)
        db.executemany("INSERT INTO bars VALUES(?,?,?,?)", (
            (bar.available_at_us, bar.view, bar.bar_start_us, encoded(asdict(bar))) for bar in bars))
        close = raw[:, FEATURE_INDEX["trade_close"]].double()
        valid_close = close > 0
        prior_idx = torch.where(valid_close, torch.arange(len(one)), -1).cummax(0).values
        for offset in range(0, len(origins), 128):
            indices = torch.tensor(origins[offset:offset + 128], dtype=torch.long)
            target = build_physical_horizon_targets(
                raw, indices, torch.tensor(horizons), available_at_us=available,
                # Conservative boundary: never infer completeness beyond observed support.
                coverage_end_us=min(int(available[-1]), clock(day, "20:00:00")),
            )
            for position, index in enumerate(indices.tolist()):
                origin = int(available[index])
                # Frozen causal momentum baseline: previous 60 wall-clock seconds,
                # extrapolated in log-price space to each physical horizon.
                lookup = int(torch.searchsorted(available, origin - 60_000_000, right=True)) - 1
                previous = int(prior_idx[lookup]) if lookup >= 0 else -1
                current = int(prior_idx[index])
                momentum = None
                if previous >= 0 and current >= 0:
                    elapsed = (int(available[current]) - int(available[previous])) / 1e6
                    if elapsed > 0:
                        rate = torch.log(close[current] / close[previous]).item() / elapsed
                        momentum = [rate * (h / 1e6) for h in horizons]
                payload = {"values": target.values[position].tolist(), "mask": target.mask[position].tolist(),
                           "momentum_log_return": momentum}
                db.execute("INSERT INTO targets VALUES(?,?)", (origin, encoded(payload)))
        metadata = {"ticker": ticker, "day": day, "origins": len(origins), "bars": len(bars),
                    "start_us": start_us, "end_us": end_us, "horizons_us": horizons,
                    "source_revision": revision, "condition_targets": "unscored: separate condition support unavailable"}
        db.execute("INSERT INTO metadata VALUES(?)", (encoded(metadata),))
        db.commit()
    finally:
        db.close()
    os.replace(temporary, path)
    return {"file": path.name, "sha256": file_hash(path), **metadata}


def prepare(root: Path, release, tickers: list[str], days: list[str], start: str, end: str,
            code_hash: str, partition: str) -> dict:
    plan = {"version": 1, "tickers": tickers, "days": days, "start": start, "end": end,
            "data_config": asdict(release.data_config), "code_hash": code_hash,
            "partition": partition, "target_version": "native-v3-physical",
            "source": "HistoricalBootstrap/certified-compact-events"}
    identity = bind_manifest(root / "plan.json", plan)
    source = HistoricalBootstrap(release)
    index_path = root / "dataset.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"identity": identity, "packets": []}
    if index["identity"] != identity:
        raise RuntimeError("dataset plan mismatch")
    completed = {(row["ticker"], row["day"]): row for row in index["packets"]}
    for day in days:
        for ticker in tickers:
            previous = completed.get((ticker, day))
            if previous:
                if file_hash(root / previous["file"]) != previous["sha256"]:
                    raise RuntimeError("prepared packet hash mismatch")
                continue
            started = time.perf_counter()
            as_of = dt.datetime.fromtimestamp(clock(day, start) / 1e6, tz=dt.UTC)
            before = source.source_revision(ticker, as_of)
            if not any(str(row["source_date"]) == day for row in before["source_days"]):
                raise RuntimeError(f"source-day evidence missing: {day}")
            if not any(str(row["source_date"]) == day for row in before["event_ticker_days"]):
                raise RuntimeError(f"ticker-day evidence missing: {ticker} {day}")
            print(f"prepare active={ticker}/{day} completed={len(index['packets'])} total={len(days)*len(tickers)}", flush=True)
            bars = source.load(ticker, as_of)
            after = source.source_revision(ticker, as_of)
            if digest(before) != digest(after):
                raise RuntimeError("source revision changed during preparation")
            packet = make_packet(root / f"{day}-{ticker}.sqlite", bars, ticker, day,
                                 clock(day, start), clock(day, end), tuple(release.data_config.horizons_us), before)
            packet["preparation_seconds"] = time.perf_counter() - started
            index["packets"].append(packet)
            atomic_json(index_path, index)
    return index


def validate_dataset(root: Path) -> tuple[dict, dict]:
    plan = json.loads((root / "plan.json").read_text())
    index = json.loads((root / "dataset.json").read_text())
    if digest(plan["manifest"]) != plan["identity"] or index["identity"] != plan["identity"]:
        raise RuntimeError("dataset manifest identity mismatch")
    expected = {(ticker, day) for ticker in plan["manifest"]["tickers"] for day in plan["manifest"]["days"]}
    observed = [(row["ticker"], row["day"]) for row in index["packets"]]
    if len(observed) != len(expected) or set(observed) != expected:
        raise RuntimeError("dataset incomplete or contains duplicate packets")
    for row in index["packets"]:
        path = (root / row["file"]).resolve()
        if not path.is_relative_to(root.resolve()) or file_hash(path) != row["sha256"]:
            raise RuntimeError("dataset packet hash/path mismatch")
    return plan, index
