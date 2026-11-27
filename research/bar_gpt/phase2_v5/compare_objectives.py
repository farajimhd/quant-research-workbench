"""Compare forecast objectives from one stopped checkpoint, then continue the winner."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from ..v5.shards.contract import file_hash, write_json
from .model import HORIZONS, NAMES


# Hold the coordinate weight fixed and isolate price weighting from auxiliary
# supervision. All trials preserve the original batches and scheduler.
TRIALS = {
    "equal-baseline": ".5,.5,.1,0,0,0,0",
    "price-only": ".65,.35,.1,0,0,0,0",
    "equal-anchor20": ".5,.5,.1,.05,.05,.05,.20",
    "price-anchor05": ".65,.35,.1,.05,.05,.05,.05",
    "price-anchor20": ".65,.35,.1,.05,.05,.05,.20",
    "price-anchor50": ".65,.35,.1,.05,.05,.05,.50",
    "heavy-price-anchor20": ".8,.2,.1,.05,.05,.05,.20",
}


def mean(values):
    return sum(values) / len(values)


def score(validation):
    """Weight each timeframe equally, with trade closes primary for selection."""
    by_frame = {}
    for frame, width in zip(NAMES, HORIZONS):
        streams = {}
        for stream in ("trade", "bid", "ask"):
            slots = [f"{frame}/h{h:02d}/{stream}" for h in range(1, width+1)]
            comparable = [key for key in slots if validation.get(key+"/persistence_close_mae", 0) > 0]
            streams[stream] = dict(
                mae=mean([validation[key+"/comparable_close_mae"] for key in comparable]),
                persistence_mae=mean([validation[key+"/persistence_close_mae"] for key in comparable]),
                mae_ratio=mean([validation[key+"/comparable_close_mae"]/validation[key+"/persistence_close_mae"]
                                for key in comparable]),
                direction=mean([validation[key+"/from_last/balanced_accuracy_pct"] for key in comparable]))
        by_frame[frame] = streams
    return dict(frames=by_frame, direction=mean([x["trade"]["direction"] for x in by_frame.values()]),
                mae_ratio=mean([x["trade"]["mae_ratio"] for x in by_frame.values()]))


def compare(trial, baseline):
    trial["direction_gain_pp"] = trial["direction"] - baseline["direction"]
    trial["mae_change_pct"] = 100 * (trial["mae_ratio"]/baseline["mae_ratio"] - 1)
    trial["worst_close_mae_change_pct"] = max(
        100 * (row["mae"]/baseline["frames"][frame][stream]["mae"] - 1)
        for frame, streams in trial["frames"].items() for stream, row in streams.items())
    trial["eligible"] = (trial["mae_change_pct"] <= 1 and trial["worst_close_mae_change_pct"] <= 3
                         and (trial["direction_gain_pp"] >= .1
                              or (trial["direction_gain_pp"] >= -.2 and trial["mae_change_pct"] <= -.5)))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parent", type=Path, required=True)
    p.add_argument("--control", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--sessions", type=int, default=2048, choices=(256, 512, 1024, 2048, 4096))
    args = p.parse_args()
    base = Path("D:/TradingML/runtimes/bar_gpt").resolve()
    if (os.environ.get("COMPUTERNAME", "").upper() != "DESKTOP-SAAI85T"
            or any(path.resolve().parent != base for path in (args.parent, args.control, args.output))
            or args.output.exists()):
        raise ValueError("Use a distinct workstation run directory and its own control directory")
    args.control.mkdir()
    status = args.control / "status.json"
    old_process = json.loads((args.parent/"process.json").read_text())
    checkpoint = args.parent/"checkpoint_last.pt"
    proof = json.loads(checkpoint.with_suffix(".proof.json").read_text())
    # Reuse the audited trainer's actual launch settings, including precision,
    # data authority, batch shapes and the frozen teacher.
    command = [sys.executable, "-B", "-u", "-m", "research.bar_gpt.phase2_v5.run_train", *old_process["command"][5:]]
    if command.count("--from-completed-pass") != 1:
        raise ValueError("Expected the original unique-release continuation")
    index = command.index("--from-completed-pass")
    command[index:index+2] = ["--objective-fork", str(checkpoint)]
    command[command.index("--source-revision")+1] = args.revision

    def launch(tag, weights, *, trial):
        if ((base/"STOP").exists() or (args.control/"STOP").exists()
                or file_hash(checkpoint) != proof["sha256"]):
            raise RuntimeError("Campaign stopped or parent checkpoint changed")
        run = base / f"{args.output.name}-{tag}" if trial else args.output
        config = command.copy()
        config[config.index("--output")+1] = str(run)
        config += ["--loss-weights", weights]
        if trial:
            config += ["--profile-sessions", str(args.sessions), "--profile-full-validation"]
        write_json(status, dict(state="profiling" if trial else "training launch",
                                trial=tag, parent_step=proof["step"], command=config))
        with (args.control / f"{tag}.log").open("a", encoding="utf-8") as log:
            subprocess.run(config, check=True, stdout=log, stderr=subprocess.STDOUT)
        return run

    try:
        results = {}
        for tag, weights in TRIALS.items():
            run = launch(tag, weights, trial=True)
            validation = json.loads((run/"profile-validation.json").read_text())
            profile = json.loads((run/"profile.json").read_text())
            if results:
                same = results["equal-baseline"]["profile"]
                if any(profile[k] != same[k] for k in ("interval_origins", "updates", "validation_origins")):
                    raise ValueError("Trials used different training updates or validation origins")
            result = dict(**score(validation), profile=profile, weights=weights)
            if tag != "equal-baseline":
                compare(result, results["equal-baseline"])
            results[tag] = result
            write_json(args.control/"comparison.json", results)
        eligible = [tag for tag, row in results.items()
                    if row.get("eligible") and "anchor" in tag]
        if not eligible:
            write_json(status, dict(state="no qualified objective", parent_step=proof["step"]))
            return
        winner = max(eligible, key=lambda tag: (results[tag]["direction_gain_pp"], -results[tag]["mae_change_pct"]))
        write_json(args.control/"winner.json", dict(tag=winner, **results[winner]))
        launch(winner, TRIALS[winner], trial=False)
    except BaseException as error:
        write_json(status, dict(state="failed", error=repr(error), parent_step=proof["step"]))
        raise


if __name__ == "__main__":
    main()
