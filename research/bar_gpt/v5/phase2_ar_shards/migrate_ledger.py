"""Explicit one-time READ-ONLY legacy export. Generation never imports SQLite."""

import argparse
import hashlib
import json
from pathlib import Path
import time

import polars as pl
import psutil

from .ledger import Catalog, TABLES, schema, save_part, sealed_write
from ..shards.contract import digest, file_hash


def migrate(root):
    import sqlite3  # Confined to this explicit legacy migration command.

    root = Path(root)
    if not (root / "STOP").exists():
        raise ValueError("Drain the generator and preserve STOP before migration")
    execution = root / "execution.json"
    if execution.exists():
        owner = json.loads(execution.read_text())
        try:
            p = psutil.Process(owner["pid"])
            if "run_phase2_ar_shards.py" in " ".join(p.cmdline()):
                raise ValueError("Generator owner is still alive")
        except psutil.NoSuchProcess:
            pass
    plan = json.loads((root / "plan.json").read_text())
    if plan["hash"] != digest({k:v for k,v in plan.items() if k!="hash"}):
        raise ValueError("Frozen plan seal changed")
    binding = plan["hash"]
    final = root / "ledger-file-v1"
    if final.exists():
        raise ValueError("File ledger already exists; never overwrite migration evidence")
    stage = root / "ledger-file-v1.pending"
    stage.mkdir(exist_ok=True)
    sources = [root / "catalog.sqlite"]
    if (root / "catalog.sqlite-wal").exists():sources.append(root / "catalog.sqlite-wal")
    identity = dict(binding=binding, source=[dict(name=p.name,bytes=p.stat().st_size,sha256=file_hash(p)) for p in sources])
    marker = stage / "migration-owner.json"
    if marker.exists() and json.loads(marker.read_text()) != identity:
        raise ValueError("Foreign/inconsistent incomplete migration")
    if not marker.exists():marker.write_text(json.dumps(identity,sort_keys=True))
    began = time.monotonic()
    units, deferrals, selection = {}, {}, []
    deferred_origins = 0
    parts, parity = {}, {}
    c = sqlite3.connect((root / "catalog.sqlite").resolve().as_uri()+"?mode=ro",uri=True)
    try:
        c.execute("PRAGMA query_only=ON")
        if c.execute("PRAGMA query_only").fetchone() != (1,):raise ValueError("Legacy connection is not read-only")
        for name, fields in TABLES.items():
            columns = fields[:-1] if name == "completed_months" else fields
            cursor = c.execute("SELECT "+",".join(columns)+" FROM "+name+" ORDER BY rowid")
            part_list, rows_count = [], 0
            checksum = hashlib.sha256()
            while True:
                batch = cursor.fetchmany(8192)
                if not batch:break
                if psutil.virtual_memory().available < 64*1024**3:
                    raise MemoryError("Migration would violate RAM reserve")
                output = []
                for row in batch:
                    if name == "units":units[row[0]]=row[1]
                    elif name == "blocks" and row[4] is not None:selection.append(row)
                    elif name in ("deferrals","completed_months"):
                        key,report,seal = row
                        body=json.loads(report)
                        if (digest(body)!=seal or body["binding"]!=binding
                            or key!=digest([body["unit"]["ticker"],body["unit"]["month"]])):
                            raise ValueError("Legacy record hash/key/binding changed")
                        if name=="deferrals":
                            deferrals[key]=seal
                            deferred_origins+=body["stats"]["source_origins"]
                        else:
                            for record in body["sessions"]:
                                if record["kind"]=="published":
                                    path=str(Path("shards")/digest(body["unit"]["ticker"])[:24]/record["session"])
                                    if units.get(path)!=record["seal"]:raise ValueError("Legacy completed receipt differs")
                                elif record["kind"]=="deferred":
                                    if deferrals.get(digest([body["unit"]["ticker"],record["session"]]))!=record["seal"]:
                                        raise ValueError("Legacy completed deferral differs")
                                else:raise ValueError("Legacy completed month contains incomplete sessions")
                            row=(*row,digest(body["unit"]))
                    output.append(row)
                table=pl.DataFrame(output,schema=schema(name),orient="row")
                descriptor=save_part(stage/f"checkpoint-{name}-{len(part_list):06d}.parquet",table)
                readback=pl.read_parquet(stage/descriptor["name"]).rows()
                if readback!=output:raise ValueError("Legacy-to-Parquet field/order parity failed")
                checksum.update(digest(batch).encode())
                part_list.append(descriptor);rows_count+=len(batch)
            parts[name]=part_list
            parity[name]=dict(rows=rows_count,source_row_checksum=checksum.hexdigest(),all_fields_exact=True)
            print(json.dumps(dict(table=name,rows=rows_count,exact=True,seconds=time.monotonic()-began)),flush=True)
        if any(file_hash(root/p["name"])!=p["sha256"] for p in identity["source"]):
            raise ValueError("Legacy source changed during migration")
    finally:c.close()
    manifest=dict(version="bargpt-file-ledger-v1",binding=binding,checkpoint=parts,
                  checkpoint_summary=dict(deferred_sessions=len(deferrals),deferred_source_origins=deferred_origins),
                  segments=[],parts=[],sequence=0,migration_source=identity)
    name="manifest-"+digest(manifest)+".json"
    sealed_write(stage/name,manifest)
    sealed_write(stage/"HEAD.json",dict(manifest=name,sha256=file_hash(stage/name)))
    stage.rename(final)
    catalog=Catalog(root)
    try:
        actual=sorted((tuple(row) for row in catalog.blocks if row[4] is not None),key=lambda row:row[4])
        if actual!=sorted(selection,key=lambda row:row[4]):raise ValueError("Selected membership/order changed")
        report=dict(passed=True,plan_hash=binding,read_only_legacy=True,exact_tables=True,
                    selected_order_exact=True,tables=parity,checkpoint_hash=digest(parts),
                    stats=catalog.stats(),deferred_sessions=catalog.deferred_sessions,
                    completed_months=len(catalog.completed),source=identity,
                    seconds=time.monotonic()-began)
        report=sealed_write(final/"migration.json",report)
    finally:catalog.close()
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--execute',action='store_true')
    a=p.parse_args(argv)
    if not a.execute:raise ValueError('Explicit --execute required for one-time migration')
    print(json.dumps(migrate(a.root)),flush=True)


if __name__=='__main__':main()
