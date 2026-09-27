from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
import json

import pytest

from src.trading_runtime.arte_market_day_certification import verify_market_day_certificate
from src.trading_runtime.arte_market_day_keeper import BuildAttestation
from src.trading_runtime.arte_market_day_session_seal import (
    SESSION_SEAL, MarketDaySessionSealClient, load_session_seal,
    prepare_session_seal, publish_session_seal, session_seal_receipt,
    read_sealed_session_families, verify_session_seal_row,
)
from test_arte_market_day_certification import BUILD, DAY, FakeReader, inventory
from test_arte_market_day_cold_preflight import fixture
from test_arte_market_day_publisher import FakeClickHouse


def source():
    tables = inventory()
    fence = tables["market_day_build_fence_v1"][0]
    certificate = verify_market_day_certificate(FakeReader(tables), BUILD,
                                                sessions=(DAY,),
                                                include_session_hashes=True)
    proof = BuildAttestation(
        BUILD, fence["definition_hash"], fence["source_plan_hash"],
        fence["source_inventory_hash"], fence["header_hash"],
        fence["scope_hash"], fence["stage_hash"], fence["seed_hash"],
        "producer", 1,
    )
    families = {name: tables[name] for name in (
        "market_day_planned_scope_v1", "market_day_stage_certificate_v1",
        "market_day_seed_v1", "market_day_source_unit_v1",
    )}
    return certificate, proof, families


def test_session_seal_is_typed_normalized_and_root_bound():
    certificate, proof, families = source()
    row = prepare_session_seal(certificate, proof,
                               session_date=date.fromisoformat(DAY), families=families)
    assert set(row) == {name for name, _ in SESSION_SEAL.columns}
    assert SESSION_SEAL.partition == "toYYYYMM(session_date)"
    assert SESSION_SEAL.order == "build_id,session_date"
    assert row["scope_count"] == row["seed_count"] == row["source_unit_count"] == 1
    assert row["stage_count"] == 3
    receipt = session_seal_receipt(row)
    verify_session_seal_row(row, proof, receipt)
    with pytest.raises(RuntimeError, match="exact typed"):
        verify_session_seal_row(row, proof, receipt + b"\nforged")
    with pytest.raises(RuntimeError, match="exact typed"):
        verify_session_seal_row({**row, "stage_count": 4}, proof, receipt)
    with pytest.raises(RuntimeError, match="Keeper CAS attestation"):
        prepare_session_seal(
            certificate, replace(proof, stage_hash="f" * 64),
            session_date=date.fromisoformat(DAY), families=families,
        )


def test_session_seal_rejects_missing_duplicate_or_changed_source_facts():
    certificate, proof, families = source()
    for name in families:
        incomplete = {key: deepcopy(value) for key, value in families.items()}
        incomplete[name].clear()
        with pytest.raises(ValueError, match="globally verified rows"):
            prepare_session_seal(certificate, proof,
                                 session_date=date.fromisoformat(DAY),
                                 families=incomplete)
    duplicate = {key: deepcopy(value) for key, value in families.items()}
    duplicate["market_day_stage_certificate_v1"].append(
        deepcopy(duplicate["market_day_stage_certificate_v1"][0]))
    with pytest.raises(ValueError, match="globally verified rows"):
        prepare_session_seal(certificate, proof,
                             session_date=date.fromisoformat(DAY), families=duplicate)
    changed = {key: deepcopy(value) for key, value in families.items()}
    changed["market_day_source_unit_v1"][0]["event_count"] += 1
    with pytest.raises(ValueError, match="globally verified rows"):
        prepare_session_seal(certificate, proof,
                             session_date=date.fromisoformat(DAY), families=changed)


def test_session_seal_publishes_once_and_cold_reads_exact_receipt(monkeypatch):
    source_client, keeper = fixture()
    certificate = verify_market_day_certificate(
        FakeReader(source_client.rows), BUILD, sessions=(DAY,),
        include_session_hashes=True)
    proof = keeper.load(BUILD)
    assert proof is not None
    families = {name: source_client.rows[name] for name in (
        "market_day_planned_scope_v1", "market_day_stage_certificate_v1",
        "market_day_seed_v1", "market_day_source_unit_v1",
    )}
    row = prepare_session_seal(certificate, proof,
                               session_date=date.fromisoformat(DAY), families=families)

    class SealHttp(FakeClickHouse):
        def __init__(self):
            super().__init__()
            self.seal_rows = []

        def execute(self, sql):
            if SESSION_SEAL.name in sql:
                if "FROM system.tables" in sql:
                    return json.dumps(dict(name=SESSION_SEAL.name, engine="MergeTree",
                        storage_policy="live_market_ssd", partition_key=SESSION_SEAL.partition,
                        sorting_key=SESSION_SEAL.order))
                if "FROM system.columns" in sql:
                    return "\n".join(json.dumps(dict(table=SESSION_SEAL.name,
                        name=name, type=kind)) for name, kind in SESSION_SEAL.columns)
                if "FROM system.data_skipping_indices" in sql or "FROM system.parts" in sql:
                    return ""
                if f"FROM arte.{SESSION_SEAL.name}" in sql:
                    return "\n".join(json.dumps(value) for value in self.seal_rows)
            return super().execute(sql)

    http = SealHttp()
    http.rows = deepcopy(source_client.rows)
    writes = []

    def insert(_http, database, name, columns, rows):
        assert _http is http and (database, name) == ("arte", SESSION_SEAL.name)
        assert columns == [column for column, _ in SESSION_SEAL.columns]
        writes.extend(deepcopy(rows))
        http.seal_rows.extend(deepcopy(rows))

    monkeypatch.setattr(
        "src.trading_runtime.arte_market_day_session_seal.insert_json_each_row", insert)
    publisher = MarketDaySessionSealClient(http)
    publish_session_seal(publisher, keeper, proof, row)
    publish_session_seal(publisher, keeper, proof, row)
    assert writes == [row]
    assert load_session_seal(publisher, keeper, proof, date.fromisoformat(DAY)) == row
    selected = read_sealed_session_families(
        publisher, proof, row, session_seal_receipt(row))
    assert len(selected["market_day_stage_certificate_v1"]) == 3
    http.rows["market_day_stage_certificate_v1"][0]["output_hash"] = "changed"
    with pytest.raises(RuntimeError, match="stage rows differ"):
        read_sealed_session_families(publisher, proof, row, session_seal_receipt(row))
    http.seal_rows.append(deepcopy(row))
    with pytest.raises(RuntimeError, match="duplicate ClickHouse"):
        load_session_seal(publisher, keeper, proof, date.fromisoformat(DAY))
