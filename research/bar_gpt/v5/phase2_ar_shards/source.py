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


def verify_engineering_revision(prior, current, proof_path, root=None):
    """Admit only a sealed, audited physical short-session preflight revision."""
    from ..quote_repair_v1 import verify_seal

    proof = json.loads(Path(proof_path).read_text())
    if "hash" not in proof:
        raise ValueError("Missing engineering proof seal")
    verify_seal(proof, "hash")
    changed = {k for k in set(prior["code"]) | set(current["code"])
               if prior["code"].get(k) != current["code"].get(k)}
    allowed = {"phase2_ar_shards/" + k for k in ("source.py", "sampling.py", "run.py")}
    kind = proof.get("kind")
    if kind in ('overlap-pending-ranks-v1', 'overlap-fair-publication-v1'):
        allowed = {'phase2_ar_shards/source.py', 'phase2_ar_shards/overlap.py'}
        if kind == 'overlap-fair-publication-v1':
            allowed.add('phase2_ar_shards/run.py')
    if kind=='adaptive-query-memory-v1':
        allowed={'phase2_ar_shards/'+name for name in ('source.py','run.py','telemetry.py','query_memory.py')}
    if kind=='source-eligibility-cache-v1':
        allowed={'phase2_ar_shards/'+name for name in ('source.py','run.py','telemetry.py','query_memory.py','eligibility_cache.py')}
    ledger_kinds = ("file-ledger-eligibility-v1", "file-ledger-observability-v1", "ticker-affinity-context-v1", "coordinator-capacity-v1")
    if kind in ledger_kinds:
        allowed |= {"phase2_ar_shards/"+k for k in ("storage.py","ledger.py","migrate_ledger.py")}
    if kind in ("file-ledger-observability-v1", "ticker-affinity-context-v1", "coordinator-capacity-v1"):
        allowed.add("phase2_ar_shards/telemetry.py")
    if kind in ("ticker-affinity-context-v1", "coordinator-capacity-v1"):
        allowed |= {"phase2_ar_shards/"+k for k in ("assignment.py", "bar_cache.py")}
    if (not changed or not changed <= allowed
        or kind not in ("exact-short-session-preflight-v1", 'adaptive-query-memory-v1', 'source-eligibility-cache-v1', 'overlap-pending-ranks-v1', 'overlap-fair-publication-v1', *ledger_kinds)
        or proof.get("plan_hash") != prior["hash"]
        or proof.get("from_code") != prior["code"]
        or proof.get("to_code") != current["code"]):
        raise ValueError("Uncertified engineering revision")
    audit_path = Path(proof["audit_path"])
    audit = json.loads(audit_path.read_text())
    if "hash" not in audit:
        raise ValueError("Missing native audit seal")
    verify_seal(audit, "hash")
    if (file_hash(audit_path) != proof["audit_sha256"]
        or audit.get("passed") is not True
        or audit.get("plan_hash") != prior["hash"]
        or audit.get("to_code") != current["code"]
        or audit.get("counts_parity_cases", 0) < 12
        or audit.get("complete_output_parity_cases", 0) < 2):
        raise ValueError("Engineering revision lacks exact native parity evidence")
    if kind in ('overlap-pending-ranks-v1', 'overlap-fair-publication-v1') and not all(audit.get(key) is True for key in (
        'failed_session_exact_context_verified', 'pending_rank_regressions_passed',
        'published_outputs_unchanged', 'parent_receipts_unchanged')):
        raise ValueError('Overlap recovery lacks rank and immutable-output evidence')
    if kind == 'overlap-fair-publication-v1' and audit.get('bounded_lane_fifo_tests_passed') is not True:
        raise ValueError('Fair publication lacks bounded FIFO proof')
    if kind=='adaptive-query-memory-v1' and not all(audit.get(key) is True for key in (
        'weighted_admission_tests_passed','client_limits_restored','identical_sql_retry_verified',
        'unchanged_payload_selection_and_cache_binding','training_guards_preserved')):
        raise ValueError('Adaptive query budgets lack bounded same-contract proof')
    if kind=='source-eligibility-cache-v1' and not all(audit.get(key) is True for key in (
        'exact_eligibility_counts_verified','missing_metadata_fails_open',
        'source_binding_tests_passed','unchanged_payload_and_history')):
        raise ValueError('Eligibility cache lacks exact source-bound query-elision proof')
    if kind == "file-ledger-observability-v1" and (
        audit.get("observer_payload_parity_cases", 0) < 2
        or audit.get("worker_status_tests_passed") is not True
        or audit.get("exact_sampling_and_canonical_reader") is not True
    ):
        raise ValueError("Worker observability lacks native payload parity evidence")
    if kind in ("ticker-affinity-context-v1", "coordinator-capacity-v1") and (
        audit.get("session_cache_parity_cases", 0) < 12
        or audit.get("fixed_assignment_tests_passed") is not True
        or audit.get("uncached_aggregation_exact") is not True
        or audit.get("existing_selection_preserved") is not True
    ):
        raise ValueError("Ticker ownership lacks source/cache/assignment parity evidence")
    if kind == "coordinator-capacity-v1" and (
        audit.get("queued_deferral_regression_passed") is not True
        or audit.get("separate_capacity_tests_passed") is not True
        or audit.get("direct_training_guard_tests_passed") is not True
        or audit.get("unchanged_all_data_producers") is not True
    ):
        raise ValueError("Coordinator recovery lacks bounded same-contract evidence")
    if kind in ledger_kinds:
        from .ledger import FileLedger

        path = Path(proof["migration_path"])
        if root is None or path.resolve() != (Path(root)/"ledger-file-v1/migration.json").resolve():
            raise ValueError("Migration proof belongs to another runtime root")
        migration = json.loads(path.read_text());verify_seal(migration,"hash")
        ledger = FileLedger(root)
        if (file_hash(path)!=proof["migration_sha256"] or not migration.get("passed")
            or migration.get("plan_hash")!=prior["hash"] or not migration.get("read_only_legacy")
            or not migration.get("exact_tables") or not migration.get("selected_order_exact")
            or digest(ledger.manifest["checkpoint"])!=migration.get("checkpoint_hash")
            or audit.get("file_ledger_tests_passed") is not True
            or audit.get("no_sqlite_loader_verified") is not True):
            raise ValueError("File ledger migration parity proof is incomplete")


