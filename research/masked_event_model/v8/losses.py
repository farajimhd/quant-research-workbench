from __future__ import annotations

import time
from dataclasses import dataclass

import torch
import torch.nn.functional as F

from research.masked_event_model.v8.config import LossConfig
from research.masked_event_model.v8.model import EventMAEOutput


BYTE_VALUE_BIT_WEIGHTS = torch.tensor([1, 2, 4, 8, 16, 32, 64, 128], dtype=torch.float32)
MAX_SEMANTIC_BIT_WEIGHT = float(BYTE_VALUE_BIT_WEIGHTS[-1])
BYTE_MAX_VALUE = 255.0
EVENT_BITS_PER_SAMPLE = 16 * 8
PSNR_EPSILON = 1e-12


def build_semantic_event_bit_weights() -> torch.Tensor:
    """Return fixed loss weights for `[event_byte, bit]`.

    Numeric bytes use the same little-endian bit significance as
    `unpack_bits`: bit 0 has weight 1 and bit 7 has weight 128. Bytes that pack
    several unrelated categorical/flag fields do not have a meaningful numeric
    ordering, so every bit in those bytes receives the maximum weight. That
    makes errors on event type, flags, exchange IDs, and condition IDs expensive
    even when the changed bit is numerically low-order inside its byte.
    """

    numeric_byte_weights = BYTE_VALUE_BIT_WEIGHTS.tolist()
    packed_or_categorical = [MAX_SEMANTIC_BIT_WEIGHT] * 8
    return torch.tensor(
        [
            packed_or_categorical,  # byte 0: event type, presence flag, correction code.
            numeric_byte_weights,  # byte 1: event time bucket, low byte.
            numeric_byte_weights,  # byte 2: event time bucket, high byte.
            numeric_byte_weights,  # byte 3: price delta 1, low byte.
            numeric_byte_weights,  # byte 4: price delta 1, high byte.
            numeric_byte_weights,  # byte 5: price delta 2, low byte.
            numeric_byte_weights,  # byte 6: price delta 2, high byte.
            numeric_byte_weights,  # byte 7: primary size bucket.
            numeric_byte_weights,  # byte 8: secondary size bucket.
            packed_or_categorical,  # byte 9: odd-lot flags plus tape code.
            packed_or_categorical,  # byte 10: primary exchange dense ID.
            packed_or_categorical,  # byte 11: secondary exchange dense ID.
            packed_or_categorical,  # byte 12: condition 1 presence plus dense ID.
            packed_or_categorical,  # byte 13: condition 2 presence plus dense ID.
            packed_or_categorical,  # byte 14: condition 3 presence plus dense ID.
            packed_or_categorical,  # byte 15: condition 4 presence plus dense ID.
        ],
        dtype=torch.float32,
    )


SEMANTIC_EVENT_BIT_WEIGHTS = build_semantic_event_bit_weights()
_BIT_LOOKUP_CACHE: dict[tuple[str, torch.dtype], torch.Tensor] = {}


@dataclass(slots=True)
class LossResult:
    """Loss tensor plus detached scalar metrics for logs/W&B."""

    # Shape: []. Scalar differentiable objective used for `backward()`.
    loss: torch.Tensor
    # Shape: dict[str, float]. Detached scalar metrics for logs and W&B.
    metrics: dict[str, float]


