"""FIFO parity, bounded lookahead and fail-closed verification for real NPYs."""

from dataclasses import fields
import hashlib
import threading
import numpy as np
import pytest
import torch

from .shard_prefetch_v3 import VerifiedShardPrefetch
from .data_v3 import EligibleStream, validate_complete_market
from .pretraining_v3 import LocalReferencePolicy
from .test_encoder_v3 import raw


def files(tmp_path, counts=(3, 7, 2, 9)):
    entries = []
    for i, n in enumerate(counts):
        path = tmp_path / f"samples-{i}.npy"
        rows = raw(n)
        rows["values"][:, :, 0] += i * 100
        np.save(path, rows, allow_pickle=False)
        entries.append(
            dict(
                path=str(path),
                start=0,
                stop=n,
                bytes=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        )
    return entries


@pytest.mark.parametrize("full", [False, True])
def test_every_prepared_tensor_and_partial_tail_matches_synchronous(tmp_path, full):
    entries = files(tmp_path)
    manifest = dict(
        dataset_version="fixture", splits={"train": entries, "validation": entries}
    )
    outputs = []
    for depth in (0, 1, 32):
        d = EligibleStream(
            manifest, "train", LocalReferencePolicy(), 4, seed=17, epoch=3
        )
        d.full_validation = full
        d.shard_prefetch = depth
        outputs.append(list(d))
    assert [len(x.visible) for x in outputs[0]] == [4, 4, 4, 4, 4, 1]
    for stream in outputs[1:]:
        assert len(stream) == len(outputs[0])
        for a, b in zip(stream, outputs[0]):
            for field in fields(a):
                assert torch.equal(
                    getattr(a, field.name), getattr(b, field.name)
                ), field.name


def test_fifo_order_byte_budget_and_readonly_arrays(tmp_path):
    entries = files(tmp_path)
    budget = max(e["bytes"] for e in entries) * 2
    reader = VerifiedShardPrefetch(
        entries, depth=32, byte_budget=budget, validator=validate_complete_market
    )
    output = list(reader)
    assert [e["path"] for e, _ in output] == [e["path"] for e in entries]
    assert reader.peak_pending_bytes <= budget
    assert reader.pending_bytes == 0
    assert all(not rows.flags.writeable for _, rows in output)
    for e, rows in output:
        assert len(rows) == e["stop"] - e["start"]


def test_oversized_unit_is_processed_not_skipped(tmp_path):
    entries = files(tmp_path, counts=(3, 4))
    reader = VerifiedShardPrefetch(entries, depth=32, byte_budget=1)
    output = list(reader)
    assert [len(rows) for _, rows in output] == [3, 4]
    assert reader.peak_pending_bytes == max(e["bytes"] for e in entries)


def test_source_hash_corruption_propagates(tmp_path):
    entries = files(tmp_path, counts=(3, 4))
    path = tmp_path / "samples-1.npy"
    data = bytearray(path.read_bytes())
    data[-2] ^= 1
    path.write_bytes(data)
    reader = VerifiedShardPrefetch(entries, depth=32)
    stream = iter(reader)
    assert next(stream)[0]["path"] == entries[0]["path"]
    with pytest.raises(ValueError, match="SHA256"):
        next(stream)


def test_repeated_path_must_retain_exact_binding(tmp_path):
    item = files(tmp_path, counts=(7,))[0]
    changed = dict(item, sha256="0" * 64)
    with pytest.raises(ValueError, match="binding"):
        list(VerifiedShardPrefetch([item, changed], depth=32))


def test_background_verifies_next_file_while_consumer_has_not_advanced(tmp_path):
    entries = files(tmp_path, counts=(3, 4))
    ready = threading.Event()

    class Reader(VerifiedShardPrefetch):
        def read(self, item):
            result = super().read(item)
            if item["path"] == entries[1]["path"]:
                ready.set()
            return result

    reader = Reader(entries, depth=2)
    stream = iter(reader)
    next(stream)
    assert ready.wait(2), "Next file was not loaded independently of consumer"
    stream.close()
    assert not reader.pending and reader.pending_bytes == 0


def test_complete_market_failure_is_not_hidden_by_prefetch(tmp_path):
    entries = files(tmp_path, counts=(3,))
    path = tmp_path / "samples-0.npy"
    rows = np.load(path, allow_pickle=False)
    rows["values"][0, 0, 0] = float("nan")
    np.save(path, rows, allow_pickle=False)
    entries[0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Invalid v6 sample"):
        list(VerifiedShardPrefetch(entries, validator=validate_complete_market))
