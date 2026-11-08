"""Read-only phase-two block/window inspection from the workstation authority.

No ClickHouse queries, GPU inference or dataset writes occur here. Discovery is
one cached directory listing; reads touch only selected sealed units/payloads.
The shared reader and encoder-window contract own all indexing/split semantics.
"""

import json
import os
from pathlib import Path
import random
import re
import time

import numpy as np

from ..shard_inspection import singleflight_cache
from .contract import (
    CHANNELS,
    FRAMES,
    SPECS,
    VERSION,
    VIEW_VERSION,
    digest,
    encoder_window,
    windows,
)
from .storage import block_segments, load_array, read_raw_window
from ..shards.contract import file_hash
from ..shards.sql import clock

ROOT = Path(
    r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\bar_gpt\phase2-streams-v1"
)


@singleflight_cache(maxsize=2)
def _plan(root):
    """Load and verify the immutable plan once; never cache mutable progress."""
    plan = json.loads((Path(root) / "plan.json").read_text())
    if plan["version"] != VERSION or plan["hash"] != digest(
        {k: v for k, v in plan.items() if k != "hash"}
    ):
        raise ValueError("Phase-two plan is unsupported or its hash is invalid")
    return plan, {row["key"]: row for row in plan["streams"]}


@singleflight_cache(maxsize=2)
def _available(root, bucket):
    """Discover published stream directories without a recursive corpus scan."""
    with os.scandir(Path(root) / "streams") as entries:
        return {
            entry.name
            for entry in entries
            if entry.is_dir() and re.fullmatch(r"[a-f0-9]{24}", entry.name)
        }


