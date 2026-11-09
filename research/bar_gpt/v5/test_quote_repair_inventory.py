"""Repair reads the sealed liquid supplement inventory, never invents a plan."""

from unittest.mock import patch

import polars as pl
import pytest

from .quote_rebuild_v1 import Rebuilder, frozen_jobs
from .shards.balanced_plan import ticker_bounds
from .shards.contract import digest, write_json


def fixture(root, supplement=True):
    (root / "plan").mkdir(exist_ok=True)
    jobs = [dict(ticker="TEST", years=[dict(year=2020, bucket=0, events=12)])]
    plan = dict(jobs=jobs, parent_plan_hash="original-plan", universe=["TEST"], fine=[])
    if supplement:
        contract = dict(component="liquid-supplement", plan_hash=digest(plan))
        write_json(root / "supplement-plan.json", plan)
    else:
        plan["hash"] = digest(plan)
        contract = dict(plan_hash=plan["hash"])
        write_json(root / "plan/manifest.json", plan)
    contract["authority"] = dict(
        corporate_actions=dict(rows=[]), dictionary_hash="dictionary"
    )
    parent = dict(contract=contract, binding=digest(contract))
    write_json(root / "contract.json", parent)
    return parent, plan


def test_supplement_without_base_manifest_initializes_real_rebuilder(tmp_path):
    parent, plan = fixture(tmp_path)
    transport = type("Transport", (), {"default_query_params": {}})()
    with patch(
        "research.bar_gpt.v5.quote_rebuild_v1.source.client", return_value=transport
    ):
        b = Rebuilder(tmp_path / "cache.sqlite", tmp_path, 1, "unused")
    try:
        assert not (tmp_path / "plan/manifest.json").exists()
        assert b.jobs == {"TEST": plan["jobs"][0]}
        assert b.client.default_query_params["max_threads"] == 1
    finally:
        b.db.close()


def test_supplement_bounds_use_exact_frozen_partition_rows(tmp_path):
    parent, plan = fixture(tmp_path)
    pl.DataFrame(
        dict(
            ticker=["TEST", "OTHER"],
            source_date=["2020-01-02"] * 2,
            event_count=[12, 99],
            first_ordinal=[0, 0],
            next_ordinal=[12, 99],
        )
    ).write_parquet(tmp_path / "plan/2020-00.parquet")
    job = frozen_jobs(tmp_path, 1, parent)["TEST"]
    assert ticker_bounds(tmp_path, job) == [
        dict(
            ticker="TEST",
            source_date="2020-01-02",
            event_count=12,
            first_ordinal=0,
            next_ordinal=12,
        )
    ]


def test_base_inventory_is_unchanged(tmp_path):
    parent, plan = fixture(tmp_path, False)
    assert frozen_jobs(tmp_path, 1, parent) == {"TEST": plan["jobs"][0]}


def test_phase_two_inventory_path_is_unchanged(tmp_path):
    _, plan = fixture(tmp_path, False)
    (tmp_path / "inventory/plan").mkdir(parents=True)
    write_json(tmp_path / "inventory/plan/manifest.json", plan)
    assert frozen_jobs(tmp_path, 2, dict(ordinal_hash=plan["hash"])) == {
        "TEST": plan["jobs"][0]
    }


@pytest.mark.parametrize("mutation", ["plan", "contract", "missing", "duplicate"])
def test_supplement_rejects_drift_without_falling_back(tmp_path, mutation):
    parent, plan = fixture(tmp_path)
    write_json(tmp_path / "plan/manifest.json", dict(jobs=[dict(ticker="FOREIGN")]))
    if mutation == "plan":
        plan["jobs"][0]["ticker"] = "CHANGED"
        write_json(tmp_path / "supplement-plan.json", plan)
    elif mutation == "contract":
        parent["contract"]["plan_hash"] = "changed"
    elif mutation == "missing":
        (tmp_path / "supplement-plan.json").unlink()
    else:
        plan["jobs"] *= 2
        parent["contract"]["plan_hash"] = digest(plan)
        parent["binding"] = digest(parent["contract"])
        write_json(tmp_path / "supplement-plan.json", plan)
    with pytest.raises((ValueError, FileNotFoundError)):
        frozen_jobs(tmp_path, 1, parent)
