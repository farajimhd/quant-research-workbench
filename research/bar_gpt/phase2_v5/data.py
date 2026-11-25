"""Join complete future candles to certified session pools without reading events."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import numpy as np
import polars as pl
import torch
from ..v5.phase2_ar_shards.contract import FRAMES, ORIGIN, BLOCK, validate_bars
from ..v5.phase2_ar_shards.storage import ShardReader
from ..v5.shards.sql import clock
from ..v5.transforms_v1 import BarInputTransformV1
from ..phase2_v2.data import checked_unit
from .model import HORIZONS


def target_indices(ends, starts, origins, width):
    """Skip forming intervals and return -1 for missing observed horizons."""
    first = np.searchsorted(starts, origins, side="left")
    indices = first[:, None] + np.arange(width)
    if len(ends) and (np.any(np.diff(starts) <= 0) or np.any(starts >= ends)):
        raise ValueError("Target intervals must be ordered, nonempty candles")
    return np.where(indices < len(ends), indices, -1)


def successor_rows(table):
    """Find the next released session for each ticker within a split and root."""
    rows = table.select("path", "unit_split").with_row_index("row").with_columns(
        pl.col("path").str.replace_all("\\", "/", literal=True).str.split("/").list.get(1).alias("ticker"),
        pl.col("path").str.slice(-10).alias("day"))
    rows = rows.sort("ticker", "unit_split", "day").with_columns(
        pl.col("row").cast(pl.Int64).shift(-1).over("ticker", "unit_split").fill_null(-1).alias("next"))
    return rows.sort("row")["next"].to_numpy()


def daily_extension(reader, future, pool, day):
    """Require a shared terminal daily anchor to prove ordinal and basis continuity."""
    if future is None or not len(pool):
        return pool, "missing-successor"
    other = future.view(4)
    validate_bars(other)
    end = pool["end_us"][-1]
    pos = np.searchsorted(other["end_us"], end)
    if pos + 1 >= len(other) or other["end_us"][pos] != end:
        return pool, "no-overlapping-daily-anchor"
    # On a split the anchor appears rebased in the later publication. Refuse that
    # target rather than mixing share bases. Equal anchor alone is insufficient
    # when no current-session daily bar is present, so require that bar as well.
    if end != clock(day, 16) or pool[-1].tobytes() != other[pos].tobytes():
        return pool, "uncertain-basis-or-anchor"
    if reader.price_basis["actions_hash"] != future.price_basis["actions_hash"]:
        return pool, "action-catalog-mismatch"
    return np.concatenate((pool, other[pos + 1:pos + 2])), "verified-successor"


@dataclass
class Session:
    pools: list
    endpoints: np.ndarray
    targets: list
    origins: np.ndarray
    daily_status: str


def prepare(unit, successor=None):
    reader = checked_unit(unit)
    pools = [reader.view(f) for f in range(5)]
    for array in pools:
        validate_bars(array)
    blocks = reader.array("origins.npy")
    if blocks.dtype != ORIGIN or blocks.shape != (reader.receipt["blocks"], BLOCK):
        raise ValueError("Unexpected certified origin geometry")
    rows = blocks[[x[0] for x in unit.blocks]].reshape(-1)
    times, ends = rows["end_us"], rows["latest"].astype(np.int64)
    day = reader.receipt["session_date"]
    if np.any(np.diff(times) <= 0) or np.any(times <= clock(day, 4)) or np.any(times > clock(day, 20)):
        raise ValueError("Origin outside ordered session")
    for frame, pool in enumerate(pools):
        expected = np.searchsorted(pool["end_us"], times, side="right") - 1
        if not np.array_equal(expected, ends[:, frame]) or (expected < 63).any():
            raise ValueError("Context is missing or includes unavailable values")
    if not np.array_equal(pools[0]["end_us"][ends[:, 0]], times):
        raise ValueError("Origin must match its final observed second")
    target_pools = list(pools)
    other = None
    if successor is not None:
        other = ShardReader(successor.path, successor.binding)
        if (other.receipt["hash"] != successor.receipt_hash
                or other.receipt["ticker"] != reader.receipt["ticker"]
                or other.receipt["session_date"] <= day
                or other.receipt["session_date"][:4] != day[:4]
                or not other.price_basis):
            raise ValueError("Successor lineage differs")
    target_pools[4], status = daily_extension(reader, other, pools[4], day)
    targets = []
    for frame, (width, pool) in enumerate(zip(HORIZONS, target_pools)):
        closes = pool["end_us"]
        if frame == 4:
            # Certified regular daily buckets are 09:30–16:00 New York.
            # Both endpoints are on the same local day, including DST changes.
            starts = closes - (6 * 3600 + 30 * 60) * 1_000_000
        else:
            starts = closes - FRAMES[frame]
        targets.append(target_indices(closes, starts, times, width))
    keep = np.concatenate(targets, axis=1).max(1) >= 0
    return Session(target_pools, ends[keep], [x[keep] for x in targets], times[keep], status)


def sessions(units, split, start, workers):
    next_rows = [successor_rows(table) for _, _, table in units.groups]
    def load(index, unit):
        group = int(np.searchsorted(units.ends, index, side="right"))
        offset = units.ends[group-1] if group else 0
        next_row = next_rows[group][index-offset]
        following = units[offset + int(next_row)] if next_row >= 0 else None
        return index, prepare(unit, following)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        source = iter(units.iter_from(split, start))
        pending = deque()
        for _ in range(workers * 2):
            item = next(source, None)
            if item is not None:
                pending.append(executor.submit(load, *item))
        while pending:
            work = pending.popleft()
            item = next(source, None)
            if item is not None:
                pending.append(executor.submit(load, *item))
            yield work.result()


def log_grid(raw, valid):
    grid = torch.where(valid, raw, 1).double().reshape(*raw.shape[:-1], 3, 6)
    result = torch.cat((grid[..., :4].log(), grid[..., 4:].log1p()), -1).reshape_as(raw)
    return torch.where(valid, result, 0).float()


class DeviceSession:
    """Copy each pool once and encode one V29 window per unique completed end."""
    def __init__(self, session, teacher, token_batch, device):
        self.encoded = []
        self.targets = []
        self.anchors = []
        self.num_windows = 0
        self.missing = []
        offsets = torch.arange(63, -1, -1, device=device)
        for f, pool in enumerate(session.pools):
            raw = torch.from_numpy(np.ascontiguousarray(pool["values"])).to(device)
            valid = torch.from_numpy(np.ascontiguousarray(pool["valid"], dtype=bool)).to(device)
            clocks = torch.from_numpy(np.ascontiguousarray(pool["end_us"])).to(device)
            logs = log_grid(raw, valid)
            unique, reverse = np.unique(session.endpoints[:, f], return_inverse=True)
            ends = torch.as_tensor(unique, device=device)
            bank, anchors, anchor_masks = [], [], []
            for chunk in ends.split(token_batch):
                gather = chunk[:, None] - offsets
                x, v, times = logs[gather], valid[gather], clocks[gather]
                frame = torch.full_like(chunk, FRAMES[f])
                assembled = BarInputTransformV1.assemble(x, v, times, frame, times > 0,
                                                         torch.diff(times, dim=1))
                bank.append(teacher.encode(assembled.values, assembled.valid))
                close_valid = v[..., [3, 9, 15]]
                positions = torch.arange(64, device=device).view(1, 64, 1)
                last = torch.where(close_valid, positions, -1).amax(1)
                price = raw[gather].gather(1, last.clamp_min(0)[:, None].expand(-1, 1, 3)
                                          + torch.zeros_like(last[:, None])) if False else raw[gather][..., [3, 9, 15]].gather(1, last.clamp_min(0)[:, None]).squeeze(1)
                anchors.append(price)
                anchor_masks.append(last >= 0)
            index = torch.as_tensor(reverse, device=device)
            self.encoded.append((torch.cat(bank), index))
            self.anchors.append((torch.cat(anchors), torch.cat(anchor_masks), index))
            targets = torch.as_tensor(session.targets[f], device=device)
            self.targets.append((raw, logs, valid, targets))
            self.num_windows += len(unique)
            self.missing.append(int((session.targets[f] < 0).sum()))

    def batch(self, indices, *, metrics=False):
        windows = torch.stack([bank[reverse[indices]] for bank, reverse in self.encoded], 1)
        raw_parts, logs, masks, anchors, anchor_valid = [], [], [], [], []
        for (raw, values, valid, table), (last, checked, reverse) in zip(self.targets, self.anchors):
            target = table[indices]
            ids = target.clamp_min(0)
            logs.append(values[ids])
            masks.append(valid[ids] & (target >= 0)[..., None])
            if metrics:
                raw_parts.append(raw[ids])
                anchors.append(last[reverse[indices], None].expand(-1, table.shape[1], -1))
                anchor_valid.append(checked[reverse[indices], None].expand(-1, table.shape[1], -1))
        return windows, torch.cat(logs, 1), torch.cat(masks, 1), (
            (torch.cat(raw_parts, 1), torch.cat(anchors, 1), torch.cat(anchor_valid, 1))
            if metrics else None)