def masked_event_bce_loss(
    output: EventMAEOutput,
    config: LossConfig,
    *,
    header_uint8: torch.Tensor | None = None,
    include_diagnostics: bool = False,
    profile_metrics: bool = False,
    metric_level: str = "standard",
) -> LossResult:
    """Reconstruct masked event bytes as independent bit logits.

    The compact sample cache stores bytes, but the objective is binary: each of
    the 16 event bytes is unpacked into 8 target bits. We use
    `binary_cross_entropy_with_logits` so the decoder can return stable raw
    logits during training; probabilities are derived only for metrics.
    """

    logits = output.event_bit_logits
    target_bytes = output.target_events_uint8
    target_bits = unpack_bits(target_bytes).to(dtype=logits.dtype, device=logits.device)
    raw_semantic_weights = SEMANTIC_EVENT_BIT_WEIGHTS.to(device=logits.device, dtype=logits.dtype).view(1, 1, 16, 8)
    # Scale each semantic bit by the total numeric byte significance. For a
    # numeric byte this makes the eight bit weights sum to one:
    # (1 + 2 + ... + 128) / 255 = 1. Packed/categorical bytes keep max
    # per-bit emphasis without multiplying the objective by the raw bit values.
    semantic_weight_normalizer = BYTE_VALUE_BIT_WEIGHTS.to(device=logits.device, dtype=logits.dtype).sum()
    semantic_weights = raw_semantic_weights / semantic_weight_normalizer
    objective = str(config.objective).lower()
    if objective not in {"weighted", "unweighted"}:
        raise ValueError(f"Unsupported loss objective {config.objective!r}; expected 'weighted' or 'unweighted'.")
    batch_size = max(1, int(logits.shape[0]))
    masked_events = max(1, int(logits.shape[1]))
    calculate_unweighted_metric = objective == "unweighted" or metric_level != "loss_only"
    unweighted_loss: torch.Tensor | None = None
    weighted_loss_mean: torch.Tensor | None = None
    weighted_term_count = int(logits.numel())
    if logits.is_cuda:
        with torch.amp.autocast("cuda", enabled=False):
            if objective == "unweighted":
                unweighted_loss = F.binary_cross_entropy_with_logits(logits.float(), target_bits.float())
                loss = unweighted_loss
            else:
                weighted_loss_mean = F.binary_cross_entropy_with_logits(
                    logits.float(),
                    target_bits.float(),
                    weight=semantic_weights.float(),
                    reduction="mean",
                )
                loss = weighted_loss_mean
                if calculate_unweighted_metric:
                    unweighted_loss = F.binary_cross_entropy_with_logits(logits.float(), target_bits.float())
    else:
        if objective == "unweighted":
            unweighted_loss = F.binary_cross_entropy_with_logits(logits, target_bits)
            loss = unweighted_loss
        else:
            weighted_loss_mean = F.binary_cross_entropy_with_logits(
                logits,
                target_bits,
                weight=semantic_weights,
                reduction="mean",
            )
            loss = weighted_loss_mean
            if calculate_unweighted_metric:
                unweighted_loss = F.binary_cross_entropy_with_logits(logits, target_bits)
    loss = loss * float(config.event_weight)

    metrics_started = time.perf_counter()
    metrics = {
        "pretrain/loss_total": float(loss.detach().cpu()),
        "pretrain/loss_objective_weighted": float(objective == "weighted"),
        "pretrain/loss_event_semantic_weight_mean": float(semantic_weights.mean().detach().cpu()),
        "pretrain/loss_event_semantic_raw_weight_mean": float(raw_semantic_weights.mean().detach().cpu()),
        "pretrain/loss_event_semantic_normalizer": float(semantic_weight_normalizer.detach().cpu()),
        "pretrain/loss_event_weighted_terms": float(weighted_term_count),
        "pretrain/loss_event_weighted_terms_per_event": float(EVENT_BITS_PER_SAMPLE),
        "pretrain/loss_event_batch_size_normalizer": float(batch_size),
        "mask/event_mask_ratio_pct": float(output.actual_mask_ratio * 100.0),
        "mask/event_requested_mask_ratio_pct": float(output.requested_mask_ratio * 100.0),
        "mask/event_visible_events": float(output.visible_event_count),
        "mask/event_masked_events": float(output.masked_event_indices.shape[1]),
        "mask/event_count": float(output.event_count),
        "mask/event_mask_policy_id": float(output.mask_policy_id),
    }
    if unweighted_loss is not None:
        metrics["pretrain/loss_event_unweighted"] = float(unweighted_loss.detach().cpu())
    if weighted_loss_mean is not None:
        metrics["pretrain/loss_event_weighted_mean"] = float(weighted_loss_mean.detach().cpu())
        metrics["pretrain/loss_event_weighted_sum_estimate"] = float((weighted_loss_mean.detach() * weighted_term_count).cpu())
        metrics["pretrain/loss_event_weight_mass"] = float(weighted_term_count)
        metrics["pretrain/loss_event_masked_events_normalizer"] = float(masked_events)
    if metric_level == "loss_only":
        # Full reconstruction metrics are useful, but they are not free at large
        # batch sizes. The training loop can request loss-only steps and reserve
        # detailed metrics for shard/validation boundaries.
        if profile_metrics:
            if logits.is_cuda:
                torch.cuda.synchronize(logits.device)
            metrics["profile/event_metrics_seconds"] = time.perf_counter() - metrics_started
            metrics["profile/metrics_seconds"] = metrics["profile/event_metrics_seconds"]
        return LossResult(loss=loss, metrics=metrics)

    with torch.no_grad():
        # Cheap metrics answer the first question during long runs: are the
        # reconstructed bits better than random and are complete bytes becoming
        # exact? More detailed metrics below are gated by `metric_level`.
        probabilities = torch.sigmoid(logits.float())
        hard_bits = probabilities >= 0.5
        target_bool = target_bits.bool()
        bit_acc = (hard_bits == target_bool).float().mean()
        hard_bytes = pack_bits(hard_bits)
        target_bytes_long = target_bytes.long()
        exact = (hard_bytes == target_bytes_long).float().mean()
        confidence = (probabilities - 0.5).abs() * 2.0
        metrics.update(
            {
            "pretrain/event_bit_acc_pct": float(bit_acc.detach().cpu() * 100.0),
            "pretrain/event_byte_exact_acc_pct": float(exact.detach().cpu() * 100.0),
            "pretrain/event_bit_conf_mean": float(confidence.mean().detach().cpu()),
            "mask/event_masked_bytes": float(target_bytes.numel()),
            "mask/total_masked_bytes": float(target_bytes.numel()),
            }
        )
        if metric_level != "cheap":
            # Baselines are calculated from the current masked targets, not from
            # a global prior. That makes the lift metrics interpretable even when
            # sampled shards have different byte distributions.
            target_one_rate = target_bits.float().mean()
            pred_one_rate = hard_bits.float().mean()
            majority_baseline = torch.maximum(target_one_rate, 1.0 - target_one_rate)
            one_mask = target_bool
            zero_mask = ~target_bool
            one_acc = (hard_bits[one_mask] == target_bool[one_mask]).float().mean() if one_mask.any() else probabilities.new_tensor(0.0)
            zero_acc = (hard_bits[zero_mask] == target_bool[zero_mask]).float().mean() if zero_mask.any() else probabilities.new_tensor(0.0)
            balanced_bit_acc = (one_acc + zero_acc) * 0.5 if one_mask.any() and zero_mask.any() else bit_acc
            target_float = target_bytes.float()
            hard_mae = (hard_bytes.float() - target_float).abs().mean()
            soft_bytes = (probabilities.float() * BYTE_VALUE_BIT_WEIGHTS.to(probabilities.device)).sum(dim=-1)
            soft_mae = (soft_bytes - target_float).abs().mean()
            mode_count = torch.bincount(target_bytes_long.flatten(), minlength=256).max()
            byte_mode_baseline = mode_count.float() / target_bytes.numel()
            metrics.update(
                {
                    "pretrain/event_bit_majority_baseline_pct": float(majority_baseline.detach().cpu() * 100.0),
                    "pretrain/event_bit_acc_lift_pct": float((bit_acc - majority_baseline).detach().cpu() * 100.0),
                    "pretrain/event_balanced_bit_acc_pct": float(balanced_bit_acc.detach().cpu() * 100.0),
                    "pretrain/event_zero_bit_acc_pct": float(zero_acc.detach().cpu() * 100.0),
                    "pretrain/event_one_bit_acc_pct": float(one_acc.detach().cpu() * 100.0),
                    "pretrain/event_target_one_rate_pct": float(target_one_rate.detach().cpu() * 100.0),
                    "pretrain/event_pred_one_rate_pct": float(pred_one_rate.detach().cpu() * 100.0),
                    "pretrain/event_byte_mode_baseline_pct": float(byte_mode_baseline.detach().cpu() * 100.0),
                    "pretrain/event_byte_exact_lift_pct": float((exact - byte_mode_baseline).detach().cpu() * 100.0),
                    "pretrain/event_hard_byte_mae": float(hard_mae.detach().cpu()),
                    "pretrain/event_soft_byte_mae": float(soft_mae.detach().cpu()),
                    "pretrain/event_bit_conf_min": float(confidence.min().detach().cpu()),
                }
            )
            if include_diagnostics:
                hard_mse = (hard_bytes.float() - target_float).pow(2).mean()
                soft_mse = (soft_bytes - target_float).pow(2).mean()
                metrics["pretrain/event_hard_byte_psnr_db"] = float(byte_psnr_db(hard_mse).detach().cpu())
                metrics["pretrain/event_soft_byte_psnr_db"] = float(byte_psnr_db(soft_mse).detach().cpu())
            if header_uint8 is not None:
                metrics.update(masked_event_semantic_metrics(header_uint8, target_bytes, hard_bytes))
        if profile_metrics:
            if logits.is_cuda:
                torch.cuda.synchronize(logits.device)
            metrics["profile/event_metrics_seconds"] = time.perf_counter() - metrics_started
            metrics["profile/metrics_seconds"] = metrics["profile/event_metrics_seconds"]
    return LossResult(loss=loss, metrics=metrics)


