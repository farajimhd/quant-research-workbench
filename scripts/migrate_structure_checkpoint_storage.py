#!/usr/bin/env python3
"""Verified, resumable SSD migration and scoped structural checkpoint cleanup.

Default is a read-only plan. --execute copies, verifies, exchanges, then drops
the replaced tables. It never drops unrelated tables or guesses stale versions.
Run on the workstation with all structural writers stopped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

TABLES = (
    "qmd_structure_daily_checkpoint_v2",
    "qmd_structure_checkpoint_set_registry_v1",
    "qmd_structure_daily_checkpoint_v1",
    "qmd_structure_state_v2",
    "qmd_structure_events_v2",
    "qmd_structure_focus_registry_v1",
)
PREFIX = "canonical-tradable-20250101-20260831-"
CURRENT_SET = PREFIX + "v19-sip-condition-v1"
REQUIRED_SETS = (CURRENT_SET, PREFIX + "v18-sip-condition-v1", PREFIX + "v16-cert-v1")


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value):
        raise ValueError(f"Invalid SQL identifier: {value!r}")
    return f"`{value}`"


def literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def save(path: Path, state: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def retention(table: str, keep: list[str]) -> str:
    if table in TABLES[:2]:
        return "checkpoint_set_id IN (" + ",".join(map(literal, keep)) + ")"
    # Live v1/state/events/focus remain active code contracts, not stale tables.
    return "1"


class ClickHouse:
    def __init__(self, url: str, user: str, password: str):
        import requests

        self.session = requests.Session()
        self.session.auth = (user, password)
        self.url = url

    def request(self, sql: str, stream: bool = False):
        response = self.session.post(
            self.url, data=sql.encode("utf-8"), stream=stream,
            params={"max_threads": 2, "max_execution_time": 1800,
                    "max_memory_usage": 4294967296, "async_insert": 0,
                    "wait_for_async_insert": 1}, timeout=(10, 1900),
        )
        if response.status_code != 200:
            raise RuntimeError(f"ClickHouse HTTP {response.status_code}: {response.text[:2000]}")
        return response

    def execute(self, sql: str) -> None:
        with self.request(sql) as response:
            # ClickHouse can append an exception after sending HTTP 200.
            if response.text.strip():
                raise RuntimeError(f"Unexpected command response: {response.text[:2000]}")

    def rows(self, sql: str) -> list[dict]:
        with self.request(sql + " FORMAT JSONEachRow") as response:
            return [json.loads(line) for line in response.text.splitlines() if line]

    def digest(self, sql: str) -> dict:
        digest, count = hashlib.sha256(), 0
        with self.request(sql + " FORMAT TSV", stream=True) as response:
            for line in response.iter_lines():
                if not re.fullmatch(rb"[0-9A-F]{64}", line):
                    raise RuntimeError("Invalid digest stream; source may have failed mid-query")
                digest.update(line + b"\n")
                count += 1
        return {"rows": count, "sha256": digest.hexdigest()}


def inspect(ch: ClickHouse, database: str) -> dict[str, dict]:
    return {r["name"]: r for r in ch.rows(
        "SELECT name,toString(uuid) AS uuid,storage_policy,create_table_query,total_bytes "
        f"FROM system.tables WHERE database={literal(database)}")}


def ensure_quiet(ch: ClickHouse, database: str) -> None:
    # This operation requires a maintenance window, not a best-effort live copy.
    mutations = ch.rows(f"SELECT table,mutation_id FROM system.mutations WHERE database={literal(database)} AND NOT is_done AND table IN ({','.join(map(literal, TABLES))})")
    queries = ch.rows("SELECT query_id,query FROM system.processes WHERE query NOT LIKE '%system.processes%'")
    writes = [r["query_id"] for r in queries
              if re.search(r"\b(INSERT|ALTER|TRUNCATE|DROP|EXCHANGE)\b", r["query"], re.I)
              and any(table in r["query"] for table in TABLES)]
    pending = ch.rows("SELECT query FROM system.asynchronous_inserts")
    if mutations or writes or any(any(t in r["query"] for t in TABLES) for r in pending):
        raise RuntimeError("Structural writers, queued inserts, or mutations remain; stop them before migration")


def migrate(ch: ClickHouse, database: str, runtime: Path, keep: list[str], execute: bool) -> dict:
    db = identifier(database)
    tables = inspect(ch, database)
    engine = ch.rows(f"SELECT engine FROM system.databases WHERE name={literal(database)}")
    if not engine or engine[0]["engine"] != "Atomic":
        raise RuntimeError("Migration requires an Atomic database")
    policies = ch.rows("SELECT disks FROM system.storage_policies WHERE policy_name='live_market_ssd'")
    if not policies or any(r["disks"] != ["live_market_ssd"] for r in policies):
        raise RuntimeError("Required SSD-only live_market_ssd policy is missing")
    state_path = runtime / "migration.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["database"] != database or state["keep_sets"] != keep:
            raise RuntimeError("Migration identity changed; refusing to reuse its journal")
    else:
        state = {"database": database, "keep_sets": keep, "id": uuid.uuid4().hex[:12], "tables": {}}
        for name in TABLES:
            if name in tables:
                state["tables"][name] = {"source_uuid": tables[name]["uuid"], "source_policy": tables[name]["storage_policy"],
                                         "replacement": name + "_ssd_" + state["id"], "partitions": {}}
    print(f"Retain {len(keep)} required checkpoint sets; migrate {len(state['tables'])} structural tables.", flush=True)
    print("Live v1/state/events/focus contracts are retained. Other checkpoint sets are excluded.", flush=True)
    if not state_path.exists() and TABLES[0] in tables:
        state["excluded_sets"] = ch.rows(
            f"SELECT checkpoint_set_id,count() AS rows FROM {db}.{identifier(TABLES[0])} "
            f"WHERE NOT ({retention(TABLES[0], keep)}) GROUP BY checkpoint_set_id")
    if not execute:
        return state
    capacity = ch.rows("SELECT free_space FROM system.disks WHERE name='live_market_ssd'")
    estimated = sum(int(tables[n].get("total_bytes") or 0) for n, item in state["tables"].items() if not item.get("complete"))
    if not capacity or int(capacity[0]["free_space"]) < estimated * 2:
        raise RuntimeError("SSD lacks migration capacity plus merge/safety headroom")
    runtime.mkdir(parents=True, exist_ok=True)
    save(state_path, state)
    ensure_quiet(ch, database)
    for name, item in state["tables"].items():
        if item.get("complete"):
            current = inspect(ch, database).get(name, {})
            if current.get("uuid") != item.get("target_uuid") or current.get("storage_policy") != "live_market_ssd":
                raise RuntimeError(f"Completed migration no longer matches live table: {name}")
            continue
        tables = inspect(ch, database)
        replacement = item["replacement"]
        source, target = f"{db}.{identifier(name)}", f"{db}.{identifier(replacement)}"
        if tables[name]["uuid"] == item.get("target_uuid"):
            # EXCHANGE succeeded before a crash. Never exchange back on resume.
            item["exchanged"] = True
        elif tables[name]["uuid"] != item["source_uuid"]:
            raise RuntimeError(f"Unexpected source identity: {name}")
        if not item.get("exchanged"):
            ch.execute(f"SYSTEM STOP MERGES {source}")
            if replacement not in tables:
                ddl = tables[name]["create_table_query"]
                ddl = re.sub(r"^CREATE TABLE\s+\S+", f"CREATE TABLE {target}", ddl, count=1)
                ddl = re.sub(r"\s+UUID\s+'[^']+'", "", ddl, count=1)
                ddl = re.sub(r"storage_policy\s*=\s*'[^']+'", "storage_policy = 'live_market_ssd'", ddl)
                if "storage_policy = 'live_market_ssd'" not in ddl:
                    ddl += (", " if " SETTINGS " in ddl else " SETTINGS ") + "storage_policy = 'live_market_ssd'"
                if "snapshot_json" in ddl:
                    ddl = re.sub(r"index_granularity\s*=\s*\d+", "index_granularity = 1", ddl)
                ch.execute(ddl)
                tables = inspect(ch, database)
            if item.get("target_uuid") not in (None, tables[replacement]["uuid"]):
                raise RuntimeError("Replacement table identity changed")
            item["target_uuid"] = tables[replacement]["uuid"]
            save(state_path, state)
            ch.execute(f"SYSTEM STOP MERGES {target}")
            columns = [r["name"] for r in ch.rows(f"SELECT name FROM system.columns WHERE database={literal(database)} AND table={literal(name)} ORDER BY position")]
            item["columns"] = columns
            projection = ",".join(map(identifier, columns))
            predicate = retention(name, keep)
            partitions = ch.rows(f"SELECT DISTINCT _partition_id AS p FROM {source} WHERE {predicate}")
            for partition in partitions:
                part = partition["p"]
                condition = f"({predicate}) AND _partition_id={literal(part)}"
                # Stable, complete row digest; order by digest preserves multiplicity.
                def fingerprint(table: str) -> dict:
                    selected = condition if table == source else f"_partition_id={literal(part)}"
                    return ch.digest(f"SELECT hex(SHA256(toJSONString(tuple({projection})))) AS h FROM {table} FINAL WHERE {selected} ORDER BY h")
                before = fingerprint(source)
                if item["partitions"].get(part) == before and fingerprint(target) == before:
                    print(f"{name} {part}: verified, reused", flush=True)
                    continue
                print(f"{name} {part}: copying {before['rows']:,} retained rows", flush=True)
                # Only the journal-owned staging partition may be replaced on retry.
                ch.execute(f"ALTER TABLE {target} DROP PARTITION ID {literal(part)}")
                ch.execute(f"INSERT INTO {target} ({projection}) SELECT {projection} FROM {source} FINAL WHERE {condition}")
                if fingerprint(target) != before or fingerprint(source) != before:
                    raise RuntimeError(f"Exact row verification failed: {name} {part}; originals retained")
                item["partitions"][part] = before
                save(state_path, state)
            ensure_quiet(ch, database)
            # Reconcile the whole retained relation, including empty tables/new partitions.
            full_sql = f"SELECT hex(SHA256(toJSONString(tuple({projection})))) AS h FROM {{table}} FINAL WHERE {{predicate}} ORDER BY h"
            verified = ch.digest(full_sql.format(table=source, predicate=predicate))
            if verified != ch.digest(full_sql.format(table=target, predicate="1")):
                raise RuntimeError(f"Final table reconciliation failed: {name}")
            item["verified_digest"] = verified
            save(state_path, state)
            bad = ch.rows(f"SELECT count() AS n FROM system.parts WHERE active AND database={literal(database)} AND table={literal(replacement)} AND disk_name!='live_market_ssd'")
            if int(bad[0]["n"]):
                raise RuntimeError("Replacement still has non-SSD parts")
            ch.execute(f"EXCHANGE TABLES {source} AND {target}")
            item["exchanged"] = True
            save(state_path, state)
        # Drop only the verified original UUID after successful exchange, never a pattern.
        tables = inspect(ch, database)
        if tables[name]["uuid"] != item["target_uuid"] or tables[name]["storage_policy"] != "live_market_ssd":
            raise RuntimeError("Post-exchange identity or storage mismatch")
        projection = ",".join(map(identifier, item["columns"]))
        actual = ch.digest(f"SELECT hex(SHA256(toJSONString(tuple({projection})))) AS h FROM {source} FINAL ORDER BY h")
        if actual != item["verified_digest"]:
            raise RuntimeError("Post-exchange content changed; refusing to drop original data")
        if replacement in tables:
            if tables[replacement]["uuid"] != item["source_uuid"]:
                raise RuntimeError("Refusing to drop an unrecognized table")
            ch.execute(f"DROP TABLE {target} SYNC")
        ch.execute(f"SYSTEM START MERGES {source}")
        item["complete"] = True
        save(state_path, state)
        print(f"{name}: verified on SSD; replaced stale table dropped", flush=True)
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://192.168.0.21:18123")
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--env-file", type=Path, default=Path(r"D:\TradingML\secrets\.env"))
    parser.add_argument("--runtime-dir", type=Path, default=Path(r"D:\TradingML\runtimes\qmd_gateway\structure-storage-migration-v1"))
    parser.add_argument("--keep-set", action="append", default=[])
    parser.add_argument("--execute", action="store_true", help="Copy, verify, swap, and drop only replaced structural tables/stale sets")
    args = parser.parse_args()
    from dotenv import dotenv_values
    config = {**dotenv_values(args.env_file), **os.environ}
    user = next((config[k] for k in ("QMD_CLICKHOUSE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "CLICKHOUSE_WORKSTATION_USER", "CLICKHOUSE_USER") if config.get(k)), "default")
    password = next((config[k] for k in ("QMD_CLICKHOUSE_PASSWORD", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD", "CLICKHOUSE_WORKSTATION_PASSWORD", "CLICKHOUSE_PASSWORD") if config.get(k)), "")
    configured_sets = {config[k] for k in ("QMD_STRUCTURE_CHECKPOINT_SET_ID", "QMD_HISTORY_STRUCTURE_CHECKPOINT_SET_ID") if config.get(k) and config[k] != "live"}
    keep = sorted(set(REQUIRED_SETS) | set(args.keep_set) | configured_sets)
    result = migrate(ClickHouse(args.url, user, password), args.database, args.runtime_dir, keep, args.execute)
    if not args.execute:
        print(json.dumps(result, indent=2))
        print("Plan only. Use --execute during the structural-writer maintenance window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
