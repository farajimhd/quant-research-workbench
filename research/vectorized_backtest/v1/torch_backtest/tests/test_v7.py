"""V6 slot projection, masks, units, source binding and real CPU/CUDA replay."""

import json
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from research.rl_trading.v1.common import digest
from research.rl_trading.v6.bank import write_bank
from research.rl_trading.v6.features import (
    LEVEL_NAMES,
    SCALAR_NAMES,
    VERSION,
    CandleFeatures,
)
from research.rl_trading.v6.split import role
from research.vectorized_backtest.v1.strategy_encoding import (
    Funnel,
    Parameter,
    Program,
    Unit,
    clickhouse,
)
from research.vectorized_backtest.v1.strategy_encoding import (
    Instruction as I,
)
from research.vectorized_backtest.v1.strategy_encoding import (
    Operation as O,
)
from research.vectorized_backtest.v1.strategy_encoding.core import EncodingError
from research.vectorized_backtest.v1.strategy_encoding.tests.test_pipeline import (
    prepared_fixture,
)
from research.vectorized_backtest.v1.torch_backtest import (
    ReplayRunner,
    arte_catalog,
    compile_strategy,
    describe,
    to_tensors,
    v7,
)
from research.vectorized_backtest.v1.torch_backtest.examples import v7_example
from research.vectorized_backtest.v1.torch_backtest.export import to_frames
from research.vectorized_backtest.v1.torch_backtest.reference import evaluate_reference

DEVICES = ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])


def level_fixture(strategy_ms=1000):
    _, prepared = prepared_fixture(strategy_ms, 1000)
    clocks = prepared.features[1000]["time_us"].to_numpy().copy()
    scalar = np.zeros((3, len(SCALAR_NAMES)), dtype=np.float32)
    scalar[:, SCALAR_NAMES.index("log_close")] = np.log(10.0)
    levels = np.zeros((3, 2, 5, len(LEVEL_NAMES)), dtype=np.float32)
    for side in range(2):
        for slot in range(5):
            center = (slot + 1) * 0.02 * (-1 if side == 0 else 1)
            levels[:, side, slot, :3] = [center, center - 0.01, center + 0.01]
            levels[:, side, slot, 3:6] = np.log1p([20, 2, 60])
            levels[:, side, slot, 6 + side] = 1
            levels[:, side, slot, 9:] = 1
    item = CandleFeatures(clocks, scalar, levels)
    bank = SimpleNamespace(listing=lambda identity: item if identity == "A" else None)
    return prepared, item, bank


def test_all_ten_slots_atomic_units_and_presence_contract():
    prepared, item, bank = level_fixture()
    catalog = arte_catalog()
    atoms = tuple(f for f in catalog.inputs if f.source == "v7")
    assert len(atoms) == 170 and len({f.label for f in catalog.inputs}) == len(
        catalog.inputs
    )
    projected = v7.attach_level_features(prepared, bank, atoms, certificate="dummy")
    frame = projected.features[1000]
    for side in range(2):
        for slot in range(5):
            prefix = f"v7_{v7.SIDES[side]}_{slot}"
            np.testing.assert_allclose(
                frame[f"{prefix}_center_price"],
                10 * (1 + item.levels[:, side, slot, 0]),
                rtol=1e-7,
            )
            np.testing.assert_allclose(
                frame[f"{prefix}_center_distance_bps"],
                10000 * item.levels[:, side, slot, 0],
                rtol=1e-7,
            )
    assert describe(catalog)["v7_contract"]["side_order"] == ["below/equal", "above"]
    names = {f.name: f for f in atoms}
    assert names["v7.below[4].present"].valid_column is None
    assert names["v7.above[3].log_observation_count"].unit == Unit.RATIO
    assert names["v7.above[0].center_distance_rel"].unit == Unit.RETURN
    assert names["v7.above[0].center_distance_bps"].unit == Unit.BPS
    assert names["v7.below[0].lower_price"].unit == Unit.PRICE


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize("strategy_ms", [100, 1000])
def test_level_policy_windows_stops_targets_and_captured_theta(device, strategy_ms):
    prepared, _, bank = level_fixture(strategy_ms)
    program, catalog = v7_example(1)
    strategy = compile_strategy(program, catalog)
    prepared = v7.attach_level_features(
        prepared, bank, strategy.dependencies, certificate="dummy"
    )
    tape = to_tensors(prepared, strategy, device=device)
    runner = ReplayRunner(
        strategy, tape, values=[[1, 10, 0.1, 0.995], [3, 10, 0.1, 0.995]]
    )
    result = runner.run()
    assert result["filled_shares"].cpu().tolist() == [4, 0]
    for candidate in range(2):
        oracle = evaluate_reference(
            replace(program, values=tuple(runner.theta[candidate].cpu().tolist())),
            catalog,
            prepared,
            runner.broker,
        )
        accounts, state = to_frames(result, tape, candidate=candidate)
        np.testing.assert_allclose(
            accounts["equity"], oracle["accounts"]["equity"], atol=1e-7, rtol=0
        )
        if candidate == 0:
            assert state["stop"][0] == pytest.approx(9.7 * 0.995, abs=1e-6)
            assert state["target"][0] == pytest.approx(10.2, abs=1e-6)
    if device == "cuda" and strategy_ms == 1000:
        captured = ReplayRunner(
            strategy,
            tape,
            values=[[1, 10, 0.1, 0.995], [3, 10, 0.1, 0.995]],
            backend="compiled_graph",
        )
        torch.testing.assert_close(
            captured.run()["accounts"], result["accounts"], atol=1e-7, rtol=0
        )
        captured.set_parameters([[3, 10, 0.1, 0.99], [1, 10, 0.1, 0.99]])
        assert captured.run()["filled_shares"].cpu().tolist() == [0, 4]


