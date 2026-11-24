"""Fit V7 normalization exclusively on the existing certified training subset."""

import os

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import argparse
import time
from pathlib import Path
import numpy as np
import torch
from ..phase1_v11.data import verify
from ..phase1_v7.full_data import prepare_raw, gpu_inputs
from ..v5.shards.phase1_contract import write_json
from .transforms import coordinates, decode, raw_values, TRANSFORM_VERSION
from .eligibility import model_validity, reasons, POLICY


def prepare(data, output):
    """Stream training rows; save immutable calibration and a raw roundtrip audit.

    Every source-valid bar receives equal weight, including repeated windows.
    Validation never enters the fit. Existing split/manifest verification is reused.
    The format is versioned: statistics from earlier transforms are incompatible.
    """
    if not output.resolve().is_relative_to(Path("D:/TradingML/runtimes").resolve()):
        raise ValueError("Output must be under D:/TradingML/runtimes")
    if output.exists():
        raise FileExistsError("Refusing to overwrite calibration")
    started = time.monotonic()
    manifest = verify(data)
    rows = np.load(data / "train.npy", mmap_mode="r")
    # These three accumulators store count, sum and sum-of-squares for each
    # feature. Streaming them avoids loading millions of bars into memory.
    sums = torch.zeros(3, 27, dtype=torch.float64)
    maximum = 0.0
    exclusions = torch.zeros(4, dtype=torch.int64)
    for start in range(0, len(rows), 256):
        batch = prepare_raw(rows[start : start + 256], native=True)
        values, valid, _ = gpu_inputs(batch)
        low, high, missing = reasons(batch.raw, batch.valid)
        exclusions += torch.stack(((low | high | missing).sum(), low.sum(), high.sum(), missing.sum()))
        valid = model_validity(values, valid)
        code, mask = coordinates(values[..., :18].double(), valid[..., :18])
        features = torch.cat((code, values[..., 18:].double()), -1)
        mask = torch.cat((mask, valid[..., 18:]), -1)
        safe = torch.where(mask, features, 0)
        if not torch.isfinite(safe).all():
            raise ValueError("Nonfinite valid calibration feature; no silent sanitation")
        sums[0] += mask.sum((0, 1))
        sums[1] += safe.sum((0, 1))
        sums[2] += safe.square().sum((0, 1))
        restored = raw_values(decode(code))
        error = ((restored - batch.raw).abs() / batch.raw.abs().clamp_min(1))[valid[..., :18]]
        if error.numel():
            maximum = max(maximum, float(error.max()))
    if not (sums[0] > 0).all() or maximum > 2e-5:
        raise ValueError("Insufficient coverage or failed native roundtrip")
    mean = sums[1] / sums[0]
    scale = (sums[2] / sums[0] - mean.square()).clamp_min(0).sqrt()
    # Floors protect constant fields, without clipping any input observation.
    scale = torch.maximum(scale, torch.tensor([1e-5] * 18 + [0.001] * 9))
    result = dict(
        version=TRANSFORM_VERSION,
        data_hash=manifest["hash"],
        split_hash=manifest["source_split_hash"],
        files=manifest["files"],
        rows=len(rows),
        bars=len(rows) * 64,
        counts=sums[0].tolist(),
        parameters=dict(
            mean=mean.tolist(), scale=scale.tolist(), transform_version=TRANSFORM_VERSION
        ),
        roundtrip_max_relative_over_max_one=maximum,
        seconds=time.monotonic() - started,
        policy="Training subset only; uniform bar exposures; no sample weights",
        eligibility_policy=POLICY,
        excluded_bars=int(exclusions[0]),
        excluded_reason_counts=dict(below_floor=int(exclusions[1]), above_ceiling=int(exclusions[2]), no_price=int(exclusions[3])),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset"),
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    torch.set_num_threads(2)
    prepare(args.data, args.output)
