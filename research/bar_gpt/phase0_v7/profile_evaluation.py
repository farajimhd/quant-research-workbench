"""Compare all metrics and evaluation throughput with a pinned previous snapshot.

Run with an idle GPU and a stopped, preserved checkpoint. Neither checkpoint nor
training state is modified. Reports belong under the workstation runtime root.
"""
import os
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
import argparse
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import torch
from .model import Autoencoder, Config
from .metrics import Metrics
from .execution import EvaluationReplay
from .data import forward_batch
from .train import evaluate
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v11.pipeline import loader, DeviceBatches
from ..v5.data_v3 import load_split, close_loader
from ..v5.loader_index_v1 import attach
from ..v5.shards.phase1_contract import write_json, file_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True, help="Previous metrics.py")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(Path("D:/TradingML/runtimes").resolve()):
        raise ValueError("Runtime output required")
    args.output.mkdir(exist_ok=False)
    torch.set_num_threads(4)
    spec = importlib.util.spec_from_file_location("research.bar_gpt.phase0_v7.reference_metrics", args.reference)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    saved = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    parameters = dict(mean=saved["model"]["mean"], scale=saved["model"]["scale"], **saved["model_extra"])
    model = Autoencoder(parameters, Config(**saved["contract"]["config"])).cuda().eval()
    model.load_state_dict({**saved["model"], "_extra_state": saved["model_extra"]})
    root = Path("D:/TradingML/runtimes/bar_gpt")
    rows = np.load(root / "phase1-v8-learned-mae-20261117/dataset/development.npy", mmap_mode="r")
    batches = [prepare_raw(rows[i:i+1024], native=True).to("cuda") for i in range(0, len(rows), 1024)]
    expected, actual = reference.Metrics("cuda"), Metrics("cuda")
    with torch.no_grad():
        replay = EvaluationReplay(model, batches[0], actual)
        for batch in batches:
            out, values, valid = forward_batch(model, batch)
            expected.add(model, out, values, valid, batch)
            if len(batch.target) == 1024:
                replay(batch)
            else:
                actual.add(model, out, values, valid, batch)
        a, b = expected.result(), actual.result()
    assert a["metrics"].keys() == b["metrics"].keys()
    for key, value in a["metrics"].items():
        if value is None:
            assert b["metrics"][key] is None
        else:
            np.testing.assert_allclose(b["metrics"][key], value, rtol=1e-8, atol=1e-7, err_msg=key)
    write_json(args.output / "parity.json", dict(passed=True, metrics=len(a["metrics"]), windows=len(rows),
               checkpoint_sha256=file_hash(args.checkpoint), reference_sha256=file_hash(args.reference)))
    del replay, expected, actual, batches
    manifest = attach(load_split(root / "phase1-v6-cohort-balanced-v4/split.json"),
                      Path(load_split(root / "phase1-v6-cohort-balanced-v4/split.json")["dataset"]))
    manifest["splits"] = {}
    settings = SimpleNamespace(output=args.output, batch_size=4096, validation_batch_size=1024,
        workers=8, validation_workers=8, prefetch=4, seed=17, preparation="raw_gpu",
        shard_prefetch=16, shard_prefetch_mib=64, device="cuda", graph_validation=True, log_every=100)
    results = []
    # Same batch boundaries keep the existing batch-weighted objective comparable.
    for label, cls in (("previous", reference.Metrics), ("grouped", Metrics)):
        source = loader(manifest, settings, "validation")
        start = time.perf_counter()
        device = DeviceBatches(source, "cuda")
        metric, replay = cls("cuda"), None
        try:
            for step in range(210):
                if step == 10:
                    torch.cuda.synchronize()
                    startup = time.perf_counter() - start
                    start, wait = time.perf_counter(), device.wait_seconds
                batch = device.pop()
                if replay is None:
                    replay = EvaluationReplay(model, batch, metric)
                replay(batch)
                device.advance()
            torch.cuda.synchronize()
            seconds = time.perf_counter()-start
            result = dict(mode=label, windows_per_second=200*1024/seconds, seconds=seconds,
                          startup_seconds=startup, loader_wait_seconds=device.wait_seconds-wait)
            results.append(result)
            print(result, flush=True)
        finally:
            device.close()
            close_loader(source)
        del metric, replay
    class Reporter:
        def update(self, **kwargs): pass
        def log(self, values, step): pass
    for repeat in range(3):
        result = evaluate(model, manifest, settings, Reporter(), 0, rows)
        results.append(dict(mode=f"development_cache_{repeat}", seconds=result["seconds"],
                            windows_per_second=result["windows_per_second"]))
    write_json(args.output / "profiles.json", dict(results=results))
    print(results, flush=True)


if __name__ == "__main__":
    main()
