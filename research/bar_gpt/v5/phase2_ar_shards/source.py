"""Canonical-only month pages using the CURRENT phase-one bar aggregation."""

from collections import OrderedDict
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import time
import re

import numpy as np

from .contract import BAR, FRAMES, VERSION
from ..shards import source as sip
from ..shards.balanced_plan import ticker_bounds
from ..shards.contract import digest, file_hash, seed_for, write_json
from ..shards.origin_samples import arrays as unpack
from ..phase2_shards.sql import query as bar_query
from ..phase2_shards.plan import PRODUCERS
from ..quote_rebuild_v1 import session_batches
from ..quote_repair_v1 import require_complete
from ..quote_quality_v1 import POLICY
from ..shards.sql import clock


def partition_page(sql, rows):
    """Redundantly constrain UTC partitions for complete NY source sessions."""
    # This version explicitly adds the already-imported 2026 canonical year.
    SOURCE = re.compile(
        r"SELECT \* FROM market_sip_compact\.events_20(?:19|2[0-6]) PREWHERE ticker="
    )

    first = datetime.fromtimestamp(
        clock(rows[0]["source_date"], 4) // 1_000_000, timezone.utc
    )
    last = datetime.fromtimestamp(
        (clock(rows[-1]["source_date"], 20) - 1) // 1_000_000, timezone.utc
    )
    year, month = first.year, first.month
    months = []
    while (year, month) <= (last.year, last.month):
        months.append(str(year * 100 + month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if not SOURCE.search(sql):
        raise ValueError("Unknown canonical page shape; partition proof unavailable")
    return SOURCE.sub(
        lambda m: m.group().replace(
            "ticker=", f"toYYYYMM(event_date) IN ({','.join(months)}) AND ticker="
        ),
        sql,
    )


def describe(parent, geometry):
    """Pure preview: inspect frozen metadata without creating output or queries."""
    parent = Path(parent)
    inherited = json.loads((parent / "contract.json").read_text())
    if not (parent / "plan/manifest.json").exists():
        raise ValueError(
            "Parent ordinal manifest is not released yet; wait for corrected phase-one completion"
        )
    manifest = json.loads((parent / "plan/manifest.json").read_text())
    if inherited["binding"] != digest(inherited["contract"]):
        raise ValueError("Parent bar contract seal changed")
    if manifest["hash"] != digest({k: v for k, v in manifest.items() if k != "hash"}):
        raise ValueError("Frozen ordinal manifest seal changed")
    if inherited["contract"]["plan_hash"] != manifest["hash"]:
        raise ValueError("Parent ordinal ancestry differs")
    jobs = sorted(manifest["jobs"], key=lambda j: seed_for(VERSION, j["ticker"]))
    base = Path(__file__).resolve().parents[1]
    code = {name: file_hash(base / name) for name in (*PRODUCERS, "shards/storage.py")}
    parent_keys = {
        "bar_semantics_v1.py": "research.bar_gpt.v5.bar_semantics_v1",
        "shards/range_sql.py": "range_sql.py",
        "shards/phase1_contract.py": "phase1_contract.py",
        "shards/origin_samples.py": "origin_samples.py",
        "quote_quality_v1.py": "research.bar_gpt.v5.quote_quality_v1",
        "shards/storage.py": "storage.py",
    }
    if any(
        inherited["contract"]["code"].get(parent_keys[name]) != value
        for name, value in code.items()
    ):
        raise ValueError("Parent uses a different phase-one producer/quote policy")
    code["phase2_shards/sql.py"] = file_hash(base / "phase2_shards/sql.py")
    code["shards/sql.py"] = file_hash(base / "shards/sql.py")
    for path in Path(__file__).parent.glob("*.py"):
        code["phase2_ar_shards/" + path.name] = file_hash(path)
    body = dict(
        version=VERSION,
        parent_binding=inherited["binding"],
        ordinal_hash=manifest["hash"],
        authority=inherited["contract"]["authority"],
        geometry=geometry.manifest(),
        code=code,
        jobs=jobs,
        sampling="3 liquid : 1 less-liquid full4096-origin blocks per split",
        quote_policy=POLICY,
        source_query=inspect.getsource(bar_query),
        storage="shared session-basis pools and per-origin causal indices; original SIP unchanged",
    )
    body["hash"] = digest(body)
    return body, manifest


def freeze(root, parent, geometry, revision, endpoint=None, seed=20261109):
    """Production requires released repaired phase-one data and exact authority."""
    root, parent = Path(root), Path(parent)
    if not (parent / "repair-contract.json").exists():
        raise ValueError("A released corrected phase-one parent is required")
    require_complete(parent)
    body, manifest = describe(parent, geometry)
    if (root / "plan.json").exists():
        prior = json.loads((root / "plan.json").read_text())
        if prior["hash"] != digest({k: v for k, v in prior.items() if k != "hash"}):
            raise ValueError("Frozen sampled plan seal changed")
        if (
            any(
                prior[k] != body[k]
                for k in ("code", "geometry", "parent_binding", "ordinal_hash")
            )
            or prior["seed"] != seed
        ):
            raise ValueError("Frozen sampled contract changed")
        from .inventory import verify_inventory

        verify_inventory(root, prior)
        from .inventory import check_validation_year

        transport = sip.client(endpoint, memory_gib=1)
        try:
            if (
                sip.preflight(transport)
                != {
                    k: v
                    for k, v in body["authority"].items()
                    if k != "corporate_actions"
                }
                or check_validation_year(transport) != prior["validation_authority"]
            ):
                raise ValueError("Canonical source schema/policy changed on resume")
        finally:
            sip.close(transport)
        return prior
    client = sip.client(endpoint, memory_gib=1)
    try:
        authority = sip.preflight(client)
        expected = {
            k: v for k, v in body["authority"].items() if k != "corporate_actions"
        }
        if (
            authority != expected
            or sip.split_authority(client, range(2019, 2026))
            != body["authority"]["corporate_actions"]
        ):
            raise ValueError("Canonical authority changed since phase one")
        from .inventory import extend_plan

        body = extend_plan(root, parent, body, manifest, client, seed)
    finally:
        sip.close(client)
    write_json(
        root / "provenance.json",
        dict(source_revision=revision, parent=str(parent), binding=body["hash"]),
    )
    return body


class CanonicalReader:
    """One worker-owned client; source pages do not define publication boundaries."""

    def __init__(self, root, plan, endpoint=None, memory_gib=3):
        self.root, self.plan = Path(root), plan
        self.client = sip.client(endpoint, memory_gib=memory_gib)
        self.client.default_query_params.update(max_threads=1, max_execution_time=60)
        self.bounds = OrderedDict()

    def months(self, job):
        if job["ticker"] not in self.bounds:
            self.bounds[job["ticker"]] = ticker_bounds(self.root / "inventory", job)
            while len(self.bounds) > 4:
                self.bounds.popitem(last=False)
        grouped = {}
        for row in self.bounds[job["ticker"]]:
            grouped.setdefault(row["source_date"][:7], []).append(row)
        return list(sorted(grouped.items()))

    def pages(self, rows, chunk_events=0):
        """V3 actual path: calendar-month pages; optional measured event bound."""
        grouped = {}
        for row in rows:
            grouped.setdefault(row["source_date"][:7], []).append(row)
        for month in sorted(grouped):
            yield from session_batches(
                grouped[month], max_events=chunk_events or 2**63 - 1, max_days=32
            )

    def fetch(self, ticker, rows, gate, chunk_events=0, frames=tuple(range(5))):
        import polars as pl

        pieces = [[] for _ in FRAMES]
        began, queries = time.monotonic(), 0
        query_seconds = admission_seconds = 0.0
        for page in self.pages(rows, chunk_events):
            for coarse, group in ((False, range(3)), (True, range(3, 5))):
                selected_frames = [f for f in group if f in frames]
                if not selected_frames:
                    continue
                sql = bar_query(ticker, page, self.plan["authority"]["rules"], coarse)
                # Explicit redundant UTC-month pruning only; logical time and
                # ordinal predicates, including cross-UTC-month NY sessions, stay.
                sql = partition_page(sql, page)
                awaiting = time.monotonic()
                with gate():
                    querying = time.monotonic()
                    admission_seconds += querying - awaiting
                    for attempt in range(3):
                        try:
                            table = sip.arrow(
                                self.client,
                                sql,
                                f"ar-month-{digest([ticker, page, coarse])[:24]}-{attempt}",
                                allow_empty=True,
                            )
                            break
                        except (ConnectionError, OSError):
                            if attempt == 2:
                                raise
                            time.sleep(0.25 * 2**attempt)
                    query_seconds += time.monotonic() - querying
                queries += 1
                table = table.with_columns(pl.col("resolution").cast(pl.Int64))
                for frame in selected_frames:
                    selected = table.filter(pl.col("resolution") == FRAMES[frame])
                    a = np.empty(len(selected), dtype=BAR)
                    if len(a):
                        a["values"], a["valid"], a["end_us"] = unpack(selected)
                    pieces[frame].append(a)
        return [
            np.concatenate(parts) if parts else np.empty(0, dtype=BAR)
            for parts in pieces
        ], dict(
            query_seconds=query_seconds,
            admission_wait_seconds=admission_seconds,
            fetch_seconds=time.monotonic() - began,
            queries=queries,
        )

    def close(self):
        sip.close(self.client)