@pytest.mark.parametrize("device", DEVICES)
def test_empty_slot_is_known_false_and_geometry_is_unknown_not_carried(device):
    prepared, item, bank = level_fixture()
    item.levels[1, 1, 0] = 0  # Disappearing nearest-above slot at candle two.
    program, catalog = v7_example(1)
    strategy = compile_strategy(program, catalog)
    prepared = v7.attach_level_features(
        prepared, bank, strategy.dependencies, certificate="dummy"
    )
    tape = to_tensors(prepared, strategy, device=device)
    labels = {f.name: tape.labels.index(f.label) for f in strategy.dependencies}
    assert tape.market[2, 0, labels["v7.above[0].present"]].item() == 0
    assert torch.isnan(tape.market[2, 0, labels["v7.above[0].center_distance_bps"]])
    bank = tape.windows[("v7", 1000)]
    window = bank.gather(torch.tensor([2], device=device), tape.start_us + 2_000_000, 2)
    assert torch.isnan(window[0, 0, 0]) and window[0, 0, 1].item() == pytest.approx(200)
    # No future candle can change accounts already produced through candle two.
    before = ReplayRunner(strategy, tape).run()["accounts"][:3].clone()
    item.levels[2, 1, 0, 0:3] = [0.2, 0.19, 0.21]
    future = v7.attach_level_features(
        replace(
            prepared,
            features={
                1000: prepared.features[1000].drop(
                    [c for c in prepared.features[1000].columns if c.startswith("v7_")]
                )
            },
        ),
        SimpleNamespace(listing=lambda _: item),
        strategy.dependencies,
        certificate="dummy-tail",
    )
    after = ReplayRunner(strategy, to_tensors(future, strategy, device=device)).run()[
        "accounts"
    ][:3]
    torch.testing.assert_close(before, after, rtol=0, atol=0)


def test_v7_input_guards_and_log_unit_constraints():
    prepared, item, bank = level_fixture()
    program, catalog = v7_example(1)
    strategy = compile_strategy(program, catalog)
    with pytest.raises(EncodingError, match="require v6_day_root"):
        v7.prepare_session(prepared.config, Funnel(), strategy.dependencies)
    item.close_us[0] += 1
    with pytest.raises(EncodingError, match="aligned"):
        v7.attach_level_features(
            prepared, bank, strategy.dependencies, certificate="dummy"
        )
    label = next(
        f.label for f in catalog.inputs if f.name == "v7.above[0].log_observation_count"
    )
    invalid = arte_catalog((Parameter("count", Unit.COUNT, 0, 100),))
    with pytest.raises(EncodingError, match="unit"):
        compile_strategy(
            Program.encode(
                [I(O.PARAMETER, parameter=0), I(O.GREATER, label, -1), I(O.EXIT, -2)],
                (10,),
            ),
            invalid,
        )