def freeze(root, parent, geometry, revision, endpoint=None, seed=20261109,
           engineering_proof=None):
    """Production requires released repaired phase-one data and exact authority."""
    root, parent = Path(root), Path(parent)
    if not (parent / "repair-contract.json").exists():
        raise ValueError("A released corrected phase-one parent is required")
    require_complete(parent)
    body, manifest = describe(parent, geometry)
    # JSON seals normalize NumPy dtype descriptor tuples to lists. Compare the
    # same representation on restart; actual fields/widths remain exact.
    body["geometry"] = json.loads(json.dumps(body["geometry"]))
    if (root / "plan.json").exists():
        prior = json.loads((root / "plan.json").read_text())
        if prior["hash"] != digest({k: v for k, v in prior.items() if k != "hash"}):
            raise ValueError("Frozen sampled plan seal changed")
        if prior["code"] != body["code"]:
            if engineering_proof is None:
                raise ValueError("Frozen sampled code changed without engineering proof")
            verify_engineering_revision(prior, body, engineering_proof, root=root)
        if (
            any(
                prior[k] != body[k]
                for k in ("geometry", "parent_binding", "ordinal_hash")
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
        from .inventory import verify_parent_actions

        verify_parent_actions(client, body["authority"]["corporate_actions"])
        if authority != expected:
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

    query_memory_gib=1.0
    query_max_memory_gib=4.0

    def __init__(self, root, plan, endpoint=None, memory_gib=1, max_memory_gib=4, eligibility_cache=None):
        self.root, self.plan = Path(root), plan
        from .query_memory import tiers
        tiers(memory_gib,max_memory_gib)
        self.query_memory_gib,self.query_max_memory_gib=memory_gib,max_memory_gib
        self.client = sip.client(endpoint, memory_gib=memory_gib)
        self.client.default_query_params.update(max_threads=1, max_execution_time=60)
        self.bounds = OrderedDict()
        from .eligibility_cache import EligibilityCache
        self.eligibility_cache=EligibilityCache(eligibility_cache,plan) if eligibility_cache else None
        from .coarse_cache import CoarseCache

        self.coarse_cache = CoarseCache(self.root.parent / "phase2-coarse-cache-v1", digest(plan["authority"]))
        from .bar_cache import BarCache
        producer = {k:v for k,v in plan["code"].items()
                    if not k.startswith("phase2_ar_shards/")}
        self.context_cache = BarCache(
            self.root.parent / "phase2-session-context-v2",
            digest(dict(authority=plan["authority"], producer=producer,
                        source_query=plan["source_query"])),
            fine_tail_rows=max(plan["geometry"]["history"][:3]),
        )

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
        """Month-aligned, whole-session pages; bound dense pages after live profiling."""
        grouped = {}
        for row in rows:
            grouped.setdefault(row["source_date"][:7], []).append(row)
        for month in sorted(grouped):
            yield from session_batches(
                grouped[month], max_events=chunk_events or 8_000_000, max_days=32
            )

    def origin_counts(self, ticker, rows, gate):
        """Filter exact known-short/empty sessions BEFORE querying; keep all counts."""
        began=time.monotonic();counts={};pending=[];resolved=[]
        costs=dict(queries=0,query_seconds=0.,admission_wait_seconds=0.,
                   cached_eligibility_sessions=0,inventory_empty_sessions=0,eligibility_unknown_sessions=0)
        def flush():
            if pending:
                found,timing=self._origin_counts_uncached(ticker,pending,gate)
                counts.update(found)
                for k,v in timing.items():costs[k]=costs.get(k,0)+v
                pending.clear()
        for row in rows:
            opened,closed=clock(row['source_date'],4),clock(row['source_date'],20)
            first,last=row.get('first_sip_timestamp_us'),row.get('last_sip_timestamp_us')
            empty=(row.get('event_count')==0 or (first is not None and last is not None
                   and first<=last and (last<opened or first>=closed)))
            cache=getattr(self,'eligibility_cache',None)
            n=0 if empty else cache.get(ticker,row) if cache else None
            resolved.append((row,n,empty))
        # Do not replace one small monthly query with many tiny queries when
        # partial cache coverage has holes. Query the original page once and
        # cross-check known counts instead; complete/prefix hits still prune.
        runs=sum(n is None and (i==0 or resolved[i-1][1] is not None)
                 for i,(_,n,_) in enumerate(resolved))
        if runs>1:
            found,timing=self._origin_counts_uncached(ticker,rows,gate)
            for row,n,_ in resolved:
                if n is not None and found[row['source_date']]!=n:
                    raise ValueError('Canonical count differs from sealed eligibility evidence')
            timing.update(eligibility_cache_fragment_fallbacks=1,fetch_seconds=time.monotonic()-began)
            return found,timing
        for row,n,empty in resolved:
            if n is None:
                pending.append(row);costs['eligibility_unknown_sessions']+=1
            else:
                flush();counts[row['source_date']]=n
                costs['inventory_empty_sessions' if empty else 'cached_eligibility_sessions']+=1
        flush()
        costs['fetch_seconds']=time.monotonic()-began
        return counts,costs

    def _origin_counts_uncached(self, ticker, rows, gate):
        """Exact completed trade-bearing 1s counts, without quote reconstruction.

        Use the unchanged producer's decoded predicates and the SAME three
        independent trade eligibility flags. Different trades can supply last,
        extremes and volume within one second. This is offline eligibility only.
        """
        sql = partition_page(bar_query(ticker, rows, self.plan["authority"]["rules"]), rows)
        marker = "    base AS (SELECT "
        if sql.count(marker) != 1:
            raise ValueError("Unknown producer shape for exact session eligibility")
        prefix = sql.split(marker)[0].rstrip()
        if not prefix.endswith(","):
            raise ValueError("Missing decoded producer boundary")
        begin, end = clock(rows[0]["source_date"], 4), clock(rows[-1]["source_date"], 20)
        sql = prefix + f""" eligible AS (
          SELECT session,intDiv(sip_timestamp_us,1000000)*1000000 bucket,
            max(p_ok) pv,max(e_ok) ev,countIf(v_ok) n
          FROM decoded WHERE in_session AND core=1 AND kind=1
          GROUP BY session,bucket
          HAVING pv AND ev AND n>0 AND bucket>={begin} AND bucket+1000000<={end})
          SELECT toString(session) session,count() origins FROM eligible
          GROUP BY session ORDER BY session FORMAT ArrowStream"""
        began = time.monotonic()
        from .query_memory import execute
        costs={}
        table=execute(self.client,sql,'ar-eligibility-'+digest([ticker,rows])[:24],gate,
                      self.query_memory_gib,self.query_max_memory_gib,costs)
        counts = {r["source_date"]: 0 for r in rows}
        for row in table.to_dicts():
            if row["session"] not in counts or not 0 <= row["origins"] <= 57600:
                raise ValueError("Eligibility result outside frozen sessions")
            counts[row["session"]] = int(row["origins"])
        return counts, dict(costs,fetch_seconds=time.monotonic()-began,eligibility_queries=1)

    def history_fetch(self, ticker, rows, gate, chunk_events=0, frames=tuple(range(5))):
        return self.fetch(ticker, rows, gate, chunk_events, frames, history=True)

    def fetch(self, ticker, rows, gate, chunk_events=0, frames=tuple(range(5)), *, history=False):
        """Reuse source-sealed unadjusted session contexts across pages/workers.

        Cached fine tails serve history ONLY. Normal fine reads still return
        full sessions. Coarse contexts are complete and serve either purpose.
        """
        if not hasattr(self, "context_cache"):
            return self._fetch_uncached(ticker, rows, gate, chunk_events, frames)
        pieces = [[] for _ in FRAMES]
        timing = dict(queries=0, query_seconds=0., admission_wait_seconds=0.,
                      fetch_seconds=0., resource_page_splits=0, coarse_cache_hits=0,
                      context_memory_hits=0, context_disk_hits=0, context_source_sessions=0)
        began = time.monotonic()

        def read(missing, group):
            arrays, costs = self._fetch_uncached(ticker, missing, gate, chunk_events, tuple(group))
            for key, value in costs.items():
                timing[key] = timing.get(key, 0) + value
            self.context_cache.put_many(ticker, missing, group.start == 3, arrays)
            timing["context_source_sessions"] += len(missing)
            return arrays

        for group in (range(3), range(3, 5)):
            selected = [i for i in group if i in frames]
            if not selected:
                continue
            if group.start == 0 and not history:
                arrays = read(rows, group)
                for i in selected:
                    pieces[i].append(arrays[i])
                continue
            for page in self.pages(rows, chunk_events):
                ready, missing = {}, []

                def flush():
                    if missing:
                        read(missing, group)
                        for row in missing:
                            ready[row["source_date"]] = self.context_cache.get(ticker, row, group.start == 3)[0]
                        missing.clear()

                for row in page:
                    arrays, kind = self.context_cache.get(ticker, row, group.start == 3)
                    if arrays is None:
                        missing.append(row)
                    else:
                        flush()  # Missing runs stay contiguous; never span a cached session.
                        ready[row["source_date"]] = arrays
                        timing["context_" + kind + "_hits"] += 1
                flush()
                for row in page:
                    for i in selected:
                        pieces[i].append(ready[row["source_date"]][i])
        timing["fetch_seconds"] = time.monotonic() - began
        return [np.concatenate(parts) if parts else np.empty(0, dtype=BAR)
                for parts in pieces], timing

    def _fetch_uncached(self, ticker, rows, gate, chunk_events=0, frames=tuple(range(5))):
        import polars as pl

        pieces = [[] for _ in FRAMES]
        began = time.monotonic()
        resource_splits = 0
        cache_hits = 0
        costs={}

        def query_page(page, coarse, sql=None):
            nonlocal resource_splits
            sql = sql or partition_page(bar_query(ticker, page, self.plan["authority"]["rules"], coarse), page)
            resource_error = None
            from .query_memory import execute
            try:
                return execute(self.client,sql,f'ar-month-{digest([ticker,page,coarse])[:24]}',gate,
                    self.query_memory_gib,self.query_max_memory_gib,costs)
            except RuntimeError as error:
                if len(page)<=1 or not any(code in str(error) for code in ('TIMEOUT_EXCEEDED','MEMORY_LIMIT_EXCEEDED')):raise
                resource_error=error
            # Release query admission before recursion. Every session remains
            # complete, disjoint and ordered; carry resets at session boundaries.
            if resource_error is not None:
                resource_splits += 1
                middle = len(page) // 2
                return pl.concat([query_page(page[:middle], coarse), query_page(page[middle:], coarse)])

        for page in self.pages(rows, chunk_events):
            for coarse, group in ((False, range(3)), (True, range(3, 5))):
                selected_frames = [f for f in group if f in frames]
                if not selected_frames:
                    continue
                if coarse:
                    sql = partition_page(bar_query(ticker, page, self.plan["authority"]["rules"], coarse), page)
                    table, hit = self.coarse_cache.get(sql, digest(page), lambda: query_page(page, coarse, sql))
                    cache_hits += int(hit)
                else:
                    table = query_page(page, coarse)
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
            query_seconds=costs.get('query_seconds',0),
            admission_wait_seconds=costs.get('admission_wait_seconds',0),
            fetch_seconds=time.monotonic() - began,
            queries=costs.get('queries',0),
            memory_retries=costs.get('memory_retries',0),
            resource_page_splits=resource_splits,
            coarse_cache_hits=cache_hits,
        )

    def close(self):
        sip.close(self.client)
