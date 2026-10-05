"""Complete source closure and independent mutations for the frozen grid."""
import importlib
from pathlib import Path
import re

import pytest

from src.backend.backtest_fixed_v4_certification import (
    certify_numbered_fixed_v4_projection,
    certify_rising_momentum_entry_source,
)

ROOT = Path(__file__).parents[1]
HELPERS = (
    "src/trading_runtime/entry_momentum_growth.py",
    "src/backend/backtest_declared_initial_momentum.py",
)
VERSIONS = ((42, "forty_two"), (50, "fifty"), (59, "fifty_nine"),
            (60, "sixty"), (61, "sixty_one"))


def authority(number, name):
    module = importlib.import_module(
        "src.backend.backtest_strategy_" + name + "_certification")
    return (module, getattr(module, f"STRATEGY{number}_SOURCE_AST"),
            getattr(module, "certify_strategy_" + name + "_source"))


NEW_LEAVES = tuple(
    (number, name, path)
    for number, name in VERSIONS[2:]
    for path in authority(number, name)[1]
)


@pytest.mark.parametrize("number", (1, 42, 46, 47, 48, 49, 50, 51,
                                    52, 53, 54, 55, 56, 57, 58, 59, 60, 61))
def test_complete_native_proof(number):
    assert len(certify_numbered_fixed_v4_projection(number)) == 64


@pytest.mark.parametrize("number,name,path", NEW_LEAVES)
def test_every_new_complete_source_leaf_rejects_mutation(tmp_path, number, name, path):
    _, leaves, certify = authority(number, name)
    assert len(leaves) == 60
    assert set(HELPERS) <= set(leaves)
    changed = tmp_path / Path(path).name
    changed.write_text((ROOT / path).read_text(encoding="utf8")
                       + "\n_SOURCE_AUTHORITY_MUTATION = True\n", encoding="utf8")
    with pytest.raises(ValueError, match="pinned source changed: " + re.escape(path)):
        certify(source_overrides={path: changed})


@pytest.mark.parametrize("number,name", VERSIONS[:2])
@pytest.mark.parametrize("path", HELPERS)
def test_old_native_proof_seals_each_shared_helper(tmp_path, monkeypatch, number, name, path):
    module, leaves, certify = authority(number, name)
    assert path in leaves
    changed = tmp_path / Path(path).name
    changed.write_text((ROOT / path).read_text(encoding="utf8")
                       + "\n_SOURCE_AUTHORITY_MUTATION = True\n", encoding="utf8")
    monkeypatch.setattr(module, "certify_strategy_" + name + "_source",
                        lambda: certify(source_overrides={path: changed}))
    with pytest.raises(ValueError, match="pinned.*source changed"):
        certify_numbered_fixed_v4_projection(number)


@pytest.mark.parametrize("number,name", VERSIONS)
def test_unknown_source_override_is_outside_exact_closure(tmp_path, number, name):
    _, _, certify = authority(number, name)
    with pytest.raises(ValueError, match="outside reviewed authority"):
        certify(source_overrides={"src/unselected_foreign_source.py": tmp_path / "unused"})


@pytest.mark.parametrize("path", HELPERS)
def test_shared_rising_closure_independently_rejects_helper_mutation(tmp_path, path):
    changed = tmp_path / Path(path).name
    changed.write_text((ROOT / path).read_text(encoding="utf8")
                       + "\n_SOURCE_AUTHORITY_MUTATION = True\n", encoding="utf8")
    with pytest.raises(ValueError, match="reviewed source authority changed"):
        certify_rising_momentum_entry_source(
            source_overrides={path.removeprefix("src/"): changed})


def test_shared_rising_closure_rejects_unknown_override(tmp_path):
    with pytest.raises(ValueError, match="outside reviewed authority"):
        certify_rising_momentum_entry_source(source_overrides={"unselected.py": tmp_path / "unused"})