def test_empty_listing_price_mismatch_and_projection_memory_guard():
    prepared, item, bank = level_fixture()
    p, c = v7_example(1)
    strategy = compile_strategy(p, c)
    with pytest.raises(MemoryError, match="prepared-session"):
        v7.attach_level_features(
            replace(prepared, config=replace(prepared.config, max_prepared_gib=1e-12)),
            bank,
            strategy.dependencies,
            certificate="dummy",
        )
    item.scalar[0, SCALAR_NAMES.index("log_close")] = np.log(9.0)
    with pytest.raises(EncodingError, match="close differs"):
        v7.attach_level_features(
            prepared, bank, strategy.dependencies, certificate="dummy"
        )
    empty = CandleFeatures(item.close_us[:0], item.scalar[:0], item.levels[:0])
    prepared = v7.attach_level_features(
        prepared,
        SimpleNamespace(listing=lambda _: empty),
        strategy.dependencies,
        certificate="dummy-empty",
    )
    result = ReplayRunner(strategy, to_tensors(prepared, strategy)).run()
    assert result["filled_shares"].tolist() == [0]
    assert result["objective"].tolist() == [0.0]


def certify_fixture(root, item, source, *, previous=None):
    day = date(2026, 8, 18) if previous else date(2026, 7, 30)
    plan = {
        "version": VERSION,
        "day": str(day),
        "split_role": role(day),
        "previous_day": "2026-07-30" if previous else None,
        "previous_build_id": source["build_id"] if previous else None,
        "source_build_id": source["build_id"],
        "source_definition_hash": source["definition_hash"],
        "source_units_hash": digest(source["units"]),
        "census": {"A": 3},
    }
    plan["hash"] = digest(plan)
    root.mkdir()
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    bank = write_bank(root / "bank", {"A": 3}, [("A", item)], source_hash=plan["hash"])
    (root / "complete.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "version": VERSION,
                "plan_hash": plan["hash"],
                "bank_file_hashes": bank["files_sha256"],
            }
        ),
        encoding="utf-8",
    )


def test_certified_day_loader_source_binding_and_corruption(tmp_path, monkeypatch):
    prepared, item, _ = level_fixture()
    source = {
        "build_id": "build",
        "definition_hash": "definition",
        "units": {"day": "pinned-attempts"},
    }
    manifest = tmp_path / "market.json"
    manifest.write_text('{"fixture":true}', encoding="utf-8")
    prepared = replace(prepared, config=replace(prepared.config, manifest=manifest))
    previous, current = tmp_path / "previous", tmp_path / "current"
    prior = replace(item, close_us=item.close_us - 19 * 86_400_000_000)
    certify_fixture(previous, prior, source)
    certify_fixture(current, item, source, previous=previous)
    monkeypatch.setattr(clickhouse.arte_source, "load_build", lambda *args: source)
    calls = []

    def load_market(config, funnel, dependencies, **kwargs):
        calls.append(dependencies)
        assert all(f.source != "v7" for f in dependencies)
        return replace(prepared, dependencies=dependencies)

    monkeypatch.setattr(v7, "prepare_market", load_market)
    p, c = v7_example(1)
    strategy = compile_strategy(p, c)
    kwargs = {
        "v6_day_root": current,
        "v6_previous_root": previous,
        "v6_runtime_root": tmp_path,
        "v7_cache": False,
    }
    loaded = v7.prepare_session(
        prepared.config, Funnel(), strategy.dependencies, **kwargs
    )
    assert loaded.metrics["v7"]["rows"] == 3 and len(calls) == 1
    assert loaded.source_key != prepared.source_key
    # Build a retained copy after full certification, then require byte/seal
    # checks on reuse. No upstream arrays are substituted by unverified data.
    cached_kwargs = {**kwargs, "v7_cache": True}
    first_copy = v7.prepare_session(
        prepared.config, Funnel(), strategy.dependencies, **cached_kwargs
    )
    assert not first_copy.metrics["v7_cache"]["reused"]
    reuse = v7.prepare_session(
        prepared.config, Funnel(), strategy.dependencies, **cached_kwargs
    )
    assert reuse.metrics["v7_cache"]["reused"]
    assert reuse.features[1000].equals(first_copy.features[1000])
    assert reuse.source_key == first_copy.source_key
    from research.vectorized_backtest.v1.torch_backtest.v7_cache import (
        cache_path,
        producer_seals,
    )

    folder, _ = cache_path(
        prepared.config,
        Funnel(),
        strategy.dependencies,
        producer_seals(current, previous, tmp_path),
    )
    seal = json.loads((folder / "complete.json").read_text())
    cache_file = folder / seal["file"]
    with cache_file.open("r+b") as stream:
        stream.write(b"BROKEN")
    with pytest.raises(EncodingError, match="Corrupt V7 projection"):
        v7.prepare_session(
            prepared.config, Funnel(), strategy.dependencies, **cached_kwargs
        )
    calls_before_mismatch = len(calls)
    source["build_id"] = "wrong-build"
    with pytest.raises(EncodingError, match="same pinned"):
        v7.prepare_session(prepared.config, Funnel(), strategy.dependencies, **kwargs)
    assert len(calls) == calls_before_mismatch  # Mismatch fails before fetching.
    source["build_id"] = "build"
    array = np.load(current / "bank" / "levels.npy", mmap_mode="r+")
    array[0, 0, 0, 0] = 0.5
    array.flush()
    del array
    with pytest.raises(ValueError, match="hash mismatch"):
        v7.prepare_session(prepared.config, Funnel(), strategy.dependencies, **kwargs)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA graph test")
