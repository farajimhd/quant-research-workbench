from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from scripts.build_market_day import digest
from scripts.clickhouse.plan_market_day_certificate import audit_saved_build
from test_arte_market_day_source_plan import plan as source_plan_fixture


def _saved_build(runtime: Path) -> str:
    plan = source_plan_fixture()
    definition = dict(version="market-day-core-v5", plan=plan,
                      calculation_source="b" * 64, rules_hash="c" * 64)
    build_id = digest(definition)
    archive = runtime / "market-day"
    archive.mkdir()
    (archive / f"{build_id}.json").write_text(json.dumps({
        "build_id": build_id, "status": "core_complete", "definition": definition,
    }), encoding="utf-8")
    with sqlite3.connect(runtime / "build-ledger-v2.sqlite3") as connection:
        connection.executescript("""
            CREATE TABLE builds (build_id TEXT, definition_hash TEXT, version TEXT,
                                 database_name TEXT, status TEXT);
            CREATE TABLE units (build_id TEXT, session_date TEXT, ticker TEXT,
                                stage TEXT, attempt_id TEXT, source_hash TEXT,
                                output_rows INTEGER, output_hash TEXT, status TEXT);
            CREATE TABLE seeds (build_id TEXT, session_date TEXT, ticker TEXT,
                                attempt_id TEXT, mode INTEGER, predecessor_date TEXT,
                                prior_build_id TEXT, prior_state_hash TEXT);
        """)
        connection.execute("INSERT INTO builds VALUES (?,?,?,?,?)", (
            build_id, build_id, "market-day-core-v5", "arte", "core_complete"))
        for unit in plan["units"]:
            day, ticker = unit["source_date"], unit["ticker"]
            for stage in ("bars", "technical", "broker_100ms"):
                connection.execute("INSERT INTO units VALUES (?,?,?,?,?,?,?,?,?)", (
                    build_id, day, ticker, stage, "attempt", "source", 0, "0", "complete"))
            connection.execute("INSERT INTO seeds VALUES (?,?,?,?,?,?,?,?)", (
                build_id, day, ticker, "attempt", 0, "", "", ""))
    return build_id


def test_saved_build_audit_prepares_full_certificate_without_publication(tmp_path):
    build_id = _saved_build(tmp_path)
    result = audit_saved_build(tmp_path, build_id)
    assert result["build_id"] == build_id
    assert result["family_rows"]["market_day_planned_scope_v1"] == 1
    assert result["family_rows"]["market_day_stage_certificate_v1"] == 3
    assert len(result["source_inventory_hash"]) == 64


def test_saved_build_audit_rejects_changed_manifest(tmp_path):
    build_id = _saved_build(tmp_path)
    archive = tmp_path / "market-day" / f"{build_id}.json"
    manifest = json.loads(archive.read_text(encoding="utf-8"))
    manifest["definition"]["rules_hash"] = "changed"
    archive.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="exact core-complete"):
        audit_saved_build(tmp_path, build_id)