def catalog(search="", year=0, offset=0, liquid=-1):
    """Return a bounded page of materialized ticker-years and live progress."""
    plan, streams = _plan(str(ROOT))
    available = _available(str(ROOT), int(time.time() // 30))
    rows = [
        dict(
            stream=key,
            ticker=row["ticker"],
            year=row["year"],
            liquid=bool(row["liquid"]),
        )
        for key, row in streams.items()
        if key in available
        and search.upper() in row["ticker"].upper()
        and (not year or row["year"] == year)
        and (liquid == -1 or bool(row["liquid"]) == bool(liquid))
    ]
    rows.sort(key=lambda row: (row["ticker"], row["year"]))
    progress = json.loads((ROOT / "progress.json").read_text())
    return dict(
        streams=rows[offset : offset + 100],
        total=len(rows),
        years=sorted({row["year"] for row in streams.values()}),
        frames=list(FRAMES),
        plan_hash=plan["hash"],
        view_version=VIEW_VERSION,
        progress={
            key: progress.get(key) for key in ("state", "bytes", "units", "updated")
        },
    )


def _stream(key):
    """Accept frozen stream IDs only; user-supplied filesystem paths are forbidden."""
    if not re.fullmatch(r"[a-f0-9]{24}", key):
        raise ValueError("Invalid phase-two stream ID")
    plan, streams = _plan(str(ROOT))
    if key not in streams:
        raise ValueError("Stream is outside the frozen phase-two plan")
    return plan, streams[key]


@singleflight_cache(maxsize=4096)
def _receipt(path, size, modified):
    """Validate receipt seal and safe payload names before opening any payload."""
    receipt = json.loads(Path(path).read_text())
    if receipt["hash"] != digest({k: v for k, v in receipt.items() if k != "hash"}):
        raise ValueError("Phase-two receipt hash mismatch")
    files = {item["name"]: item for item in receipt["files"]}
    for name in files:
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Unsafe receipt payload name")
    for item in [*receipt["segments"], *block_segments(receipt)]:
        if item["name"] not in files or item["sha256"] != files[item["name"]]["sha256"]:
            raise ValueError("Logical array is not bound to the sealed payload")
    return receipt


@singleflight_cache(maxsize=8)
def _receipts(root, key, bucket):
    """Cache metadata for at most eight selected streams, refreshed every 30s."""
    plan, identity = _stream(key)
    rows = []
    for path in sorted((Path(root) / "streams" / key).glob("*/receipt.json")):
        if not re.fullmatch(r"\d{4,8}", path.parent.name):
            continue  # Unsealed .partial directories are never visible.
        stat = path.stat()
        receipt = _receipt(str(path), stat.st_size, stat.st_mtime_ns)
        if (
            receipt["stream"] != key
            or receipt["ticker"] != identity["ticker"]
            or receipt["timing"]["plan"] != plan["hash"]
        ):
            raise ValueError("Receipt belongs to a different stream or plan")
        rows.append((path.parent, receipt))
    return rows


def units(stream):
    """List sealed source units with full 256-decision blocks for one stream."""
    _, identity = _stream(stream)
    rows = _receipts(str(ROOT), stream, int(time.time() // 30))
    return dict(
        ticker=identity["ticker"],
        units=[
            dict(
                unit=receipt["unit"],
                blocks=receipt["blocks"],
                begin=receipt["timing"]["begin"],
                end=receipt["timing"]["end"],
                storage=receipt.get("storage", "legacy"),
            )
            for _, receipt in rows
            if receipt["blocks"]
        ],
    )


@singleflight_cache(maxsize=128)
def _verified(path, size, modified, expected):
    """Hash each unchanged payload once; a later mutation invalidates this key."""
    if file_hash(Path(path)) != expected:
        raise ValueError("Phase-two payload SHA-256 mismatch")


def _verify(path, descriptor):
    stat = path.stat()
    _verified(str(path), stat.st_size, stat.st_mtime_ns, descriptor["sha256"])


def sample(
    stream="",
    unit=-1,
    block=0,
    decision=255,
    frame=0,
    view=-1,
    randomize=False,
    search="",
    year=0,
    liquid=-1,
):
    """Inspect a single BAR[64] view at one exact completed 1s decision close.

    Return raw/adjusted [64,18] fields, masks, nine shared UTC fields, block clocks,
    all 38 view availabilities and immutable receipt provenance. No embeddings
    are calculated. Random selection is a bounded stream/unit/block draw, not a
    uniform sample of the training corpus. Split IDs describe cutoff eligibility,
    not admission into a balanced selected-block release.
    """
    if frame not in range(5) or decision not in range(256) or block < 0:
        raise ValueError("Invalid frame, decision or block")
    if not stream:
        plan, streams = _plan(str(ROOT))
        candidates = list(_available(str(ROOT), int(time.time() // 30)))
        candidates = [
            key
            for key in candidates
            if key in streams
            and search.upper() in streams[key]["ticker"].upper()
            and (not year or streams[key]["year"] == year)
            and (liquid == -1 or bool(streams[key]["liquid"]) == bool(liquid))
        ]
        random.shuffle(candidates)
        for key in candidates[:50]:
            if units(key)["units"]:
                stream = key
                break
        if not stream:
            raise ValueError(
                "No matching sealed full blocks found; choose another ticker/year"
            )
        randomize = True
    plan, identity = _stream(stream)
    receipts = _receipts(str(ROOT), stream, int(time.time() // 30))
    choices = [
        (directory, row)
        for directory, row in receipts
        if row["blocks"] and (unit < 0 or row["unit"] == unit)
    ]
    if not choices:
        raise ValueError("No sealed full blocks in the selected unit")
    directory, receipt = random.choice(choices) if randomize else choices[0]
    if randomize:
        block = random.randrange(receipt["blocks"])
    if block >= receipt["blocks"]:
        raise ValueError("Block is outside the selected unit")
    remaining, data = block, None
    for descriptor in block_segments(receipt):
        if remaining < descriptor["rows"]:
            _verify(directory / descriptor["name"], descriptor)
            data = load_array(directory, descriptor)[remaining]
            break
        remaining -= descriptor["rows"]
    if data is None:
        raise ValueError("Missing sealed decision block")
    origin = int(data["end_us"][decision])
    if origin <= 0 or origin % FRAMES[0]:
        raise ValueError("Origin is not a completed 1s bar close")
    # Verify that the origin references precisely this last observed 1s close.
    reference = read_raw_window(
        ROOT,
        stream,
        int(data["latest"][decision, 0]),
        0,
        receipts=receipts,
        verify_payload=_verify,
    )
    last_close = int(reference["end_us"][-1]) if reference is not None else None
    if reference is None:
        latest = int(data["latest"][decision, 0])
        for path, entry in receipts:
            for item in entry["segments"]:
                if (
                    item["frame"] == 0
                    and item["start"] <= latest < item["start"] + item["rows"]
                ):
                    _verify(path / item["name"], item)
                    last_close = int(
                        load_array(path, item)[latest - item["start"]]["end_us"]
                    )
    if last_close != origin:
        raise ValueError("Decision origin differs from the referenced 1s bar close")
    summaries = []
    for i, duration in enumerate(FRAMES):
        indices, present = windows(np.array([data["latest"][decision, i]]), i)
        summaries.append(
            dict(
                frame=i,
                timeframe=duration,
                context_bars=SPECS[i][1],
                views=[
                    dict(view=j, available=bool(ok), last_index=int(index[-1]))
                    for j, (index, ok) in enumerate(zip(indices[0], present[0]))
                ],
            )
        )
    available = [row for row in summaries[frame]["views"] if row["available"]]
    selected = (
        available[-1]
        if view < 0 and available
        else next((row for row in available if row["view"] == view), None)
    )
    bars, time_channels = [], []
    actions = [
        row
        for row in plan["actions"]["rows"]
        if row["provider_ticker"] == identity["ticker"]
    ]
    if selected is not None:
        raw = (
            reference
            if frame == 0 and selected["last_index"] == int(data["latest"][decision, 0])
            else read_raw_window(
                ROOT,
                stream,
                selected["last_index"],
                frame,
                receipts=receipts,
                verify_payload=_verify,
            )
        )
        adjusted = encoder_window(raw, FRAMES[frame], actions, origin_us=origin)
        # CPU-only shared transformation; inspect UTC channels before market scaling.
        import torch
        from ..transforms_v1 import BarInputTransformV1, TIME_CHANNELS

        transformed = BarInputTransformV1()(
            torch.from_numpy(adjusted["values"].copy()),
            torch.from_numpy(adjusted["valid"].copy()),
            torch.from_numpy(adjusted["end_us"].copy()),
            FRAMES[frame],
        )
        time_channels = list(TIME_CHANNELS)
        for i in range(64):
            valid = raw["valid"][i].astype(bool)
            bars.append(
                dict(
                    index=i,
                    end_us=int(raw["end_us"][i]),
                    padding=False,
                    gap_us=int(raw["end_us"][i] - raw["end_us"][i - 1]) if i else None,
                    raw=raw["values"][i].tolist(),
                    valid=valid.tolist(),
                    values=[
                        float(value) if ok else None
                        for value, ok in zip(adjusted["values"][0, i], valid)
                    ],
                    time_values=transformed.values[0, i, 18:].tolist(),
                )
            )
    return dict(
        stream=stream,
        ticker=identity["ticker"],
        year=identity["year"],
        liquid=bool(identity["liquid"]),
        unit=receipt["unit"],
        block=block,
        block_count=receipt["blocks"],
        decision=decision,
        decisions=[int(value) for value in data["end_us"]],
        origin_us=origin,
        frame=frame,
        view=selected["view"] if selected else None,
        summaries=summaries,
        channels=list(CHANNELS),
        time_channels=time_channels,
        bars=bars,
        split=int(receipt["splits"][block]),
        actions=[
            dict(
                row,
                effective_us=clock(row["execution_date"], 4),
                effective_by_origin=clock(row["execution_date"], 4) <= origin,
            )
            for row in actions
        ],
        receipt_hash=receipt["hash"],
        plan_hash=plan["hash"],
        producer_revision=receipt["timing"].get("producer_revision"),
        view_version=VIEW_VERSION,
    )
