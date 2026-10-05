"""Completed-clock crossing predicates, not watchlist or order authorization.

Callers bind certified producer columns and retain admission/level identities in
sequential state. Adjacency is required: absent price evidence cannot become a
crossing. No indicators are derived here.
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class CompletedCrossBatch:
    boundary_ms: np.ndarray
    crossing: np.ndarray
    rejection: np.ndarray


REJECT_PRICE = 1
REJECT_REFERENCE = 2
REJECT_CONTINUITY = 4
REJECT_ADMISSION = 8
REJECT_NO_CROSS = 16


def completed_crossings(*, boundary_ms: np.ndarray, close_int: np.ndarray,
                        price_valid: np.ndarray, reference_int: np.ndarray,
                        reference_available_ms: np.ndarray,
                        reference_valid: np.ndarray,
                        admitted_at_ms: int, buffer_int: int,
                        maximum_reference_age_ms: int) -> CompletedCrossBatch:
    """Evaluate close <= prior reference then close > current reference+buffer.

    Prices use the native 1/10000-dollar integer scale. A frozen resistance
    caller supplies the same geometry for both boundaries and a causal producer
    availability clock. VWAP callers supply each completed boundary's value.
    The reference's continuity/identity certification remains caller-owned.
    """
    columns = (boundary_ms, close_int, price_valid, reference_int,
               reference_available_ms, reference_valid)
    if any(not isinstance(value, np.ndarray) or value.ndim != 1 for value in columns):
        raise ValueError("Crossing inputs must be one-dimensional typed arrays")
    n = len(boundary_ms)
    if any(len(value) != n or value.dtype.kind not in "iu" for value in columns):
        raise ValueError("Crossing inputs must be aligned integer arrays")
    if any(value.dtype != np.dtype("int64") for value in
           (boundary_ms, close_int, reference_int, reference_available_ms)):
        raise ValueError("Prices and clocks require exact native int64 columns")
    if (type(admitted_at_ms) is not int or admitted_at_ms < 0
            or type(buffer_int) is not int or buffer_int < 0
            or type(maximum_reference_age_ms) is not int or maximum_reference_age_ms < 0):
        raise ValueError("Invalid crossing policy")
    if (np.any(boundary_ms < 0) or np.any(boundary_ms % 100)
            or np.any(np.diff(boundary_ms) <= 0)):
        raise ValueError("Crossing boundaries must be ordered completed 100ms clocks")
    if np.any((price_valid != 0) & (price_valid != 1)) or np.any(
            (reference_valid != 0) & (reference_valid != 1)):
        raise ValueError("Validity columns must contain only zero or one")
    if buffer_int > np.iinfo(np.int64).max or np.any(
            reference_int > np.iinfo(np.int64).max - buffer_int):
        raise ValueError("Crossing buffer overflows native price domain")
    good_price = (price_valid == 1) & (close_int > 0)
    good_reference = ((reference_valid == 1) & (reference_int > 0)
                      & (reference_available_ms >= 0)
                      & (reference_available_ms <= boundary_ms))
    # Subtraction is safe after requiring nonnegative causal availability.
    age = boundary_ms - np.maximum(reference_available_ms, 0)
    good_reference &= age <= maximum_reference_age_ms
    continuity = np.zeros(n, dtype=bool)
    continuity[1:] = np.diff(boundary_ms) == 100
    prior_price = np.zeros(n, dtype=bool)
    prior_reference = np.zeros(n, dtype=bool)
    prior_price[1:] = good_price[:-1]
    prior_reference[1:] = good_reference[:-1]
    after_admission = np.zeros(n, dtype=bool)
    after_admission[1:] = boundary_ms[:-1] >= admitted_at_ms
    crossing = np.zeros(n, dtype=bool)
    crossing[1:] = ((close_int[:-1] <= reference_int[:-1])
                    & (close_int[1:] > reference_int[1:] + buffer_int))
    reasons = np.zeros(n, dtype=np.uint8)
    for bit, good in (
        (REJECT_PRICE, good_price & prior_price),
        (REJECT_REFERENCE, good_reference & prior_reference),
        (REJECT_CONTINUITY, continuity),
        (REJECT_ADMISSION, after_admission),
        (REJECT_NO_CROSS, crossing),
    ):
        reasons[~good] |= bit
    return CompletedCrossBatch(boundary_ms.copy(), reasons == 0, reasons)