@pytest.mark.parametrize("graph_steps", [2, 3, 8])
@pytest.mark.parametrize("strategy_ms", [100, 1000])
def test_multi_tick_graph_exact_tail_and_parameter_updates(graph_steps, strategy_ms):
    prepared, _, bank = level_fixture(strategy_ms)
    p, c = v7_example(1)
    strategy = compile_strategy(p, c)
    prepared = v7.attach_level_features(
        prepared, bank, strategy.dependencies, certificate="dummy"
    )
    tape = to_tensors(prepared, strategy, device="cuda")
    values = [[1, 10, 0.1, 0.995], [3, 10, 0.1, 0.995]]
    baseline = ReplayRunner(strategy, tape, values=values).run()
    captured = ReplayRunner(
        strategy, tape, values=values, backend="compiled_graph", graph_steps=graph_steps
    )
    actual = captured.run()
    torch.testing.assert_close(
        actual["accounts"], baseline["accounts"], rtol=0, atol=1e-7
    )
    for key, value in baseline["state"].items():
        torch.testing.assert_close(actual["state"][key], value, rtol=0, atol=1e-7)
    steps = min(graph_steps, tape.market.shape[0])
    assert actual["graph_launches"] == (tape.market.shape[0] + steps - 1) // steps
    captured.set_parameters(list(reversed(values)))
    assert captured.run()["filled_shares"].cpu().tolist() == [0, 4]


def test_shared_source_receipt_rejects_wrong_day_changed_manifest_and_forgery(
    tmp_path, monkeypatch
):
    _, prepared = prepared_fixture()
    manifest = tmp_path / "market.json"
    manifest.write_text('{"test":true}', encoding="utf-8")
    config = replace(prepared.config, manifest=manifest)
    source = {"build_id": "build", "definition_hash": "hash", "units": {}}
    reads = []

    def load(*args):
        reads.append(args)
        return source

    monkeypatch.setattr(clickhouse.arte_source, "load_build", load)
    receipt = clickhouse.certify_source(config)
    assert len(reads) == 1
    for invalid in (
        replace(receipt, day=date(2026, 8, 17)),
        replace(receipt, _authority=None),
    ):
        with pytest.raises(EncodingError, match="receipt differs"):
            clickhouse.prepare_session(config, Funnel(), (), source_receipt=invalid)
    manifest.write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(EncodingError, match="receipt differs"):
        clickhouse.prepare_session(config, Funnel(), (), source_receipt=receipt)
    assert len(reads) == 1
