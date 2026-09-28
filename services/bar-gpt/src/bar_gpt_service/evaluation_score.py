"""Prediction-blind target join and interpretable horizon scorecards."""
from __future__ import annotations

import csv
import json
import math
import random
from contextlib import closing
import struct
from collections import defaultdict
from pathlib import Path

from .evaluation_data import read_packet, validate_dataset
from .evaluation_store import atomic_json, digest


def bps(value: float) -> float:
    return math.expm1(math.sinh(value) / 100.) * 10000.


class Metrics:
    def __init__(self):
        self.n = 0
        self.error = self.zero = self.momentum = self.covered = self.width = self.pinball = 0.
        self.momentum_n = self.crossings = 0
        self.momentum_model_error = 0.
        self.confusion = [[0] * 3 for _ in range(3)]

    def update(self, prediction: list[float], truth: float, quantiles: list[float], momentum: float | None):
        if len(prediction) != len(quantiles) or not all(math.isfinite(x) for x in [*prediction, truth]):
            raise ValueError("invalid forecast or target")
        median = prediction[quantiles.index(.5)]
        actual, predicted = bps(truth), bps(median)
        self.n += 1
        self.error += abs(predicted - actual)
        self.zero += abs(actual)
        if momentum is not None:
            self.momentum += abs(math.expm1(momentum) * 10000. - actual)
            self.momentum_n += 1
            self.momentum_model_error += abs(predicted - actual)
        low, high = bps(prediction[0]), bps(prediction[-1])
        self.covered += low <= actual <= high
        self.width += high - low
        self.crossings += any(a > b for a, b in zip(prediction, prediction[1:]))
        self.pinball += sum(max(q * (truth - p), (q - 1) * (truth - p)) for q, p in zip(quantiles, prediction)) / len(quantiles)
        # Match float32 native target-space thresholds; don't reclassify decoded roundoff.
        f32 = lambda value: struct.unpack("f", struct.pack("f", value))[0]
        lo = f32(math.asinh(math.log1p(-.0001)*100))
        hi = f32(math.asinh(math.log1p(.0001)*100))
        classify = lambda value: 0 if value < lo else 2 if value > hi else 1
        self.confusion[classify(truth)][classify(median)] += 1

    def result(self):
        n = self.n
        actual = [sum(row) for row in self.confusion]
        predicted = [sum(self.confusion[i][j] for i in range(3)) for j in range(3)]
        correct = sum(self.confusion[i][i] for i in range(3))
        denominator = math.sqrt((n*n - sum(x*x for x in actual)) * (n*n - sum(x*x for x in predicted)))
        recalls = [self.confusion[i][i] / actual[i] for i in range(3) if actual[i]]
        return {"support": n, "mae_bps": self.error/n, "zero_mae_bps": self.zero/n,
                "skill_vs_zero": 1 - self.error/self.zero if self.zero else None,
                "momentum_mae_bps": self.momentum/self.momentum_n if self.momentum_n else None,
                "momentum_support": self.momentum_n,
                "model_mae_on_momentum_support_bps": self.momentum_model_error/self.momentum_n if self.momentum_n else None,
                "skill_vs_momentum": 1-self.momentum_model_error/self.momentum if self.momentum else None,
                "interval_coverage": self.covered/n, "interval_width_bps": self.width/n,
                "quantile_crossings": self.crossings, "pinball_transformed": self.pinball/n,
                "balanced_accuracy_present_classes": sum(recalls)/len(recalls),
                "mcc": (correct*n - sum(a*p for a, p in zip(actual, predicted)))/denominator if denominator else None,
                "confusion": self.confusion}


def day_interval(deltas: list[float]) -> dict:
    if len(deltas) < 5:
        return {"days": len(deltas), "confidence_interval": None, "reason": "fewer than five days"}
    rng = random.Random(20260928)
    boot = sorted(sum(rng.choices(deltas, k=len(deltas)))/len(deltas) for _ in range(2000))
    return {"days": len(deltas), "mean_baseline_minus_model_bps": sum(deltas)/len(deltas),
            "confidence_interval": [boot[49], boot[1949]], "method": "paired equal-day bootstrap, 2000 replicates"}


