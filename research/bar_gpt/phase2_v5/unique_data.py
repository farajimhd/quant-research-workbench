"""Session-local, deduplicated Phase 2 data and its certified reader."""
from functools import cached_property
from pathlib import Path
import json

import numpy as np
import polars as pl

from ..v5.phase2_ar_shards.contract import BAR, FRAMES
from ..v5.shards.contract import digest, file_hash
from ..v5.shards.sql import clock
from .data import Session, eligible_session, target_indices
from .model import HORIZONS

VERSION = "phase2-unique-context-v1"
POOL_KEYS = tuple(f"pool{i}" for i in range(len(FRAMES)))


def merge_sessions(sources, audit=None):
    """Preserve every origin and context; label from the union of certified pools."""
    sessions, bases = zip(*sources)
    if any(b != bases[0] for b in bases[1:]):
        raise ValueError("Price basis differs for source copies")
    clocks = np.unique(np.concatenate([s.origins for s in sessions]))
    destinations = [np.searchsorted(clocks, s.origins) for s in sessions]
    pools, targets = [], []
    endpoints = np.full((len(clocks), len(FRAMES)), -1, np.int32)
    for f, width in enumerate(HORIZONS):
        # Compare shared records bytewise so source masks, float bit patterns
        # and clock evidence survive. Endpoints are checked after remapping.
        values = np.concatenate([s.pools[f] for s in sessions])
        order = np.argsort(values["end_us"], kind="stable")
        ordered = values[order]
        repeated = np.flatnonzero(np.diff(ordered["end_us"]) == 0) + 1
        raw = ordered.view(f"V{BAR.itemsize}")
        if (raw[repeated] != raw[repeated-1]).any():
            raise ValueError(f"Source bar bytes disagree at a shared clock, frame {f}")
        keep = np.r_[True, np.diff(ordered["end_us"]) > 0]
        pool = ordered[keep]
        pools.append(pool)
        closes = pool["end_us"]
        duration = (6 * 3600 + 30 * 60) * 1_000_000 if f == 4 else FRAMES[f]
        # Intraday pools include input warm-up and can include partial source
        # ranges. The forecast stays within the source session.
        label_closes = closes
        if f != 4 and "session_date" in bases[0]:
            label_closes = closes[:np.searchsorted(closes, clock(bases[0]["session_date"], 20), side="right")]
        out = target_indices(label_closes, label_closes-duration, clocks, width).astype(np.int32)
        changes = filled = 0
        for s, dest in zip(sessions, destinations):
            mapping = np.searchsorted(pool["end_us"], s.pools[f]["end_us"])
            end = s.endpoints[:, f]
            mapped = mapping[end]
            # Each mapped 64-bar slice must contain the exact original rows
            # with zero insertion, even when pools have distinct start clocks.
            gaps = np.r_[0, np.cumsum(np.diff(mapping) != 1)]
            if (gaps[end] != gaps[end-63]).any():
                raise ValueError(f"Pool union inserts a bar into a context, frame {f}")
            prior = endpoints[dest, f]
            if ((prior >= 0) & (prior != mapped)).any():
                raise ValueError(f"Copies select different completed contexts, frame {f}")
            endpoints[dest, f] = mapped
            src = s.targets[f]
            target = np.where(src >= 0, mapping[src.clip(min=0)], -1)
            canonical = out[dest]
            if ((target >= 0) & (canonical < 0)).any():
                raise ValueError(f"Pool union loses an available target, frame {f}")
            changes += int(((target >= 0) & (canonical != target)).sum())
            filled += int(((target < 0) & (canonical >= 0)).sum())
        if audit is not None:
            audit.append(dict(shifted=changes, filled=filled))
        targets.append(out)
    return Session(pools, endpoints, targets, clocks, "sealed-source-targets", 0)


def payload(session):
    return dict(zip(POOL_KEYS, session.pools), clocks=session.origins, endpoints=session.endpoints,
                **{f"target{i}": value for i, value in enumerate(session.targets)})


def read_session(path, sha, candidates, eligible):
    if file_hash(path) != sha:
        raise ValueError(f"Session payload hash differs: {path}")
    with np.load(path, allow_pickle=False) as data:
        origins = data["clocks"]
        if len(origins) != candidates or np.any(np.diff(origins) <= 0):
            raise ValueError(f"Unique session count/order differs: {path}")
        result = eligible_session([data[k] for k in POOL_KEYS], data["endpoints"],
                                  [data[f"target{i}"] for i in range(len(FRAMES))],
                                  origins, "sealed-source-targets")
    if len(result.origins) != eligible:
        raise ValueError(f"Session eligibility differs: {path}")
    return result


class UniqueRelease:
    """One catalog row per ticker/session. Shuffle the sessions on every pass."""
    def __init__(self, root, rows):
        self.root, self.rows = root, rows

    def __len__(self):
        return self.rows.height

    def order_for_pass(self, index):
        training = self.rows.filter(pl.col("split") == 0).sample(fraction=1., shuffle=True, seed=17041+1009*index)
        return UniqueRelease(self.root, training.vstack(self.rows.filter(pl.col("split") == 1)))

    @cached_property
    def prefix(self):
        counts = self.rows.select(pl.when(pl.col("split") == 0).then(pl.col("candidates")).otherwise(0)).to_series()
        return np.r_[0, np.cumsum(counts.to_numpy(), dtype=np.int64)]

    def candidate_prefix(self, index, offset):
        return int(self.prefix[index]) + offset

    @cached_property
    def train_eligible(self):
        return self.rows.filter(pl.col("split") == 0)["eligible"].sum()

    def load(self, index, row):
        name, sha, count, eligible = row
        return index, read_session(self.root / name, sha, count, eligible)

    def iter_load(self, split, start):
        rows = self.rows.with_row_index("index").filter((pl.col("index") >= start) & (pl.col("split") == split))
        for i, *row in rows.select("index", "path", "sha", "candidates", "eligible").iter_rows():
            yield i, row


def load_unique_release(certificate):
    certificate = Path(certificate)
    manifest = json.loads(certificate.read_text())
    sha = manifest.pop("hash")
    if manifest.get("version") != VERSION or sha != digest(manifest):
        raise ValueError("Expected sealed unique release")
    catalog = certificate.parent / "sessions.parquet"
    if file_hash(catalog) != manifest["catalog_sha256"]:
        raise ValueError("Unique release catalog hash differs")
    rows = pl.read_parquet(catalog)
    counts = rows.group_by("split").agg(pl.sum("candidates", "eligible"),
                                        pl.len().alias("sessions")).sort("split").to_dicts()
    if rows.height != manifest["sessions"] or rows["path"].n_unique() != rows.height:
        raise ValueError("Unique session inventory differs")
    if (rows["path"].str.contains(r"^sessions/[a-f0-9]{2}/[a-f0-9]{24}/[0-9-]{10}\.npz$").not_().any()
            or rows["candidates"].sum() != manifest["unique_origins"]
            or counts != manifest["split_counts"]):
        raise ValueError("Unique release paths or row counts differ")
    return UniqueRelease(certificate.parent, rows), dict(manifest, hash=sha, certificate=str(certificate))
