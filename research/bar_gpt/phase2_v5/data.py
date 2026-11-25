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
from ..phase0_v7.eligibility import model_validity
from ..phase0_v7.transforms import coordinates
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
    rejected_origins: int


def prepare(unit, successor=None):
    reader = checked_unit(unit)
    pools = [reader.view(f) for f in range(5)]
    for array in pools:
        validate_bars(array)
        quote = array["values"].reshape(-1,3,6)[:,1:]
        valid = array["valid"].reshape(-1,3,6)[:,1:].astype(bool)
        if ((quote[...,:4] <= 0) & valid[...,:4]).any() or ((quote[...,4:] < 0) & valid[...,4:]).any():
            raise ValueError("Invalid observed quote numerics")
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
                or (other.receipt["session_date"][:4] == "2026") != (day[:4] == "2026")
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
    return Session(target_pools, ends[keep], [x[keep] for x in targets], times[keep], status, int((~keep).sum()))


def sessions(units, split, start, workers):
    if not hasattr(units, "forecast_successors"):
        units.forecast_successors = [successor_rows(table) for _, _, table in units.groups]
    next_rows = units.forecast_successors
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



def packed_pools(teacher, pools, device, cached):
    """Prepare all five pools in one GPU transform, keeping timeframe boundaries explicit."""
    lengths = [len(pool) for pool in pools]
    widths = [((n + 63) // 64) * 64 for n in lengths]
    starts = np.cumsum([0, *widths[:-1]])
    slices = [slice(start, start + n) for start, n in zip(starts, lengths)]
    # Padding aligns each timeframe to an assemble() row. Padding is never
    # returned as a target or gathered as part of a certified context.
    raw_host = np.zeros((sum(widths), 18), dtype=pools[0]["values"].dtype)
    valid_host = np.zeros_like(raw_host, dtype=bool)
    clock_host = np.zeros(sum(widths), dtype=np.int64)
    for region, pool in zip(slices, pools):
        raw_host[region] = pool["values"]
        valid_host[region] = pool["valid"]
        clock_host[region] = pool["end_us"]
    raw, valid, clocks = [torch.from_numpy(a).to(device) for a in (raw_host, valid_host, clock_host)]
    logs = log_grid(raw, valid)
    if cached:
        frame_ids = torch.from_numpy(np.repeat(np.asarray(FRAMES, dtype=np.int64),
                                               np.asarray(widths) // 64)).to(device)
        features = bar_features(teacher, logs, valid, clocks, frame_ids)
    return [(raw[r], valid[r], clocks[r], logs[r], features[r] if cached else None) for r in slices]


def bar_features(teacher, logs, valid, clocks, frame_ids):
    """Build reusable bar features for aligned timeframe pools."""
    slots = clocks.reshape(-1, 64)
    assembled = BarInputTransformV1.assemble(logs.reshape(-1, 64, 18), valid.reshape(-1, 64, 18),
                                             slots, frame_ids, slots > 0, torch.diff(slots, dim=1))
    x = assembled.values.reshape(-1, 27)
    v = assembled.valid.reshape(-1, 27)
    # Internal tile boundaries may fall in a real context. Pool boundaries
    # cannot: that bar is always position zero and encode_cached clears its gap.
    gaps = torch.diff(clocks, prepend=clocks[:1]).clamp_min(0)
    x[:, 22] = torch.log1p(gaps.reshape_as(slots).double() / frame_ids[:, None]).flatten()
    v[:, 22] = True
    v = model_validity(x,v)
    code, market = coordinates(x[:,:18],v[:,:18])
    features = torch.cat((code,x[:,18:]),-1)
    observed = torch.cat((market,v[:,18:]),-1)
    safe = torch.where(observed,features,teacher.mean)
    scaled = (safe-teacher.mean)/teacher.scale
    scaled = torch.where(teacher.geometry_mask,torch.asinh(scaled),scaled)
    return torch.stack((scaled,observed.float(),v.float()),-1)


def encode_cached(teacher, features, gather):
    x = features[gather]
    pos = torch.arange(64,device=x.device,dtype=torch.float32)/63
    x[...,18,0] = torch.where(x[...,18,1].bool(),(pos-teacher.mean[18])/teacher.scale[18],0)
    x[:,0,22] = 0
    bars = []
    for tile in x.reshape(-1,81).split(teacher.config.chunk_bars):
        bars.append(teacher.readout(teacher.blocks(teacher.stem(tile))))
    embedded = torch.cat(bars).reshape(-1,64,32)
    return torch.cat([teacher.mixers(tile) for tile in embedded.split(teacher.config.mixer_chunk_windows)])


class DeviceSession:
    """Copy each pool once and encode one V29 window per unique completed end."""
    def __init__(self, session, teacher, token_batch, device, *, cached=True, verify=False, metrics=True):
        self.encoded = []
        self.targets = []
        self.anchors = []
        self.num_windows = 0
        self.missing = []
        offsets = torch.arange(63, -1, -1, device=device)
        prepared = packed_pools(teacher, session.pools, device, cached)
        unique_rows = [np.unique(session.endpoints[:, f], return_inverse=True) for f in range(len(prepared))]
        # A blocking H2D upload per index array previously interrupted the GPU
        # after each timeframe. Submit all index arrays before encoding begins.
        chunks = [a.reshape(-1) for (ends, reverse), target in zip(unique_rows, session.targets)
                  for a in (ends, reverse, target)]
        indices = torch.from_numpy(np.concatenate(chunks)).to(device).split([len(a) for a in chunks])
        for f, (raw, valid, clocks, logs, features) in enumerate(prepared):
            ends, index, flat_targets = indices[3*f:3*f+3]
            bank, anchors, anchor_masks = [], [], []
            for chunk in ends.split(token_batch):
                gather = chunk[:, None] - offsets
                if cached:
                    encoded = encode_cached(teacher,features,gather)
                if not cached or (verify and not bank):
                    x, v, times = logs[gather], valid[gather], clocks[gather]
                    frame = torch.full_like(chunk, FRAMES[f])
                    assembled = BarInputTransformV1.assemble(x, v, times, frame, times > 0, torch.diff(times,dim=1))
                    precision = torch.get_float32_matmul_precision()
                    if verify and cached and precision != "highest":
                        torch.set_float32_matmul_precision("highest")
                        try:
                            expected = teacher.encode(assembled.values, assembled.valid)
                            expected_logs = teacher.decode(expected)["logs"]
                            actual_logs = teacher.decode(encoded)["logs"]
                        finally:
                            torch.set_float32_matmul_precision(precision)
                        price = [0,1,2,3,6,7,8,9,12,13,14,15]
                        difference = (actual_logs[...,price]-expected_logs[...,price]).abs()
                        # Test log-price output drift rather than allowing an
                        # embedding-norm tolerance to hide economically large errors.
                        if bool((difference.mean() > .0001) | (difference.max() > .0025)):
                            raise ValueError(f"TF32 changes V29 log prices: mean={float(difference.mean())} max={float(difference.max())}")
                    else:
                        expected = teacher.encode(assembled.values, assembled.valid)
                        if cached:
                            torch.testing.assert_close(encoded,expected,rtol=1e-5,atol=3e-6)
                        else:
                            encoded = expected
                bank.append(encoded)
                if metrics:
                    channels = [3, 9, 15]
                    positions = torch.arange(64, device=device).view(1, 64, 1)
                    close_valid = valid[:, channels][gather]
                    last = torch.where(close_valid, positions, -1).amax(1)
                    price = raw[:, channels][gather].gather(1, last.clamp_min(0)[:, None]).squeeze(1)
                    anchors.append(price)
                    anchor_masks.append(last >= 0)
            self.encoded.append((bank[0] if len(bank) == 1 else torch.cat(bank), index))
            if metrics:
                self.anchors.append((torch.cat(anchors), torch.cat(anchor_masks), index))
            targets = flat_targets.view(session.targets[f].shape)
            self.targets.append((raw, logs, valid, targets))
            self.num_windows += len(ends)
            self.missing.append(int((session.targets[f] < 0).sum()))

    def batch(self, indices, *, metrics=False):
        windows = torch.stack([bank[reverse[indices]] for bank, reverse in self.encoded], 1)
        raw_parts, logs, masks, anchors, anchor_valid = [], [], [], [], []
        for f, (raw, values, valid, table) in enumerate(self.targets):
            target = table[indices]
            ids = target.clamp_min(0)
            logs.append(values[ids])
            masks.append(valid[ids] & (target >= 0)[..., None])
            if metrics:
                last, checked, reverse = self.anchors[f]
                raw_parts.append(raw[ids])
                anchors.append(last[reverse[indices], None].expand(-1, table.shape[1], -1))
                anchor_valid.append(checked[reverse[indices], None].expand(-1, table.shape[1], -1))
        return windows, torch.cat(logs, 1), torch.cat(masks, 1), (
            (torch.cat(raw_parts, 1), torch.cat(anchors, 1), torch.cat(anchor_valid, 1))
            if metrics else None)


def device_sessions(source, teacher, args, *, metrics):
    """Prefetch frozen encodings on bounded CUDA streams; yield in certified order.

    Only immutable teacher parameters are shared. All gradients, optimizer steps
    and traversal decisions remain on the caller thread in their original order.
    """
    def build(session, verify):
        if not len(session.origins):
            return None
        with torch.no_grad():
            return DeviceSession(session, teacher, args.token_batch, "cuda", cached=not args.direct_teacher,
                                 metrics=metrics, verify=verify)

    lanes = args.session_lanes
    if lanes == 0:
        try:
            for n, (index, session) in enumerate(source):
                yield index, session, build(session, bool(args.profile_sessions and n == 0 and not metrics))
        finally:
            source.close()
        return

    consumer = torch.cuda.current_stream()
    streams = [torch.cuda.Stream() for _ in range(lanes)]
    for stream in streams:
        stream.wait_stream(consumer)

    def prepare_on_stream(stream, session):
        with torch.cuda.stream(stream):
            prepared = build(session, False)
            return prepared, stream.record_event()

    pending = deque()
    def consume(job):
        index, session, future = job
        prepared, ready = future.result()
        consumer.wait_event(ready)
        if prepared is not None:
            # Allocations originate on a worker stream. Prevent recycling their
            # storage before the last queued consumer kernel has read it.
            for entries in (prepared.encoded, prepared.anchors, prepared.targets):
                for entry in entries:
                    for tensor in entry:
                        tensor.record_stream(consumer)
        return index, session, prepared

    try:
        with ThreadPoolExecutor(max_workers=lanes) as executor:
            for n, (index, session) in enumerate(source):
                if n == 0 and args.profile_sessions and not metrics:
                    # The reference check can temporarily change global matmul precision.
                    yield index, session, build(session, True)
                    continue
                ready = consume(pending.popleft()) if len(pending) == lanes else None
                stream = streams[n % lanes]
                pending.append((index, session, executor.submit(prepare_on_stream, stream, session)))
                if ready is not None:
                    yield ready
            while pending:
                yield consume(pending.popleft())
    finally:
        source.close()
