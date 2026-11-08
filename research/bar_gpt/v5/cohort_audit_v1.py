"""Bounded completion audit for the complete v6 training cohort.

Every receipt, file size/header, declared feature count and final run counter is
checked. First/middle/last records of every payload are inspected; stratified
payloads are SHA-256 verified in full. Training still verifies every accessed
payload and every consumed record. This is not a claim of a second full-byte
scan of 1.5 TB before training.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import numpy as np

from .cohort_v1 import members
from .data_v1 import read_receipt, validate_complete_market
from .shards.phase1_contract import CHANNELS, FRAMES, digest, file_hash, write_json


def audit(dataset, output, progress=None):
    """Audit all immutable publications and stratified content using eight I/O lanes.

    dataset is the base root that resolves both completed cohort members.
    output is a runtime JSON report. Validity masks may legitimately be zero
    for unavailable quotes; every one of the 18 channels must have real evidence
    somewhere in each member. Trades must be complete in every inspected bar.
    """
    seal, components = members(dataset, require_all=True)
    if len(components) != 2:
        raise ValueError("Overnight training requires both base and liquid supplement")
    report = dict(
        cohort_hash=seal,
        members=[],
        evidence="all receipts/headers; three records per payload; stratified full SHA256; every consumed record checked by loader",
    )
    for name, root in components:
        wrapper = json.loads((root / "contract.json").read_text())
        if digest(wrapper["contract"]) != wrapper["binding"]:
            raise ValueError("Completion contract seal mismatch")
        completion = json.loads((root / "result.json").read_text())
        if completion["bytes"] < completion["target_bytes"] * 0.95:
            raise ValueError(
                f"{name}: completed inventory falls short of intended corpus size"
            )
        paths = sorted(root.glob("jobs/*/*/receipt.json"))
        frames, features = Counter(), np.zeros(18, dtype=np.int64)
        samples = files = inspected = 0
        representatives = {}

        def inspect(path):
            receipt = json.loads(path.read_text())
            entries = read_receipt(path, wrapper["binding"])
            counts = np.asarray(receipt["valid_bars_by_channel"], dtype=np.int64)
            if counts.shape != (18,) or not np.all(
                counts[:6] == receipt["samples"] * 64
            ):
                raise ValueError(f"Incomplete declared trade evidence: {path}")
            if receipt.get("excluded_no_price", 0) or receipt.get("padded_bars", 0):
                raise ValueError(
                    f"Unexpected exclusions/padding in observed-bar corpus: {path}"
                )
            checked = 0
            for item in entries:
                array = np.load(item["path"], mmap_mode="r", allow_pickle=False)
                try:
                    ids = np.unique([0, len(array) // 2, len(array) - 1])
                    rows = array[ids]
                    validate_complete_market(rows)
                    if not np.isin(rows["timeframe_us"], FRAMES).all():
                        raise ValueError("Unexpected timeframe in published payload")
                    checked += len(rows)
                finally:
                    array._mmap.close()
            return receipt, entries, counts, checked

        with ThreadPoolExecutor(8) as pool:
            for begin in range(0, len(paths), 256):
                for local, (receipt, entries, counts, checked) in enumerate(
                    pool.map(inspect, paths[begin : begin + 256])
                ):
                    index = begin + local
                    samples += receipt["samples"]
                    files += len(entries)
                    inspected += checked
                    features += counts
                    frames.update({int(f): n for f, n in receipt["frames"].items()})
                    for frame, count in receipt["frames"].items():
                        if count:
                            representatives.setdefault(int(frame), entries[0])
                    if progress and index % 1000 == 0:
                        progress(stage="auditing " + name, done=index, total=len(paths))
        if samples != completion["samples"] or any(
            frames[f] != completion["frames"].get(str(f), 0) for f in FRAMES
        ):
            raise ValueError(
                "Published inventory does not reconcile to completed run counters"
            )
        if any(frames[f] <= 0 for f in FRAMES) or np.any(features <= 0):
            raise ValueError("Missing timeframe or market feature coverage")
        hashes = []
        for frame, item in sorted(representatives.items()):
            if file_hash(item["path"]) != item["sha256"]:
                raise ValueError("Stratified payload SHA256 mismatch")
            hashes.append(dict(frame=frame, path=item["path"], sha256=item["sha256"]))
        report["members"].append(
            dict(
                name=name,
                binding=wrapper["binding"],
                samples=samples,
                bytes=completion["bytes"],
                files=files,
                receipts=len(paths),
                inspected_records=inspected,
                frames=dict(frames),
                channel_valid_bars=dict(zip(CHANNELS, features.tolist())),
                verified_payloads=hashes,
            )
        )
    report.update(
        state="passed",
        samples=sum(m["samples"] for m in report["members"]),
        bytes=sum(m["bytes"] for m in report["members"]),
    )
    report["audit_hash"] = digest(report)
    write_json(output, report)
    return report
