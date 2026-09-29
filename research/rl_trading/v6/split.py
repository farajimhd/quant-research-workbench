"""Forward-only dates for the revised actual-candle campaign."""
from __future__ import annotations

from datetime import date


CONTEXT_ONLY = (date(2026, 7, 30),)
TRAIN = (
    date(2026, 7, 31), date(2026, 8, 3), date(2026, 8, 4),
    date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7),
    date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12),
    date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17),
    date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
    date(2026, 8, 21),
)
DEVELOPMENT = (date(2026, 8, 24), date(2026, 8, 25))
SEALED_TEST = (date(2026, 8, 26),)


def role(day: date) -> str:
    """Reject unknown days so a context-only day cannot gain training labels."""
    if day in CONTEXT_ONLY:
        return 'context_only'
    if day in TRAIN:
        return 'train'
    if day in DEVELOPMENT:
        return 'development'
    if day in SEALED_TEST:
        return 'sealed_test'
    raise ValueError(f'Date is not in the approved V6 forward split: {day}')


def training_dates() -> tuple[date, ...]:
    return TRAIN
