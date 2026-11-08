"""Freeze the entire source inventory once, independent of execution capacity."""

import json
from functools import lru_cache
from pathlib import Path
import shutil

from .contract import CAPACITY, CUTOFF, SPECS, BLOCK, VERSION
from ..shards.contract import digest, file_hash, seed_for, write_json
from ..shards import source

# A changed aggregate expression requires an explicit new contract, not an
# unrecorded fallback to another bar implementation.
PRODUCERS = ("bar_semantics_v1.py", "shards/range_sql.py", "shards/phase1_contract.py")


def freeze(root, parent, revision, endpoint=None):
    """Pin phase-one provenance and all 2019–2025 ticker-year streams.

    ``root``/``parent`` are runtime directories. The immutable plan contains a
    four-TB capacity, stable stream order and complete ordinal-summary snapshot.
    Raising the mutable two-TB execution limit never changes these identities.
    """
    root, parent = Path(root), Path(parent)
    root.mkdir(parents=True, exist_ok=True)
    final = root / "plan.json"
    if final.exists():
        plan = json.loads(final.read_text())
        if plan["hash"] != digest({k: v for k, v in plan.items() if k != "hash"}):
            raise ValueError("Phase-two plan seal mismatch")
        verify_code(plan)
        return plan
    inherited = json.loads((parent / "contract.json").read_text())
    manifest = json.loads((parent / "plan/manifest.json").read_text())
    transport = source.client(endpoint)
    try:
        authority = source.preflight(transport)
        actions = source.split_authority(transport, range(2019, 2026))
    finally:
        source.close(transport)
    contract = inherited["contract"]
    parent_authority = {
        k: v for k, v in contract["authority"].items() if k != "corporate_actions"
    }
    if (
        authority != parent_authority
        or actions != contract["authority"]["corporate_actions"]
    ):
        raise ValueError("Canonical bar authority differs from phase one")
    # The existing source snapshot is immutable. Copy small summary partitions,
    # not event data, and verify every copy before any producer is admitted.
    inventory = root / "inventory/plan"
    inventory.mkdir(parents=True, exist_ok=True)
    for item in manifest["partitions"]:
        original, target = parent / "plan" / item["name"], inventory / item["name"]
        if not target.exists():
            shutil.copyfile(original, target)
        if file_hash(target) != item["sha256"]:
            raise ValueError("Ordinal snapshot hash mismatch")
    write_json(inventory / "manifest.json", manifest)
    streams = []
    for job in manifest["jobs"]:
        for year in job["years"]:
            streams.append(
                dict(
                    ticker=job["ticker"],
                    years=[year],
                    key=digest((job["ticker"], year["year"]))[:24],
                    liquid=int(year["liquid"]),
                    year=year["year"],
                )
            )
    streams.sort(key=lambda s: seed_for(VERSION, s["key"]))
    base = Path(__file__).resolve().parents[1]
    code = {name: file_hash(base / name) for name in PRODUCERS}
    # Parent code keys can include repository prefixes; compare by suffix.
    parent_code = contract["code"]
    parent_keys = {
        "bar_semantics_v1.py": "research.bar_gpt.v5.bar_semantics_v1",
        "shards/range_sql.py": "range_sql.py",
        "shards/phase1_contract.py": "phase1_contract.py",
    }
    for name, actual in code.items():
        if parent_code.get(parent_keys[name]) != actual:
            raise ValueError(f"Phase-one producer drift: {name}")
    plan = dict(
        version=VERSION,
        capacity_bytes=CAPACITY,
        parent_binding=inherited["binding"],
        ordinal_hash=manifest["hash"],
        authority=authority,
        actions=actions,
        code=code,
        revision=revision,
        streams=streams,
        specs=SPECS,
        block_decisions=BLOCK,
        cutoff_us=CUTOFF,
        range_calendar_days=14,
        sampling="3 liquid : 1 less-liquid accepted full blocks, per split",
        storage="raw unadjusted bars + causal indices; no GPU embeddings",
    )
    plan["hash"] = digest(plan)
    write_json(final, plan)
    return plan


def verify_code(plan):
    """Reject changes to shared market math even when resuming an old plan."""
    base = Path(__file__).resolve().parents[1]
    for name, expected in plan["code"].items():
        if file_hash(base / name) != expected:
            raise ValueError(f"Pinned bar producer changed: {name}")


def bounds(root, plan, stream):
    """Read a ticker's full frozen history for one-time warmup and core ranges."""
    from ..shards.balanced_plan import ticker_bounds

    jobs = inventory_jobs(str(Path(root).resolve()))
    job = jobs[stream["ticker"]]
    return ticker_bounds(Path(root) / "inventory", job)


@lru_cache(maxsize=2)
def inventory_jobs(root):
    """Cache immutable small metadata once; never reparse 40 MB per subdivision.

    The root's sealed plan pins this inventory. Only two roots can remain
    resident, bounding memory even when profiling and production share a process.
    """
    manifest = json.loads((Path(root) / "inventory/plan/manifest.json").read_text())
    return {job["ticker"]: job for job in manifest["jobs"]}


def ranges(rows, year):
    """Group ordered source sessions into disjoint fourteen-calendar-day units."""
    from datetime import date

    origin = date(year, 1, 1)
    grouped = {}
    for row in rows:
        if int(row["source_date"][:4]) == year:
            key = (date.fromisoformat(row["source_date"]) - origin).days // 14
            grouped.setdefault(key, []).append(row)
    return list(grouped.values())
