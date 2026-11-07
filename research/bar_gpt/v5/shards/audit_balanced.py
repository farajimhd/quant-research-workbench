"""Audit prepared v5 payloads and independently reconstruct canonical bar fields.

The structural audit reads every sealed pilot sample. The source oracle decodes
canonical events directly, applies dictionary flags independently in Python, and
compares selected real bars. It does not call production aggregation SQL.
"""

import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from . import source
from .balanced_plan import VERSION, ticker_bounds
from .phase1_contract import FRAMES, SAMPLE_DTYPE, digest, write_json
from .range_sql import event_source
from .sql import clock
from .storage import verify_receipt

NY = ZoneInfo("America/New_York")


def reference(c, ticker, bounds, rules, end, resolution, sample_end, actions):
    """Return independent values/masks [18] for one selected completed bar.

    Raw diagnostic reads are bounded to one bar plus its preceding quote. Dense
    daily/hourly bars are audited on the explicit low-activity oracle ticker.
    """
    day = datetime.fromtimestamp(end / 1e6, NY).date().isoformat()
    start = (
        clock(day, 9) + 1800_000_000 if resolution == FRAMES[-1] else end - resolution
    )
    rows = [
        r
        for r in bounds
        if int(r["last_sip_timestamp_us"]) >= clock(day, 4)
        and int(r["first_sip_timestamp_us"]) < end
    ]
    if not rows:
        raise ValueError("Oracle bar has no frozen source coverage")
    raw = event_source(
        ticker,
        rows,
        min(r["first_ordinal"] for r in rows),
        max(r["next_ordinal"] for r in rows),
    )
    columns = (
        "ordinal,sip_timestamp_us,event_meta,price_primary_int,price_secondary_int,size_primary,size_secondary,"
        + ",".join(f"condition_token_{i}" for i in range(1, 6))
    )
    selected = source.rows(
        c,
        f"SELECT {columns} FROM ({raw}) WHERE sip_timestamp_us>={start} AND sip_timestamp_us<{end} ORDER BY sip_timestamp_us,ordinal",
    )
    previous = source.rows(
        c,
        f"SELECT {columns} FROM ({raw}) WHERE sip_timestamp_us>={clock(day,4)} AND sip_timestamp_us<{start} AND bitAnd(event_meta,1)=0 ORDER BY sip_timestamp_us DESC,ordinal DESC LIMIT 1",
    )
    trade_rules = [r for r in rules if r["source_family"] == "trade_conditions"]
    known = {int(r["token_id"]) for r in trade_rules}
    form = {int(r["token_id"]) for r in trade_rules if int(r["modifier_int"]) == 12}
    allowed = [
        {int(r["token_id"]) for r in trade_rules if r[key] == 1}
        for key in ("update_last", "update_high_low", "update_volume")
    ]
    trades = [[], [], []]
    quotes = []
    for e in selected:
        primary = e["price_primary_int"] * (1 if e["event_meta"] & 2 else 100) / 10000.0
        if e["event_meta"] & 1:
            usable = (
                primary > 0
                and e["size_primary"] > 0
                and math.isfinite(e["size_primary"])
            )
            tokens = {e[f"condition_token_{i}"] for i in range(1, 6)} - {0}
            local = datetime.fromtimestamp(int(e["sip_timestamp_us"]) / 1e6, NY)
            sec = local.hour * 3600 + local.minute * 60 + local.second
            form_ok = (
                (sec < 34200 or sec >= 57600)
                and bool(tokens & form)
                and all(
                    t in form or (t in allowed[0] and t in allowed[1]) for t in tokens
                )
            )
            for category, accepted in enumerate(allowed):
                if usable and all(
                    t in known and (t in accepted or (form_ok and t in form))
                    for t in tokens
                ):
                    trades[category].append((primary, e["size_primary"]))
        else:
            bid = (
                e["price_secondary_int"] * (1 if e["event_meta"] & 4 else 100) / 10000.0
            )
            good = (
                bid > 0
                and primary >= bid
                and all(
                    math.isfinite(e[k]) and e[k] >= 0
                    for k in ("size_primary", "size_secondary")
                )
            )
            quotes.append((good, bid, primary, e["size_secondary"], e["size_primary"]))
    value = np.zeros(18, dtype=np.float32)
    valid = np.zeros(18, dtype=np.uint8)
    assert all(trades), "Published bar has no complete independent trade evidence"
    value[:6] = [
        trades[0][0][0],
        max(p for p, s in trades[1]),
        min(p for p, s in trades[1]),
        trades[0][-1][0],
        sum(s for p, s in trades[2]),
        len(trades[2]),
    ]
    valid[:6] = 1
    if quotes:
        book = quotes[-1]
    elif previous:
        e = previous[0]
        ask = e["price_primary_int"] * (1 if e["event_meta"] & 2 else 100) / 10000.0
        bid = e["price_secondary_int"] * (1 if e["event_meta"] & 4 else 100) / 10000.0
        good = (
            bid > 0
            and ask >= bid
            and all(
                math.isfinite(e[k]) and e[k] >= 0
                for k in ("size_primary", "size_secondary")
            )
        )
        book = (good, bid, ask, e["size_secondary"], e["size_primary"])
    else:
        book = (False, 0, 0, 0, 0)
    for off, p, s in ((6, 1, 3), (12, 2, 4)):
        observed = [q[p] for q in quotes if q[0]]
        if observed:
            value[off : off + 3] = [observed[0], max(observed), min(observed)]
        elif book[0]:
            value[off : off + 3] = book[p]
        valid[off : off + 3] = int(bool(observed) or book[0])
        if book[0]:
            value[off + 3 : off + 5] = [book[p], book[s]]
        valid[off + 3 : off + 5] = int(book[0])
        value[off + 5] = len(observed)
        valid[off + 5] = 1
    factor = 1.0
    for action in actions:
        if end < clock(action["execution_date"], 4) <= sample_end:
            factor *= float(action["split_from"]) / float(action["split_to"])
    value[[0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15]] *= factor
    value[[4, 10, 16]] /= factor
    return value, valid


