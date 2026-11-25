"""Paired ticker bootstrap for the width study's disjoint validation audit."""
import argparse
import json
import os
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
from .width_study import ValidationSessions, Forecast, score, train
from .model import HORIZONS
import numpy as np
import torch


def balanced_ratio(errors, persistence):
    """Reduce sums over tickers before giving every timeframe equal weight."""
    ratios = np.divide(errors,persistence,out=np.full_like(errors,np.nan),where=persistence>0)
    frames = np.split(ratios,np.cumsum(HORIZONS)[:-1],axis=0)
    return float(np.exp(np.mean([np.log(np.nanmean(frame)) for frame in frames])))


@torch.no_grad()
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--source-revision",required=True)
    p.add_argument("--candidate",type=Path,action="append",required=True)
    cli = p.parse_args()
    root = cli.output.parent
    manifest = json.loads((root/"manifest.json").read_text())
    args = argparse.Namespace(**{**manifest["arguments"],**vars(cli)})
    args.resume = None
    train.preflight(args,desktop_paths=(r"c:\windows\system32\applicationframehost.exe",
                                        r"c:\windows\immersivecontrolpanel\systemsettings.exe"))
    for name in ("teacher","calibration","release"):
        setattr(args,name,Path(getattr(args,name)))
    torch.set_num_threads(1)
    torch.set_float32_matmul_precision("highest")
    teacher, source = train.teacher_for(args)
    units, data = train.load_units(release=args.release)
    audit = ValidationSessions(units,True)
    train.write_json(args.output/"selection.json",audit.selected)
    parent = train.load_checkpoint(root/"checkpoint_last.pt",manifest["execution_model"])
    parent_model = Forecast()
    parent_model.load_state_dict(parent["model"])
    models = {"parent":parent_model.eval().cuda()}
    for path in cli.candidate:
        contract = json.loads((path/"trial.json").read_text())
        saved = train.load_checkpoint(path/"checkpoint_last.pt",contract)
        if (saved["step"]!=contract["updates"] or contract["teacher"]!=source or contract["data"]!=data["hash"]
                or contract["parent"]!=train.file_hash(root/"checkpoint_last.pt")):
            raise ValueError("Candidate lineage or training budget differs")
        model = Forecast(contract["frame_width"],contract["embedding"])
        model.load_state_dict(saved["model"])
        models[path.name] = model.eval().cuda()

    stats = {name:train.Metrics("cuda") for name in models}
    per_ticker = {}
    stream = train.device_sessions(train.sessions(audit,1,0,args.workers),teacher,args,metrics=True)
    closes = torch.tensor([3,9,15],device="cuda")
    try:
        for index,session,device in stream:
            if (args.output/"STOP").exists() or (root/"STOP").exists():
                raise InterruptedError("Audit stopped")
            if device is None:
                continue
            sums = torch.zeros(len(models)+1,sum(HORIZONS),3,device="cuda",dtype=torch.float64)
            for ids in torch.arange(len(session.origins),device="cuda").split(args.batch_size):
                windows,logs,valid,evidence = device.batch(ids,metrics=True)
                raw,anchor,anchor_valid = evidence
                target = raw.index_select(-1,closes)
                base = valid.index_select(-1,closes) & anchor_valid & (anchor>0)
                sums[-1] += torch.where(base,(target-anchor).abs(),0).double().sum(0)
                for j,(name,model) in enumerate(models.items()):
                    out = train.predict(model,teacher,windows,args)
                    stats[name].add(out,valid,evidence)
                    forecast = out["logs"].index_select(-1,closes).double().exp()
                    sums[j] += torch.where(base,(forecast-target).abs(),0).sum(0)
            ticker = audit[index].path.parent.name
            per_ticker.setdefault(ticker,np.zeros(sums.shape,dtype=np.float64))
            per_ticker[ticker] += sums.cpu().numpy()
    finally:
        stream.close()
    all_sums = np.stack(list(per_ticker.values()))
    np.savez_compressed(args.output/"ticker-errors.npz",tickers=list(per_ticker),models=list(models),sums=all_sums)
    rng = np.random.default_rng(9303)
    draws = rng.integers(len(all_sums),size=(2000,len(all_sums)))
    control = list(models).index("f64-e128")
    report = {}
    for i,name in enumerate(models):
        metrics = stats[name].result()
        if metrics["nonfinite"]:
            raise ValueError("Nonfinite audit prediction")
        summary = score(metrics)
        bootstrap = []
        for indices in draws:
            sample = all_sums[indices].sum(0)
            bootstrap.append(balanced_ratio(sample[i],sample[-1])/balanced_ratio(sample[control],sample[-1])-1)
        summary["relative_change_ci95"] = np.quantile(bootstrap,[.025,.975]).tolist()
        train.write_json(args.output/f"{name}-metrics.json",metrics)
        report[name] = summary
    train.write_json(args.output/"comparison.json",dict(sessions=len(audit.selected),tickers=len(per_ticker),models=report))


if __name__=="__main__":
    main()
