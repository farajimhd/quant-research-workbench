"""Read every published sample in a bounded-memory shard audit; never repair silently."""

import argparse
import ast
import json
from pathlib import Path
import time

import numpy as np

from .contract import FRAMES, SAMPLE_DTYPE, VERSION, write_json
from .storage import verify_receipt
from .sql import clock


def audit(root, output):
    """Validate final [N,64,21] records, full context bounds, hashes and nonoverlap.

    root is an immutable/pilot dataset directory. output is a runtime JSON path.
    One small mmap file is examined at a time. Intervals are compared separately
    per ticker-year and timeframe; different timeframes intentionally coexist.
    """
    root = Path(root)
    contract = json.loads((root / "contract.json").read_text())
    if contract["contract"]["version"] != VERSION:
        raise ValueError("Audit requires the consecutive-observed-bar dataset version.")
    began = time.perf_counter()
    total = excluded = receipts = payload_bytes = 0
    frames = {str(frame): 0 for frame in FRAMES}
    reporting = np.zeros(4, dtype=np.int64)
    last_end = {}
    gaps = padded = 0
    for receipt_path in sorted((root / "jobs").glob("*/*/receipt.json")):
        receipt = verify_receipt(receipt_path.parent, hashes=True)
        receipts += 1
        excluded += receipt.get("excluded_no_price", 0)
        if receipt.get("reporting_counts"):
            reporting += np.asarray(ast.literal_eval(receipt["reporting_counts"]), dtype=np.int64)
        counted = 0
        for item in receipt["files"]:
            if not item["name"].startswith("samples-"):
                raise ValueError("Intermediate bar files must not be persisted.")
            data = np.load(receipt_path.parent / item["name"], mmap_mode="r", allow_pickle=False)
            try:
                if data.dtype != SAMPLE_DTYPE or data.ndim != 1:
                    raise ValueError("Unexpected sample shape/type.")
                if not np.isfinite(data["values"]).all() or not np.isin(data["valid"], (0, 1)).all():
                    raise ValueError("Malformed values or evidence masks.")
                if not data["valid"][..., [3, 9, 15]].any(axis=(1, 2)).all():
                    raise ValueError("A published sample has no price reference.")
                price_columns = [0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15]
                if np.any((data["valid"][..., price_columns] != 0) & (data["values"][..., price_columns] <= 0)):
                    raise ValueError("Valid price masks reference nonpositive prices.")
                differences = np.diff(data["end_us"], axis=1)
                present = data["end_us"] > 0
                if (not data["valid"][..., :6][present].all()
                        or np.any(data["values"][..., :6][present] <= 0)):
                    raise ValueError("Real bar lacks complete positive trade attributes.")
                if (np.any(data["end_us"] < 0) or np.any(~present[:, 0])
                        or np.any(present[:, 1:] & ~present[:, :-1])
                        or np.any((differences <= 0) & present[:, 1:])
                        or not np.isin(data["timeframe_us"], FRAMES).all()):
                    raise ValueError("Nonmonotonic clocks or unknown timeframe.")
                if np.any(data["values"][~present]) or np.any(data["valid"][~present]):
                    raise ValueError("Nonzero padding values or evidence masks.")
                fine = data["timeframe_us"] < FRAMES[4]
                if fine.any():
                    real = data["end_us"][fine][present[fine]]
                    if np.any(real <= clock(receipt["day"], 4)) or np.any(real > clock(receipt["day"], 20)):
                        raise ValueError("Fine sample crosses its source session.")
                if not present[~fine].all():
                    raise ValueError("Hourly/daily contexts must contain 64 real bars.")
                padded += int((~present).sum())
                gaps += int((differences > data["timeframe_us"][:, None]).sum())
                starts = data["end_us"][:, 0] - data["timeframe_us"]
                ends = data["end_us"].max(axis=1)
                if int(starts.min()) != item["first_start_us"] or int(ends.max()) != item["last_end_us"]:
                    raise ValueError("Receipt context bounds disagree with payload.")
                for frame in FRAMES:
                    selected = np.flatnonzero(data["timeframe_us"] == frame)
                    if not len(selected):
                        continue
                    selected = selected[np.argsort(starts[selected])]
                    key = (receipt_path.parent.parent.name, frame)
                    if np.any(starts[selected][1:] < ends[selected][:-1]):
                        raise ValueError("Overlapping contexts within a timeframe/file.")
                    if int(starts[selected][0]) < last_end.get(key, 0):
                        raise ValueError("Overlapping contexts across shard boundaries.")
                    last_end[key] = int(ends[selected][-1])
                    frames[str(frame)] += len(selected)
                counted += len(data)
                payload_bytes += item["bytes"]
            finally:
                data._mmap.close()  # Release Windows file mappings promptly.
        if counted != receipt["samples"]:
            raise ValueError("Receipt count differs from final sample records.")
        total += counted
    result = dict(status="passed", receipts=receipts, samples=total, frames=frames,
                  excluded_price_empty_blocks=excluded, observed_clock_gaps=gaps,
                  padded_bars=padded,
                  reporting_counts=reporting.tolist(), payload_bytes=payload_bytes,
                  seconds=time.perf_counter()-began, dataset=str(root))
    write_json(output, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.dataset, arguments.output), indent=2))
