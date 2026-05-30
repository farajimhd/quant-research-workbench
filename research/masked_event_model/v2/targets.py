from __future__ import annotations

import numpy as np


def log_return_bps(numerator: np.ndarray | float, denominator: np.ndarray | float) -> np.ndarray:
    num = np.asarray(numerator, dtype=np.float32)
    den = np.asarray(denominator, dtype=np.float32)
    return np.log(np.maximum(num, 1e-6) / np.maximum(den, 1e-6)) * 10000.0


def encode_binary_magnitude_targets(target_bps: np.ndarray, *, bits: int) -> np.ndarray:
    values = np.asarray(target_bps, dtype=np.float32)
    sign = (values >= 0.0).astype(np.float32)[..., None]
    max_magnitude = (1 << int(bits)) - 1
    magnitude = np.clip(np.rint(np.abs(values)).astype(np.int64), 0, max_magnitude)
    bit_weights = (1 << np.arange(int(bits), dtype=np.int64)).reshape((1,) * magnitude.ndim + (-1,))
    magnitude_bits = ((magnitude[..., None] & bit_weights) > 0).astype(np.float32)
    return np.concatenate([sign, magnitude_bits], axis=-1).astype(np.float32)


def decode_binary_magnitude_logits_to_bps(logits: np.ndarray) -> np.ndarray:
    probabilities = 1.0 / (1.0 + np.exp(-np.clip(np.asarray(logits, dtype=np.float64), -60.0, 60.0)))
    sign = np.where(probabilities[..., 0] >= 0.5, 1.0, -1.0)
    bits = probabilities[..., 1:] >= 0.5
    weights = (1 << np.arange(bits.shape[-1], dtype=np.int64)).astype(np.float64)
    return sign * (bits.astype(np.float64) * weights).sum(axis=-1)