def unpack_bits(values: torch.Tensor) -> torch.Tensor:
    """Expand uint8 bytes into little-endian bit targets/probability axes."""

    lookup = bit_lookup(values.device, torch.float32)
    return lookup[values.long()]


def bit_lookup(device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    """Return a cached `[256, 8]` little-endian byte-to-bit lookup table."""

    key = (str(device), dtype)
    cached = _BIT_LOOKUP_CACHE.get(key)
    if cached is not None:
        return cached
    values = torch.arange(256, device=device, dtype=torch.long).view(256, 1)
    shifts = torch.arange(8, device=device, dtype=torch.long).view(1, 8)
    lookup = ((values >> shifts) & 1).to(dtype=dtype)
    _BIT_LOOKUP_CACHE[key] = lookup
    return lookup


def pack_bits(bits: torch.Tensor) -> torch.Tensor:
    """Invert `unpack_bits` for hard reconstruction metrics."""

    weights = BYTE_VALUE_BIT_WEIGHTS.to(bits.device, dtype=torch.long)
    return (bits.long() * weights).sum(dim=-1)


def masked_event_semantic_metrics(
    header_uint8: torch.Tensor,
    target_events_uint8: torch.Tensor,
    predicted_events_uint8: torch.Tensor,
) -> dict[str, float]:
    """Compare masked-event reconstructions after decoding byte fields.

    The compact event bytes are not independent columns once prices are
    interpreted: `E3-E6` are signed deltas that need the sample header's ask and
    spread anchors to become quote/trade tick values. These metrics therefore
    decode both target and prediction with the original header and report errors
    in market-facing units for the masked event subset only.
    """

    with torch.no_grad():
        device = predicted_events_uint8.device
        header = header_uint8.to(device=device, dtype=torch.long)
        target = target_events_uint8.to(device=device, dtype=torch.long)
        predicted = predicted_events_uint8.to(device=device, dtype=torch.long)
        target_fields = decode_masked_event_semantics(header, target)
        predicted_fields = decode_masked_event_semantics(header, predicted)

        valid_mask = target_fields["presence"] == 1
        quote_mask = valid_mask & (target_fields["event_type"] == 0)
        trade_mask = valid_mask & (target_fields["event_type"] == 1)
        metrics = {
            "pretrain/semantic/masked_events": float(target.shape[0] * target.shape[1]),
            "pretrain/semantic/valid_events": float(valid_mask.float().sum().detach().cpu()),
            "pretrain/semantic/quote_events": float(quote_mask.float().sum().detach().cpu()),
            "pretrain/semantic/trade_events": float(trade_mask.float().sum().detach().cpu()),
            "pretrain/semantic/event_type_acc_pct": masked_accuracy(predicted_fields["event_type"], target_fields["event_type"], valid_mask),
            "pretrain/semantic/event_presence_acc_pct": masked_accuracy(predicted_fields["presence"], target_fields["presence"], torch.ones_like(valid_mask, dtype=torch.bool)),
            "pretrain/semantic/quote_time_bucket_mae": masked_mae(predicted_fields["event_delta_bucket"], target_fields["event_delta_bucket"], quote_mask),
            "pretrain/semantic/quote_ask_delta_tick_mae": masked_mae(predicted_fields["price1_delta"], target_fields["price1_delta"], quote_mask),
            "pretrain/semantic/quote_spread_delta_tick_mae": masked_mae(predicted_fields["price2_delta"], target_fields["price2_delta"], quote_mask),
            "pretrain/semantic/quote_ask_tick_mae": masked_mae(predicted_fields["price1_abs_ticks"], target_fields["price1_abs_ticks"], quote_mask),
            "pretrain/semantic/quote_spread_tick_mae": masked_mae(predicted_fields["spread_ticks"], target_fields["spread_ticks"], quote_mask),
            "pretrain/semantic/quote_bid_tick_mae": masked_mae(predicted_fields["bid_ticks"], target_fields["bid_ticks"], quote_mask),
            "pretrain/semantic/quote_ask_price_mae": masked_price_mae(predicted_fields["price1_abs_ticks"], target_fields["price1_abs_ticks"], target_fields["tick_size"], quote_mask),
            "pretrain/semantic/quote_bid_price_mae": masked_price_mae(predicted_fields["bid_ticks"], target_fields["bid_ticks"], target_fields["tick_size"], quote_mask),
            "pretrain/semantic/quote_bid_size_bucket_mae": masked_mae(predicted_fields["size1_bucket"], target_fields["size1_bucket"], quote_mask),
            "pretrain/semantic/quote_ask_size_bucket_mae": masked_mae(predicted_fields["size2_bucket"], target_fields["size2_bucket"], quote_mask),
            "pretrain/semantic/quote_bid_small_flag_acc_pct": masked_accuracy(predicted_fields["size1_small_flag"], target_fields["size1_small_flag"], quote_mask),
            "pretrain/semantic/quote_ask_small_flag_acc_pct": masked_accuracy(predicted_fields["size2_small_flag"], target_fields["size2_small_flag"], quote_mask),
            "pretrain/semantic/quote_tape_acc_pct": masked_accuracy(predicted_fields["tape"], target_fields["tape"], quote_mask),
            "pretrain/semantic/quote_bid_exchange_acc_pct": masked_accuracy(predicted_fields["exchange1"], target_fields["exchange1"], quote_mask),
            "pretrain/semantic/quote_ask_exchange_acc_pct": masked_accuracy(predicted_fields["exchange2"], target_fields["exchange2"], quote_mask),
            "pretrain/semantic/quote_condition_slot_acc_pct": masked_accuracy(predicted_fields["conditions"], target_fields["conditions"], quote_mask),
            "pretrain/semantic/quote_all_condition_slots_exact_acc_pct": masked_boolean_rate((predicted_fields["conditions"] == target_fields["conditions"]).all(dim=-1), quote_mask),
            "pretrain/semantic/trade_time_bucket_mae": masked_mae(predicted_fields["event_delta_bucket"], target_fields["event_delta_bucket"], trade_mask),
            "pretrain/semantic/trade_price_delta_tick_mae": masked_mae(predicted_fields["price1_delta"], target_fields["price1_delta"], trade_mask),
            "pretrain/semantic/trade_price_tick_mae": masked_mae(predicted_fields["price1_abs_ticks"], target_fields["price1_abs_ticks"], trade_mask),
            "pretrain/semantic/trade_price_mae": masked_price_mae(predicted_fields["price1_abs_ticks"], target_fields["price1_abs_ticks"], target_fields["tick_size"], trade_mask),
            "pretrain/semantic/trade_size_bucket_mae": masked_mae(predicted_fields["size1_bucket"], target_fields["size1_bucket"], trade_mask),
            "pretrain/semantic/trade_small_flag_acc_pct": masked_accuracy(predicted_fields["size1_small_flag"], target_fields["size1_small_flag"], trade_mask),
            "pretrain/semantic/trade_tape_acc_pct": masked_accuracy(predicted_fields["tape"], target_fields["tape"], trade_mask),
            "pretrain/semantic/trade_exchange_acc_pct": masked_accuracy(predicted_fields["exchange1"], target_fields["exchange1"], trade_mask),
            "pretrain/semantic/trade_condition_slot_acc_pct": masked_accuracy(predicted_fields["conditions"], target_fields["conditions"], trade_mask),
            "pretrain/semantic/trade_all_condition_slots_exact_acc_pct": masked_boolean_rate((predicted_fields["conditions"] == target_fields["conditions"]).all(dim=-1), trade_mask),
            "pretrain/semantic/trade_correction_acc_pct": masked_accuracy(predicted_fields["correction"], target_fields["correction"], trade_mask),
            "pretrain/semantic/predicted_quote_valid_pct": masked_boolean_rate(
                (predicted_fields["price1_abs_ticks"] > 0)
                & (predicted_fields["spread_ticks"] >= 0)
                & (predicted_fields["bid_ticks"] > 0),
                quote_mask,
            ),
        }
        return metrics


def decode_masked_event_semantics(header_uint8: torch.Tensor, events_uint8: torch.Tensor) -> dict[str, torch.Tensor]:
    """Decode compact masked event bytes into semantic integer fields.

    Header shape: `[B, 14]`; event shape: `[B, M, 16]`. Returned tensors use
    `[B, M]` except `conditions`, which is `[B, M, 4]`.
    """

    header_long = header_uint8.long()
    ask_anchor_ticks = header_long[:, 0] | (header_long[:, 1] << 8) | ((header_long[:, 2] & 0x0F) << 16)
    spread_anchor_ticks = header_long[:, 3] | (header_long[:, 4] << 8)
    tick_size = torch.where((header_uint8[:, 13] & 0x04) != 0, header_uint8.new_tensor(0.01, dtype=torch.float32), header_uint8.new_tensor(0.0001, dtype=torch.float32))
    ask_anchor = ask_anchor_ticks.unsqueeze(1)
    spread_anchor = spread_anchor_ticks.unsqueeze(1)
    event_type = events_uint8[:, :, 0] & 0x01
    presence = (events_uint8[:, :, 0] >> 1) & 0x01
    correction = (events_uint8[:, :, 0] >> 2) & 0x0F
    event_delta_bucket = uint16_le(events_uint8[:, :, 1], events_uint8[:, :, 2]) & 0x03FF
    price1_delta = int16_le(events_uint8[:, :, 3], events_uint8[:, :, 4])
    price2_delta = int16_le(events_uint8[:, :, 5], events_uint8[:, :, 6])
    price1_abs_ticks = ask_anchor + price1_delta
    spread_ticks = spread_anchor + price2_delta
    bid_ticks = price1_abs_ticks - spread_ticks
    size_flags = events_uint8[:, :, 9]
    conditions = events_uint8[:, :, 12:16]
    return {
        "event_type": event_type,
        "presence": presence,
        "correction": correction,
        "event_delta_bucket": event_delta_bucket,
        "price1_delta": price1_delta,
        "price2_delta": price2_delta,
        "price1_abs_ticks": price1_abs_ticks,
        "spread_ticks": spread_ticks,
        "bid_ticks": bid_ticks,
        "size1_bucket": events_uint8[:, :, 7],
        "size2_bucket": events_uint8[:, :, 8],
        "size1_small_flag": size_flags & 0x01,
        "size2_small_flag": (size_flags >> 1) & 0x01,
        "tape": (size_flags >> 2) & 0x07,
        "exchange1": events_uint8[:, :, 10] & 0x1F,
        "exchange2": events_uint8[:, :, 11] & 0x1F,
        "conditions": conditions,
        "tick_size": tick_size.unsqueeze(1),
    }


def uint16_le(low: torch.Tensor, high: torch.Tensor) -> torch.Tensor:
    return low.long() | (high.long() << 8)


def int16_le(low: torch.Tensor, high: torch.Tensor) -> torch.Tensor:
    value = uint16_le(low, high)
    return torch.where(value >= 32768, value - 65536, value)


def masked_accuracy(predicted: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> float:
    if predicted.ndim == target.ndim + 1:
        target = target.unsqueeze(-1).expand_as(predicted)
    if mask.ndim < predicted.ndim:
        mask = mask.unsqueeze(-1).expand_as(predicted)
    if not bool(mask.any()):
        return 0.0
    return float(((predicted == target) & mask).float().sum().detach().cpu() * 100.0 / mask.float().sum().detach().cpu())


def masked_mae(predicted: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> float:
    if not bool(mask.any()):
        return 0.0
    return float((predicted.float().sub(target.float()).abs()[mask]).mean().detach().cpu())


def masked_price_mae(predicted_ticks: torch.Tensor, target_ticks: torch.Tensor, tick_size: torch.Tensor, mask: torch.Tensor) -> float:
    if not bool(mask.any()):
        return 0.0
    return float((predicted_ticks.float().sub(target_ticks.float()).abs() * tick_size)[mask].mean().detach().cpu())


def masked_boolean_rate(values: torch.Tensor, mask: torch.Tensor) -> float:
    if not bool(mask.any()):
        return 0.0
    return float(values[mask].float().mean().detach().cpu() * 100.0)


def byte_psnr_db(mse: torch.Tensor) -> torch.Tensor:
    """PSNR over reconstructed byte values; higher means lower byte-level MSE."""

    return 10.0 * torch.log10(mse.new_tensor(BYTE_MAX_VALUE**2) / mse.clamp_min(PSNR_EPSILON))
