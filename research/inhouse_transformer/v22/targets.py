from __future__ import annotations

import numpy as np


def target_values_to_bps(
    values: np.ndarray,
    current_close: np.ndarray,
    center: np.ndarray,
    scale: np.ndarray,
    target_mode: str,
) -> np.ndarray:
    if target_mode == "actual_price_zscore":
        prices = denormalize_actual_zscore(values, center, scale)
        return simple_return_bps(prices, np.asarray(current_close, dtype=np.float64).reshape(-1, 1, 1))
    if target_mode == "return_bps":
        return np.asarray(values, dtype=np.float64)
    if target_mode == "binary_magnitude_bps":
        return decode_binary_magnitude_logits_to_bps(values)
    raise ValueError(f"Unsupported target_mode: {target_mode}")


def denormalize_actual_zscore(
    values: np.ndarray,
    center: np.ndarray,
    scale: np.ndarray,
) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    center_array = np.asarray(center, dtype=np.float64).reshape(-1, 1, 1)
    scale_array = np.asarray(scale, dtype=np.float64).reshape(-1, 1, 1)
    return values * scale_array + center_array


def encode_binary_magnitude_targets(target_bps: np.ndarray, *, bits: int) -> np.ndarray:
    target_bps = np.asarray(target_bps, dtype=np.float32)
    sign = (target_bps >= 0.0).astype(np.float32)[..., None]
    max_magnitude = (1 << int(bits)) - 1
    magnitude = np.rint(np.abs(target_bps)).astype(np.int64)
    magnitude = np.clip(magnitude, 0, max_magnitude)
    bit_weights = (1 << np.arange(int(bits), dtype=np.int64)).reshape((1,) * magnitude.ndim + (-1,))
    magnitude_bits = ((magnitude[..., None] & bit_weights) > 0).astype(np.float32)
    return np.concatenate([sign, magnitude_bits], axis=-1).astype(np.float32)


def decode_binary_magnitude_logits_to_bps(values: np.ndarray) -> np.ndarray:
    logits = np.asarray(values, dtype=np.float64)
    if logits.ndim < 1 or logits.shape[-1] < 2:
        raise ValueError(f"Expected binary magnitude logits with a bit axis, got shape {logits.shape}.")
    probabilities = sigmoid_np(logits)
    sign = np.where(probabilities[..., 0] >= 0.5, 1.0, -1.0)
    bits = probabilities[..., 1:] >= 0.5
    weights = (1 << np.arange(bits.shape[-1], dtype=np.int64)).astype(np.float64)
    magnitude = (bits.astype(np.float64) * weights).sum(axis=-1)
    return sign * magnitude


def binary_magnitude_logits_to_distribution_stats(values: np.ndarray) -> dict[str, np.ndarray]:
    logits = np.asarray(values, dtype=np.float64)
    if logits.ndim < 1 or logits.shape[-1] < 2:
        raise ValueError(f"Expected binary magnitude logits with a bit axis, got shape {logits.shape}.")
    probabilities = sigmoid_np(logits)
    sign_probability = probabilities[..., 0]
    magnitude_probabilities = probabilities[..., 1:]
    weights = (1 << np.arange(magnitude_probabilities.shape[-1], dtype=np.int64)).astype(np.float64)

    expected_magnitude = (magnitude_probabilities * weights).sum(axis=-1)
    magnitude_variance = (magnitude_probabilities * (1.0 - magnitude_probabilities) * np.square(weights)).sum(axis=-1)
    magnitude_std = np.sqrt(np.maximum(magnitude_variance, 0.0))

    sign_mean = 2.0 * sign_probability - 1.0
    expected_signed_bps = sign_mean * expected_magnitude
    confidence_denominator = np.abs(expected_signed_bps) + magnitude_std + 1e-12
    confidence = np.divide(
        np.abs(expected_signed_bps),
        confidence_denominator,
        out=np.zeros_like(expected_signed_bps, dtype=np.float64),
        where=confidence_denominator > 0.0,
    )
    return {
        "expected_signed_bps": expected_signed_bps,
        "expected_magnitude_bps": expected_magnitude,
        "magnitude_std_bps": magnitude_std,
        "confidence": np.clip(confidence, 0.0, 1.0),
        "sign_confidence": np.abs(sign_mean),
        "p_up": sign_probability,
    }


def sigmoid_np(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60.0, 60.0)))


def log_return_bps(numerator: np.ndarray, denominator: np.ndarray | float) -> np.ndarray:
    numerator = np.asarray(numerator, dtype=np.float32)
    denominator_array = np.asarray(denominator, dtype=np.float32)
    safe_num = np.maximum(numerator, 1e-6)
    safe_den = np.maximum(denominator_array, 1e-6)
    return np.log(safe_num / safe_den) * 10000.0


def simple_return_bps(numerator: np.ndarray, denominator: np.ndarray | float) -> np.ndarray:
    numerator = np.asarray(numerator, dtype=np.float64)
    denominator_array = np.asarray(denominator, dtype=np.float64)
    safe_den = np.maximum(denominator_array, 1e-6)
    return (numerator / safe_den - 1.0) * 10000.0