def audit(root, output, oracle_ticker="SLVO"):
    """Read all pilot records, reject duplicates, and check each frame at source."""
    root = Path(root)
    wrapper = json.loads((root / "contract.json").read_text())
    assert (
        wrapper["contract"]["version"] == VERSION
        and digest(wrapper["contract"]) == wrapper["binding"]
    )
    plan = json.loads((root / "plan/manifest.json").read_text())
    frames = Counter()
    cohorts = {str(f): [0, 0] for f in FRAMES}
    seen = set()
    references = {}
    receipts = 0
    for path in sorted(root.glob("jobs/*/*/receipt.json")):
        receipt = verify_receipt(path.parent, hashes=True)
        assert receipt["binding"] == wrapper["binding"]
        receipts += 1
        for file in receipt["files"]:
            data = np.load(
                path.parent / file["name"], mmap_mode="r", allow_pickle=False
            )
            assert data.dtype == SAMPLE_DTYPE and (data["end_us"] > 0).all()
            assert (np.diff(data["end_us"], axis=1) > 0).all()
            assert data["valid"][..., :6].all() and (data["values"][..., :6] > 0).all()
            assert np.isfinite(data["values"]).all()
            cutoff = 1759291200000000  # 2025-10-01 04:00 UTC, the shared development boundary.
            assert (
                (data["end_us"][:, -1] < cutoff)
                | (data["end_us"][:, 0] - data["timeframe_us"] >= cutoff)
            ).all()
            for record in data:
                frame = int(record["timeframe_us"])
                identity = (
                    receipt["ticker"],
                    frame,
                    int(record["end_us"][0]),
                    int(record["end_us"][-1]),
                )
                assert identity not in seen, f"Duplicate saved context: {identity}"
                seen.add(identity)
                frames[str(frame)] += 1
                cohorts[str(frame)][receipt["liquid"]] += 1
                if receipt["ticker"] == oracle_ticker:
                    references.setdefault(frame, record.copy())
                    # Prefer a real split-crossing context when one is present.
                    if (
                        int(record["end_us"][0])
                        < clock("2022-09-27", 4)
                        <= int(record["end_us"][-1])
                    ):
                        references[frame] = record.copy()
            data._mmap.close()
    c = source.client(memory_gib=2)
    compared = 0
    try:
        job = next(j for j in plan["jobs"] if j["ticker"] == oracle_ticker)
        bounds = ticker_bounds(root, job)
        actions = [
            r
            for r in wrapper["contract"]["authority"]["corporate_actions"]["rows"]
            if r["provider_ticker"] == oracle_ticker
        ]
        for frame, record in references.items():
            for position in (0, 31, 63):
                value, valid = reference(
                    c,
                    oracle_ticker,
                    bounds,
                    wrapper["contract"]["authority"]["rules"],
                    int(record["end_us"][position]),
                    frame,
                    int(record["end_us"][-1]),
                    actions,
                )
                np.testing.assert_array_equal(record["values"][position, :18], value)
                np.testing.assert_array_equal(record["valid"][position, :18], valid)
                compared += 18
    finally:
        source.close(c)
    result = dict(
        passed=True,
        samples=sum(frames.values()),
        receipts=receipts,
        frames=dict(frames),
        cohorts=cohorts,
        source_values_compared=compared,
        source_masks_compared=compared,
        oracle_ticker=oracle_ticker,
        limitations=[
            "Pilot audit; not certification of all future campaign outputs",
            "Phase-one market-only contract; halt fields are intentionally absent",
        ],
    )
    write_json(Path(output), result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.dataset, args.output), indent=2))
