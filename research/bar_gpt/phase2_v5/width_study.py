"""Paired, bounded capacity trials branching from a verified Phase 2 checkpoint."""
import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path

os.environ.update(PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
import numpy as np
import torch
from . import run_train as train
from .model import Forecast, HORIZONS, NAMES
from ..phase2_v2.release import ReleasedUnits
from ..v5.shards.contract import digest, file_hash, write_json


@torch.no_grad()
def widen(parent, frame_width, embedding):
    """Start with the parent's forecast. New frame channels initially have zero outgoing weights.

    Replicating the context channels preserves LayerNorm statistics. Unequal
    outgoing shares sum to one and let replicas receive different gradients.
    """
    model = Forecast(frame_width, embedding)
    repeats = embedding // 128
    if embedding != repeats * 128 or repeats < 1 or frame_width < 64:
        raise ValueError("Expected an integer multiple of 128 and at least 64 summary channels")
    model.compress.weight[:64].copy_(parent.compress.weight)
    model.compress.bias[:64].copy_(parent.compress.bias)
    fused = model.context[0]
    fused.weight.zero_()
    fused.weight.view(embedding, 5, frame_width)[:, :, :64].copy_(
        parent.context[0].weight.reshape(128, 5, 64).repeat(repeats, 1, 1))
    fused.bias.copy_(parent.context[0].bias.repeat(repeats))
    shares = torch.arange(1, repeats + 1, dtype=torch.float32)
    shares /= shares.sum()

    def linear(dest, src, output_repeats):
        weights = torch.cat([src.weight * fraction for fraction in shares], dim=1)
        dest.weight.copy_(weights.repeat(output_repeats, 1))
        dest.bias.copy_(src.bias.repeat(output_repeats))

    def norm(dest, src):
        dest.weight.copy_(src.weight.repeat(repeats))
        dest.bias.copy_(src.bias.repeat(repeats))

    for i in (2, 3):
        dst, src = model.context[i].branch, parent.context[i].branch
        norm(dst[0], src[0])
        linear(dst[1], src[1], repeats)
        linear(dst[3], src[3], repeats)
    norm(model.context[4], parent.context[4])
    linear(model.heads, parent.heads, 1)
    return model


class ValidationSessions(ReleasedUnits):
    """Select validation rows while preserving the full authority for successor lookup."""
    def __init__(self, units, audit):
        super().__init__(units.groups)
        candidates, offset = [], 0
        for _, _, table in self.groups:
            rows = table.with_row_index("row").filter(table["unit_split"] == 1)
            for i, path, receipt in rows.select("row", "path", "receipt_hash").iter_rows():
                ticker = path.replace(chr(92), "/").split("/")[1]
                group = hashlib.sha256(ticker.encode()).digest()[0] % 2
                if group == int(audit):
                    candidates.append((offset + i, receipt))
            offset += table.height
        rng = np.random.default_rng(5198 + int(audit))
        indices = rng.choice(len(candidates), min(len(candidates), 128), replace=False)
        self.selected = [candidates[i] for i in indices]
        if not self.selected:
            raise ValueError("Empty validation partition")

    def iter_from(self, split, start=0):
        if split != 1 or start:
            raise ValueError("Validation-only view")
        for i, _ in self.selected:
            yield i, self[i]


def score(report):
    """Equal timeframe weights; each horizon contributes the trade/bid/ask close MAE ratio."""
    metrics = {}
    for frame, width in zip(NAMES, HORIZONS):
        ratios, directions = [], []
        for h in range(1, width + 1):
            for side in ("trade", "bid", "ask"):
                key = f"{frame}/h{h:02}/{side}/"
                baseline = report.get(key + "persistence_close_mae", 0)
                if baseline > 0:
                    ratios.append(report[key + "comparable_close_mae"] / baseline)
                    directions.append(report[key + "from_last/balanced_accuracy_pct"])
        if not ratios:
            raise ValueError(f"No comparable forecasts for {frame}")
        metrics[frame + "_ratio"] = float(np.mean(ratios))
        metrics[frame + "_direction"] = float(np.mean(directions))
    metrics["ratio"] = math.exp(np.mean([math.log(metrics[f + "_ratio"]) for f in NAMES]))
    metrics["direction"] = float(np.mean([metrics[f + "_direction"] for f in NAMES]))
    return metrics


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source-revision", required=True)
    p.add_argument("--frame-width", type=int, choices=(64,128,256), required=True)
    p.add_argument("--embedding", type=int, choices=(128,256,512), required=True)
    p.add_argument("--audit", type=Path, help="Verify and evaluate a completed trial on disjoint tickers")
    trial = p.parse_args()
    root = trial.output.parent
    parent_manifest = json.loads((root / "manifest.json").read_text())
    args = argparse.Namespace(**{**parent_manifest["arguments"], **vars(trial)})
    args.resume = None
    train.preflight(args, desktop_paths=(r"c:\windows\system32\applicationframehost.exe",
                                         r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    for name in ("teacher","calibration","release"):
        setattr(args, name, Path(getattr(args,name)))
    torch.set_num_threads(1)
    torch.manual_seed(719)
    torch.set_float32_matmul_precision("highest")
    parent = train.load_checkpoint(root/"checkpoint_last.pt", parent_manifest["execution_model"])
    teacher, provenance = train.teacher_for(args)
    units, identity = train.load_units(release=args.release)
    if provenance != parent["contract"]["teacher"] or identity != parent["contract"]["data"]:
        raise ValueError("Parent teacher or training data changed")
    subset = ValidationSessions(units, bool(args.audit))
    parent_model = Forecast()
    parent_model.load_state_dict(parent["model"])
    model = widen(parent_model, args.frame_width, args.embedding)
    probe = torch.randn(8,5,64,32)
    torch.testing.assert_close(model(probe),parent_model(probe),rtol=2e-5,atol=3e-5)
    model.cuda()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-5, fused=True)
    contract = dict(source=args.source_revision,parent=file_hash(root/"checkpoint_last.pt"),teacher=provenance,
                    data=identity["hash"],ordering=identity["ordering_hash"],cursor=parent["cursor"],
                    frame_width=args.frame_width,embedding=args.embedding,lr=1e-5,seed=719,
                    updates=2000,dev_hash=digest(ValidationSessions(units,False).selected),
                    parameters=sum(x.numel() for x in model.parameters()))
    write_json(args.output/"selection.json",subset.selected)
    write_json(args.output/"trial.json",contract)
    train.load_env_files(train.discover_env_files(Path.cwd()),verbose=False)
    os.environ.setdefault("WANDB_ENTITY","mehdifaraji")
    reporter = train.Reporter(args.output, dict(model=contract, audit=bool(args.audit)),
                              mode="online", project="bargpt-phase2")
    def evaluate(step):
        report = train.evaluate(subset,model,teacher,args,len(subset.selected))
        if report is None:
            raise InterruptedError("Evaluation stopped")
        summary = score(report)
        write_json(args.output/f"evaluation-{step}.json",dict(summary=summary,report=report))
        reporter.log({**{"forecast/"+k:v for k,v in summary.items()},"forecast/loss":report["loss"]},step)
        return summary

    try:
        if args.audit:
            saved = train.load_checkpoint(args.audit/"checkpoint_last.pt",contract)
            model.load_state_dict(saved["model"])
            evaluate(saved["step"])
        else:
            run_updates(args,units,teacher,model,opt,parent,contract,reporter,evaluate)
    except BaseException as exc:
        reporter.finish("failed",repr(exc))
        raise
    reporter.finish("complete")


def run_updates(args,units,teacher,model,opt,parent,contract,reporter,evaluate):
    first, offset = parent["cursor"]
    stream = train.device_sessions(train.sessions(units,0,first,args.workers),teacher,args,metrics=False)
    gradients = train.ForecastGradients(model,teacher,args)
    step = count = 0
    elapsed = 0.
    traversal = hashlib.sha256()
    start = time.perf_counter()
    try:
        for index, session, device in stream:
            if (args.output/"STOP").exists() or (args.output.parent/"STOP").exists():
                raise InterruptedError("Trial stopped")
            if device is None:
                continue
            gen = torch.Generator(device="cuda").manual_seed(51771+index)
            order = torch.randperm(len(session.origins),device="cuda",generator=gen)
            pos = offset if index == first else 0
            for ids in order[pos:].split(args.batch_size):
                if not len(ids):
                    continue
                inputs,logs,valid,_ = device.batch(ids)
                opt.zero_grad(set_to_none=True)
                loss = gradients(inputs,logs,valid)
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
                if not bool(torch.isfinite(loss) & torch.isfinite(norm)):
                    raise FloatingPointError("Nonfinite update")
                opt.step()
                step += 1
                count += len(ids)
                pos += len(ids)
                traversal.update(f"{index}/{pos};".encode())
                if step%100==0:
                    reporter.log({"train/loss":float(loss),"train/origins":count},step)
                if step in (500,1250,2000):
                    elapsed += time.perf_counter()-start
                    summary = evaluate(step)
                    cursor = (index+1,0) if pos==len(order) else (index,pos)
                    train.save_checkpoint(args.output/"checkpoint_last.pt",dict(model=model.state_dict(),
                        optimizer=opt.state_dict(),rng=train.rng_state(),contract=contract,step=step,cursor=cursor))
                    write_json(args.output/"result.json",dict(step=step,origins=count,rate=count/elapsed,
                        traversal=traversal.hexdigest(),summary=summary))
                    start = time.perf_counter()
                if step==contract["updates"]:
                    return
    finally:
        stream.close()
    raise RuntimeError("Training selection exhausted")


if __name__=="__main__":
    main()
