"""Prove that compact metadata does not alter payloads, order or random masks."""

from dataclasses import fields
import pickle

import numpy as np
import pytest
import torch

from research.bar_gpt.v5.data_v1 import EligibleStream, make_loader, close_loader
from research.bar_gpt.v5.loader_index_v1 import attach, Entries
from research.bar_gpt.v5.test_engineering_v1 import inventory


def test_index_preserves_shuffle_and_has_small_spawn_state(inventory, tmp_path):
    manifest, _ = inventory
    indexed = attach(dict(manifest), tmp_path)
    for epoch in range(4):
        for split in ("train", "validation"):
            expected = list(manifest["splits"][split])
            if split == "train":
                np.random.default_rng(17 + epoch).shuffle(expected)
            actual = Entries(
                indexed["loader_index"][split], 17 + epoch if split == "train" else None
            )
            assert actual.sample_count() == manifest["counts"][split]
            for row, target in zip(actual[:], expected):
                assert row == {key: target[key] for key in row}
            encoded = pickle.dumps(actual)
            assert len(encoded) < 2048 + 8 * len(actual)
            restored = pickle.loads(encoded)
            assert list(restored[:]) == list(actual[:])
    assert manifest["manifest_hash"] == indexed["manifest_hash"]


@pytest.mark.parametrize("split", ["train", "validation"])
def test_real_spawned_batches_masks_and_tails_are_identical(inventory, tmp_path, split):
    manifest, stats = inventory
    indexed = attach(dict(manifest), tmp_path)
    loaders = [
        make_loader(EligibleStream(m, split, stats, 25, epoch=3), workers=2)
        for m in (manifest, indexed)
    ]
    try:
        first, second = list(loaders[0]), list(loaders[1])
        assert len(first) == len(second)
        assert sum(len(batch.visible) for batch in first) == manifest["counts"][split]
        for old, new in zip(first, second):
            for field in fields(old):
                assert torch.equal(getattr(old, field.name), getattr(new, field.name))
    finally:
        for loader in loaders:
            close_loader(loader)


def test_changed_metadata_index_fails_closed(inventory, tmp_path):
    manifest, _ = inventory
    indexed = attach(dict(manifest), tmp_path)
    with open(indexed["loader_index"]["train"]["path"], "r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"x")
    with pytest.raises(ValueError, match="index changed"):
        attach(dict(manifest), tmp_path)