def score(dataset_root: Path, run: Path, allow_partial: bool = False) -> dict:
    plan, dataset = validate_dataset(dataset_root)
    experiment = json.loads((run / "experiment.json").read_text())
    if digest(experiment["manifest"]) != experiment["identity"] or experiment["manifest"]["dataset_hash"] != digest(dataset):
        raise RuntimeError("run/dataset identity mismatch")
    is_acceptance = plan["manifest"]["partition"] == "acceptance"
    if is_acceptance and allow_partial:
        raise ValueError("partial acceptance reports are forbidden")
    # Read-only: scoring cannot alter predictions or replay eligibility.
    predictions = read_packet(run / "results.sqlite")
    expected = sum(row["origins"] for row in dataset["packets"])
    count = predictions.execute("SELECT count(*) FROM predictions").fetchone()[0]
    if count != expected and not allow_partial:
        predictions.close()
        raise RuntimeError(f"incomplete predictions: {count}/{expected}; pilot scoring needs --allow-partial")
    from research.bar_gpt.v3.targets import RETURN_TARGET_NAMES
    horizons = dataset["packets"][0]["horizons_us"]
    quantiles = experiment["manifest"].get("quantiles", [.1, .5, .9])
    groups = defaultdict(Metrics)
    masked = defaultdict(int)
    joined = 0
    try:
        for packet in dataset["packets"]:
            with closing(read_packet(dataset_root / packet["file"])) as targets:
                for origin, payload in predictions.execute(
                    "SELECT origin,payload FROM predictions WHERE day=? AND ticker=? ORDER BY origin",
                    (packet["day"], packet["ticker"])):
                    row = json.loads(payload)
                    if row["checkpoint_hash"] != experiment["manifest"]["release"]["checkpoint_hash"]:
                        raise RuntimeError("wrong checkpoint in prediction journal")
                    found = targets.execute("SELECT payload FROM targets WHERE origin=?", (origin,)).fetchone()
                    if found is None:
                        raise RuntimeError("prediction without an eligible target origin")
                    target = json.loads(found[0])
                    joined += 1
                    for hi, horizon in enumerate(horizons):
                        for ti, name in enumerate(RETURN_TARGET_NAMES):
                            if not target["mask"][hi][ti]:
                                masked[f"{horizon}/{name}"] += 1
                                continue
                            momentum = (target["momentum_log_return"][hi]
                                        if name == "trade_close_return" and target["momentum_log_return"] else None)
                            for group in ("all", "day:" + packet["day"], "ticker:" + packet["ticker"]):
                                groups[(group, horizon, name)].update(row["raw"]["horizon_quantiles"][hi][ti],
                                                                   target["values"][hi][ti], quantiles, momentum)
    finally:
        predictions.close()
    if joined != count:
        raise RuntimeError("unreconciled predictions outside dataset population")
    rows = [{"group": group, "horizon_seconds": horizon/1e6, "target": name, **metric.result()}
            for (group, horizon, name), metric in sorted(groups.items())]
    path = run / "scorecard.csv"
    if rows:
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    intervals = {}
    for horizon in horizons:
        daily = [value.result() for (group, h, name), value in groups.items()
                 if group.startswith("day:") and h == horizon and name == "trade_close_return"]
        intervals[str(horizon//1_000_000)] = day_interval([v["zero_mae_bps"] - v["mae_bps"] for v in daily])
    report = {"status": "pilot_partial" if count != expected else "complete", "expected": expected, "predictions": count,
              "missing": expected-count, "masked_targets": dict(masked), "day_intervals": intervals,
              "scope": "physical OHLC return heads; no profitability claim",
              "unscored": ["autoregressive heads", "condition targets", "volume/count/volatility and availability heads"],
              "scorecard": str(path), "experiment_identity": experiment["identity"]}
    atomic_json(run / "score_summary.json", report)
    lines = ["# BarGPT evaluation", "", f"Status: {report['status']}; predictions {count:,}/{expected:,}.", "",
             "| Horizon | Close MAE (bps) | No-change MAE | Skill | MCC | Interval coverage |",
             "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        if row["group"] == "all" and row["target"] == "trade_close_return":
            skill = f"{row['skill_vs_zero']:.1%}" if row['skill_vs_zero'] is not None else "undefined"
            mcc = f"{row['mcc']:.4f}" if row['mcc'] is not None else "undefined"
            lines.append(f"| {row['horizon_seconds']:g}s | {row['mae_bps']:.3f} | {row['zero_mae_bps']:.3f} | {skill} | {mcc} | {row['interval_coverage']:.1%} |")
    lines.extend(["", "Positive skill means lower error than no change. Calibration must be read with interval width and crossing counts.",
                  "Confidence intervals resample whole days, not overlapping origins. No live or trading approval is implied."])
    (run / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
