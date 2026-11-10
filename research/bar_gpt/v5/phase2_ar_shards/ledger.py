"""Single-owner immutable Parquet checkpoint and batched journal; no SQL engine."""

from collections import deque
import json
import os
from pathlib import Path
import time

import polars as pl
import psutil

from .contract import BLOCK
from ..shards.contract import digest, file_hash, write_json
from ..quote_repair_v1 import verify_seal

TABLES = {
    "units": ["path", "seal", "bytes", "split", "source_origins", "warmup", "boundary"],
    "blocks": ["path", "ordinal", "split", "liquid", "selection"],
    "tails": ["ticker", "month", "pending"],
    "deferrals": ["key", "report", "hash"],
    "completed_months": ["key", "report", "hash", "unit_hash"],
}
STRINGS = {"path", "seal", "ticker", "month", "key", "report", "hash", "unit_hash"}


def schema(table):
    return {k: pl.String if k in STRINGS else pl.Int64 for k in TABLES[table]}


def save_part(path, table):
    """fsync, independently hash the target, retain orphan parts on interruption."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if psutil.disk_usage(str(path.parent)).free < 150*1024**3 + 2*table.estimated_size() + 1024**2:
        raise OSError("Ledger publication would consume disk reserve")
    temporary = path.with_suffix(".partial")
    with temporary.open("wb") as stream:
        table.write_parquet(stream, compression="zstd")
        stream.flush()
        os.fsync(stream.fileno())
    sha = file_hash(temporary)
    if path.exists():
        if file_hash(path) != sha:
            raise ValueError("Immutable ledger part collision")
    else:
        temporary.replace(path)
    if file_hash(path) != sha:
        raise ValueError("Ledger independent readback mismatch")
    return dict(name=path.name, bytes=path.stat().st_size, sha256=sha, rows=table.height)


def sealed_write(path, body):
    body = dict(body)
    body["hash"] = digest(body)
    path = Path(path)
    body = json.loads(json.dumps(body))
    if path.exists() and path.name != "HEAD.json":
        if json.loads(path.read_text()) != body:
            raise ValueError("Immutable ledger manifest collision")
        return body
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(body, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(8):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 7:raise
            time.sleep(.05 * (attempt + 1))
    observed = json.loads(Path(path).read_text())
    if observed != body:
        raise ValueError("Ledger manifest readback differs")
    return body


class FileLedger:
    def __init__(self, root):
        self.root = Path(root) / "ledger-file-v1"
        self.root.mkdir(exist_ok=True)
        self.buffer = []
        self.last_flush = time.monotonic()
        head = self.root / "HEAD.json"
        if not head.exists():
            if any(self.root.iterdir()):
                raise ValueError("Ledger HEAD is missing; preserve existing checkpoint/journal evidence")
            if (self.root.parent / "catalog.sqlite").exists():
                raise ValueError("Explicit read-only legacy ledger migration required")
            self.manifest = dict(version="bargpt-file-ledger-v1", checkpoint={},
                                 checkpoint_summary={}, segments=[], parts=[], sequence=0)
            plan = self.root.parent / "plan.json"
            self.manifest["binding"] = json.loads(plan.read_text())["hash"] if plan.exists() else None
            self._commit_manifest(self.manifest)
        self.manifest = self._read_manifest()
        self.sequence = self.manifest["sequence"]

    def _read_manifest(self):
        head = json.loads((self.root / "HEAD.json").read_text())
        verify_seal(head, "hash")
        path = self.root / head["manifest"]
        if path.name != head["manifest"] or file_hash(path) != head["sha256"]:
            raise ValueError("Ledger HEAD/manifest changed")
        manifest = json.loads(path.read_text())
        verify_seal(manifest, "hash")
        if manifest["version"] != "bargpt-file-ledger-v1":
            raise ValueError("Unknown ledger version")
        plan = self.root.parent / "plan.json"
        if plan.exists() and json.loads(plan.read_text())["hash"] != manifest.get("binding"):
            raise ValueError("File ledger belongs to another frozen plan")
        return manifest

    def _commit_manifest(self, manifest):
        body = {k: v for k, v in manifest.items() if k != "hash"}
        name = "manifest-" + digest(body) + ".json"
        sealed = sealed_write(self.root / name, body)
        sealed_write(self.root / "HEAD.json", dict(manifest=name, sha256=file_hash(self.root / name)))
        self.manifest = sealed

    def checked_part(self, item, columns=None):
        path = self.root / item["name"]
        if (path.name != item["name"] or path.stat().st_size != item["bytes"]
            or file_hash(path) != item["sha256"]):
            raise ValueError("Immutable ledger part changed")
        result = pl.read_parquet(path, columns=columns)
        if result.height != item["rows"]:
            raise ValueError("Ledger row count changed")
        return result

    def table(self, name, columns=None):
        parts = [self.checked_part(p, columns) for p in self.manifest["checkpoint"].get(name, [])]
        return pl.concat(parts) if parts else pl.DataFrame(schema={k:v for k,v in schema(name).items() if columns is None or k in columns})

    def events(self):
        expected = 1
        parts = []
        for segment in self.manifest.get("segments", []):
            path = self.root / segment["name"]
            if path.name != segment["name"] or file_hash(path) != segment["sha256"]:
                raise ValueError("Ledger journal segment changed")
            body = json.loads(path.read_text());verify_seal(body,"hash")
            parts.extend(body["parts"])
        parts.extend(self.manifest["parts"])
        for part in parts:
            for sequence, kind, key, body, seal in self.checked_part(part).iter_rows():
                value = json.loads(body)
                if sequence != expected or digest(value) != seal:
                    raise ValueError("Ledger journal order/seal changed")
                expected += 1
                yield kind, key, value
        if expected - 1 != self.manifest["sequence"]:
            raise ValueError("Ledger journal sequence changed")

    def append(self, kind, key, body):
        self.sequence += 1
        self.buffer.append((self.sequence, kind, key, json.dumps(body, separators=(",", ":")), digest(body)))

    def flush(self, force=False):
        if not self.buffer or (not force and len(self.buffer)<256 and time.monotonic()-self.last_flush<2):
            return
        rows = self.buffer
        table = pl.DataFrame(rows, schema={"sequence":pl.Int64,"kind":pl.String,"key":pl.String,"body":pl.String,"seal":pl.String}, orient="row")
        name = f"journal-{rows[0][0]:012d}-{digest(rows)[:24]}.parquet"
        part = save_part(self.root / name, table)
        manifest = dict(self.manifest, parts=[*self.manifest["parts"], part], sequence=rows[-1][0])
        if len(manifest["parts"]) >= 128:
            body = dict(parts=manifest["parts"])
            name = "segment-" + digest(body) + ".json"
            sealed_write(self.root / name, body)
            manifest["segments"] = [*manifest.get("segments", []),dict(name=name,sha256=file_hash(self.root/name))]
            manifest["parts"] = []
        self._commit_manifest(manifest)
        self.buffer = []
        self.last_flush = time.monotonic()

    def close(self):
        self.flush(True)


class Catalog:
    """Typed in-memory indices, immutable checkpoint, restart-safe batch journal."""
    def __init__(self, root):
        self.root = Path(root)
        self.ledger = FileLedger(root)
        units = self.ledger.table("units")
        self.units = {r[0]: r for r in units.iter_rows()}
        self.blocks = [list(r) for r in self.ledger.table("blocks").iter_rows()]
        self.tails = {r[0]:(r[1],r[2]) for r in self.ledger.table("tails").iter_rows()}
        self.deferrals = dict(self.ledger.table("deferrals", ["key","hash"]).iter_rows())
        self.completed = {key:(seal,unit_hash) for key,seal,unit_hash in self.ledger.table("completed_months", ["key","hash","unit_hash"]).iter_rows()}
        summary = self.ledger.manifest["checkpoint_summary"]
        self.deferred_sessions = summary.get("deferred_sessions", 0)
        self.deferred_source_origins = summary.get("deferred_source_origins", 0)
        if self.deferred_sessions != len(self.deferrals):
            raise ValueError("Deferral checkpoint summary differs")
        self.pending = {}
        orders = []
        for i, row in enumerate(self.blocks):
            if row[4] is None:
                self.pending.setdefault((row[2],row[3]), deque()).append(i)
            else:
                orders.append(row[4])
        if sorted(orders) != list(range(len(orders))):
            raise ValueError("Selected block ordering changed")
        self.order = len(orders)
        self.totals = dict(units=len(self.units), bytes=sum(r[2] for r in self.units.values()),
                           source_origins=sum(r[4] for r in self.units.values()),
                           warmup_deferred=sum(r[5] for r in self.units.values()),
                           boundary_tail_deferred=sum(r[6] for r in self.units.values()),
                           candidate_blocks=len(self.blocks), selected_blocks=self.order,
                           pending_tail_origins=sum(p[1] for p in self.tails.values()))
        self.split_bytes = {s:sum(r[2] for r in self.units.values() if r[3]==s) for s in (0,1)}
        for kind, key, body in self.ledger.events():
            self._apply(kind,key,body)
        self.stats()

    def _apply(self, kind, key, body):
        binding = self.ledger.manifest.get("binding")
        observed = body.get("receipt",body).get("binding")
        if binding is not None and observed != binding:
            raise ValueError("Foreign ledger event binding")
        if kind == "unit":
            self._unit(body)
        elif kind == "deferral":
            if key != digest([body["unit"]["ticker"],body["unit"]["month"]]):
                raise ValueError("Foreign deferral identity")
            seal = digest(body)
            if key in self.deferrals:
                if self.deferrals[key] != seal:raise ValueError("Deferred session changed")
                return
            self.deferrals[key] = seal
            self.deferred_sessions += 1
            self.deferred_source_origins += body["stats"]["source_origins"]
        elif kind == "complete":
            if key != digest([body["unit"]["ticker"],body["unit"]["month"]]):
                raise ValueError("Foreign completed unit")
            value = (digest(body), digest(body["unit"]))
            if key in self.completed and self.completed[key] != value:
                raise ValueError("Completed month changed")
            self.completed[key] = value
        else:
            raise ValueError("Unknown ledger event")

    def _record(self, kind, key, body):
        self._apply(kind,key,body)
        self.ledger.append(kind,key,body)

    def is_complete(self, unit):
        prior = self.completed.get(digest([unit["ticker"],unit["month"]]))
        if prior and prior[1] != digest(unit):raise ValueError("Completed unit changed")
        return prior is not None

    def complete_month(self, key, body):
        for record in body["sessions"]:
            if record["kind"] == "deferred":
                if self.deferrals.get(digest([body["unit"]["ticker"],record["session"]])) != record["seal"]:
                    raise ValueError("Completed session deferral changed")
            elif record["kind"] == "published":
                from ..shards.contract import digest as key_hash
                path = str(Path("shards") / key_hash(body["unit"]["ticker"])[:24] / record["session"])
                if path not in self.units or self.units[path][1] != record["seal"]:
                    raise ValueError("Completed session receipt changed")
            else:raise ValueError("Incomplete month cannot be committed")
        self._record("complete",key,body)

    def defer(self, key, body):
        seal = digest(body)
        prior = self.deferrals.get(key)
        if prior:
            if prior != seal:raise ValueError("Deferred session changed")
        else:self._record("deferral",key,body)
        return seal

    def _unit(self, body):
        relative, r, accounted = body["path"],body["receipt"],body["accounted_bytes"]
        if relative in self.units:
            if self.units[relative][1] != r["hash"]:raise ValueError("Registered session changed")
            return
        tail_key = r["ticker"] + ("|"+r["month"] if r.get("independent_unit") else "")
        prior_tail = self.tails.get(tail_key)
        if prior_tail and prior_tail[0]>=r["month"]:raise ValueError("Session registration order changed")
        split = r["splits"][0] if r["splits"] else 0
        self.units[relative] = (relative,r["hash"],accounted,split,r["stats"]["source_origins"],r["stats"]["warmup_deferred"],r["stats"]["boundary_tail_deferred"])
        for ordinal, s in enumerate(r["splits"]):
            index=len(self.blocks)
            self.blocks.append([relative,ordinal,s,r["liquid"],None])
            self.pending.setdefault((s,r["liquid"]),deque()).append(index)
        for s in (0,1):
            liquid=self.pending.setdefault((s,1),deque());less=self.pending.setdefault((s,0),deque())
            groups=min(len(liquid)//3,len(less))
            for queue,count in ((liquid,groups*3),(less,groups)):
                for _ in range(count):
                    self.blocks[queue.popleft()][4]=self.order
                    self.order+=1
        self.tails[tail_key]=(r["month"],r["stats"]["pending_origins"])
        self.totals["pending_tail_origins"] += r["stats"]["pending_origins"]-(prior_tail[1] if prior_tail else 0)
        for key,value in (("units",1),("bytes",accounted),("source_origins",r["stats"]["source_origins"]),("warmup_deferred",r["stats"]["warmup_deferred"]),("boundary_tail_deferred",r["stats"]["boundary_tail_deferred"]),("candidate_blocks",r["blocks"])):
            self.totals[key]+=value
        self.totals["selected_blocks"]=self.order
        self.split_bytes[split]=self.split_bytes.get(split,0)+accounted

    def register(self, path, receipt):
        relative=str(Path(path).relative_to(self.root))
        if relative in self.units:
            if self.units[relative][1]!=receipt["hash"]:raise ValueError("Registered session changed")
            return
        accounted=receipt["bytes"]
        if (Path(path)/"receipt.json").exists():
            accounted+=(Path(path)/"receipt.json").stat().st_size+(Path(path)/"publication.json").stat().st_size
        self._record("unit",relative,dict(path=relative,receipt=receipt,accounted_bytes=accounted))

    def stats(self):
        accounted=self.totals["candidate_blocks"]*BLOCK+self.totals["pending_tail_origins"]+self.totals["warmup_deferred"]+self.totals["boundary_tail_deferred"]
        if accounted!=self.totals["source_origins"]:raise ValueError("Source origin coverage does not reconcile")
        return dict(self.totals,reconciled_source_origins=accounted,selected_origins=self.order*BLOCK,
                    unselected_complete_blocks=self.totals["candidate_blocks"]-self.order,
                    train_bytes=self.split_bytes.get(0,0),validation_bytes=self.split_bytes.get(1,0))

    def export(self, binding):
        self.flush(True)
        rows=sorted((tuple(r) for r in self.blocks if r[4] is not None),key=lambda r:r[4])
        path=self.root/f"selection-{len(rows):012d}-{digest(rows)[:16]}.parquet"
        if not path.exists():
            temporary=path.with_suffix('.partial')
            pl.DataFrame(rows,schema=schema('blocks'),orient='row').write_parquet(temporary)
            temporary.replace(path)
        elif pl.read_parquet(path).rows()!=rows:raise ValueError("Published selection changed")
        body=dict(binding=binding,path=path.name,sha256=file_hash(path),blocks=len(rows),origins=len(rows)*BLOCK,
                  mix="3 liquid : 1 less-liquid per split",input_context_only=True,future_target_purge_required=True)
        body['hash']=digest(body);write_json(self.root/'selection.json',body)
        return body

    def inspection_catalog(self, plan):
        jobs={digest(j['ticker'])[:24]:j for j in plan['jobs']}
        groups={}
        for path,ordinal,split,liquid,selection in self.blocks:
            normalized=path.replace('\\','/');key,day=normalized.split('/')[1:3];year=int(day[:4])
            item=groups.setdefault((key,year,liquid),dict(paths=set(),blocks=0,selected=0))
            item['paths'].add(path);item['blocks']+=1;item['selected']+=int(selection is not None)
        streams=[]
        for (key,year,liquid),item in groups.items():
            job=jobs[key]
            if not any(int(y['year'])==year and int(y['liquid'])==liquid for y in job['years']):raise ValueError('Research classification changed')
            streams.append(dict(stream=f'{key}-{year}',ticker=job['ticker'],year=year,liquid=bool(liquid),units=len(item['paths']),blocks=item['blocks'],selected_blocks=item['selected']))
        body=dict(binding=plan['hash'],updated=time.time(),streams=sorted(streams,key=lambda r:(r['ticker'],r['year'])))
        body['hash']=digest(body);write_json(self.root/'research-catalog.json',body)

    def flush(self, force=False):self.ledger.flush(force)

    def close(self):self.ledger.close()
