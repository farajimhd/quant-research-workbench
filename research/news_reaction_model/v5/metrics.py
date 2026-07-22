from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import torch
import torch.nn.functional as F

from research.news_reaction_model.v5.model import NewsReactionRangeOutput
from research.news_reaction_model.v5.ranges import TARGET_NAMES, range_targets


@dataclass(slots=True)
class _Stats:
    count: int = 0
    correct: int = 0
    within_one: int = 0
    log_loss: float = 0.0
    confidence_sum: float = 0.0


@dataclass(slots=True)
class RangeAccumulator:
    stats: dict[str, _Stats] = field(default_factory=lambda: defaultdict(_Stats))

    @torch.no_grad()
    def add(self, output: NewsReactionRangeOutput, returns: torch.Tensor, mask: torch.Tensor) -> None:
        targets = range_targets(returns, mask)
        for horizon, horizon_targets in targets.items():
            for target_index, target_name in enumerate(TARGET_NAMES):
                actual = horizon_targets[:, target_index]
                valid = actual >= 0
                if not bool(valid.any()):
                    continue
                logits = output.logits[horizon][target_name][valid].float()
                actual = actual[valid]
                probabilities = torch.softmax(logits, dim=-1)
                predicted = logits.argmax(dim=-1)
                confidence = probabilities.gather(1, predicted.unsqueeze(1)).squeeze(1)
                key = f"{horizon}/{target_name}"
                stat = self.stats[key]
                stat.count += int(actual.numel())
                stat.correct += int((predicted == actual).sum().cpu())
                stat.within_one += int(((predicted - actual).abs() <= 1).sum().cpu())
                stat.log_loss += float(F.cross_entropy(logits, actual, reduction="sum").cpu())
                stat.confidence_sum += float(confidence.sum().cpu())

    def compute(self, prefix: str = "val") -> dict[str, float]:
        result: dict[str, float] = {}
        total_count = total_correct = total_within_one = 0
        total_log_loss = total_confidence = 0.0
        for key, stat in sorted(self.stats.items()):
            count = max(stat.count, 1)
            result[f"{prefix}/{key}/samples"] = float(stat.count)
            result[f"{prefix}/{key}/accuracy"] = stat.correct / count
            result[f"{prefix}/{key}/within_one_bin_accuracy"] = stat.within_one / count
            result[f"{prefix}/{key}/log_loss"] = stat.log_loss / count
            result[f"{prefix}/{key}/mean_confidence"] = stat.confidence_sum / count
            total_count += stat.count
            total_correct += stat.correct
            total_within_one += stat.within_one
            total_log_loss += stat.log_loss
            total_confidence += stat.confidence_sum
        denominator = max(total_count, 1)
        result[f"{prefix}/samples"] = float(total_count)
        result[f"{prefix}/accuracy"] = total_correct / denominator
        result[f"{prefix}/within_one_bin_accuracy"] = total_within_one / denominator
        result[f"{prefix}/log_loss"] = total_log_loss / denominator
        result[f"{prefix}/mean_confidence"] = total_confidence / denominator
        return result
