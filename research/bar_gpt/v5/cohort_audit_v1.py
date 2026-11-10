"""Bounded completion audit for the complete v6 training cohort.

Every receipt seal, declared feature count and final run counter is checked.
Headers, first/middle/last records and SHA256 are checked on payloads stratified
by member, timeframe, year and liquidity. Training still verifies every accessed
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
    """Audit all publication metadata and targeted content using 32 bounded I/O lanes.

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
        evidence="all receipt seals and declared coverage; stratified headers/content/SHA256 by member/frame/year/liquidity; every consumed file and record checked by loader",
    )
    for name, root in components:
        wrapper = json.loads((root / "contract.json").read_text())
        if digest(wrapper["contract"]) != wrapper["binding"]:
            raise ValueError("Completion contract seal mismatch")
        completion = json.loads((root / "result.json").read_text())
        # The original base result omits the byte target; its sealed contract
        # remains the authority for the allocation, for both producer formats.
        target = wrapper["contract"]["configuration"]["target_bytes"]
        if "target_bytes" in completion and completion["target_bytes"] != target:
            raise ValueError("Completion target differs from sealed allocation")
        if completion["bytes"] < target * 0.95:
            raise ValueError(
                f"{name}: completed inventory falls short of intended corpus size"
            )
        paths = sorted(root.glob("jobs/*/*/receipt.json"))
        frames, features = Counter(), np.zeros(18, dtype=np.int64)
        samples = files = inspected = 0
        representatives = {}

        def inspect(path):
            receipt = json.loads(path.read_text())
            if receipt["binding"] != wrapper["binding"] or receipt[
                "receipt_hash"
            ] != digest({k: v for k, v in receipt.items() if k != "receipt_hash"}):
                raise ValueError(f"Foreign or corrupt publication receipt: {path}")
            if sum(receipt["frames"].values()) != receipt["samples"] or any(
                int(f) not in FRAMES and n for f, n in receipt["frames"].items()
            ):
                raise ValueError(f"Inconsistent timeframe counts: {path}")
            for item in receipt["files"]:
                if Path(item["name"]).name != item["name"] or not item[
                    "name"
                ].startswith("samples-"):
                    raise ValueError(f"Invalid payload identity: {path}")
            counts = np.asarray(receipt["valid_bars_by_channel"], dtype=np.int64)
            if counts.shape != (18,) or not np.all(
                counts[:6] == receipt["samples"] * 64
            ):
                raise ValueError(f"Incomplete declared trade evidence: {path}")
            if receipt.get("excluded_no_price", 0) or receipt.get("padded_bars", 0):
                raise ValueError(
                    f"Unexpected exclusions/padding in observed-bar corpus: {path}"
                )
            return receipt, path, counts

        with ThreadPoolExecutor(32) as pool:
            for begin in range(0, len(paths), 1024):
                for local, (receipt, path, counts) in enumerate(
                    pool.map(inspect, paths[begin : begin + 1024])
                ):
                    index = begin + local
                    samples += receipt["samples"]
                    files += len(receipt["files"])
                    features += counts
                    frames.update({int(f): n for f, n in receipt["frames"].items()})
                    for frame, count in receipt["frames"].items():
                        if count:
                            representatives.setdefault(
                                (
                                    int(frame),
                                    receipt.get("year", -1),
                                    receipt.get("liquid", -1),
                                ),
                                path,
                            )
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
        for (frame, year, liquid), path in sorted(representatives.items()):
            # Target headers/content instead of opening every payload twice.
            # The loader still validates every consumed file and record.
            item = read_receipt(path, wrapper["binding"])[0]
            array = np.load(item["path"], mmap_mode="r", allow_pickle=False)
            try:
                ids = np.unique([0, len(array) // 2, len(array) - 1])
                rows = array[ids]
                validate_complete_market(rows)
                if not np.all(rows["timeframe_us"] == frame):
                    raise ValueError(
                        "Payload frame differs from publication declaration"
                    )
                inspected += len(rows)
            finally:
                array._mmap.close()
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
                frames={str(frame): count for frame, count in frames.items()},
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
