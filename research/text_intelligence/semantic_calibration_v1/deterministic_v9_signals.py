from __future__ import annotations

import re
from typing import Any, Iterable

from .teacher_split_v9 import normalized_headline_template


TITLE_FAMILY_PATTERNS: dict[str, str] = {
    "analyst_maintains": r"\b(?:maintains?|reiterates?)\b.{0,80}\b(?:rating|target|buy|sell|hold|overweight|underweight)\b",
    "analyst_changes": r"\b(?:upgrades?|downgrades?|raises?|lowers?|cuts?)\b.{0,80}\b(?:rating|target|buy|sell|hold|overweight|underweight)\b",
    "earnings_preview": r"\b(?:earnings|results)\b.{0,60}\b(?:preview|expected|ahead|before)\b",
    "earnings_report": r"\b(?:reports?|posts?|announces?)\b.{0,80}\b(?:earnings|results|eps|revenue)\b",
    "mover_list": r"\b(?:stocks?|shares?)\b.{0,50}\b(?:moving|movers|gainers|losers)\b",
    "why_moving": r"\bwhy\b.{0,50}\b(?:moving|up|down|higher|lower)\b",
    "offering": r"\b(?:offering|private placement|registered direct|at the market)\b",
    "ma": r"\b(?:acquire|acquisition|merger|buyout)\b",
    "index_constituent_change": (
        r"\b(?:replace|replaces|replacing|added to|removed from)\b.{0,120}"
        r"\b(?:index|smallcap|midcap|s&p|nasdaq)\b"
    ),
    "clinical": r"\b(?:clinical|trial|phase <number>|topline)\b",
    "regulatory": r"\b(?:fda|sec|nasdaq|nyse|regulatory)\b",
}


def article_signals_from_parts(
    *,
    title: str,
    provider_tickers: Iterable[str],
    provider_tags: Iterable[Any],
    channels: Iterable[Any],
    evidence: Iterable[str],
) -> tuple[str, ...]:
    tickers = tuple(str(value) for value in provider_tickers if value)
    values = {f"tag:{normalize_signal(tag)}" for tag in provider_tags if normalize_signal(tag)}
    values.update(f"channel:{normalize_signal(channel)}" for channel in channels if normalize_signal(channel))
    values.update(str(value).casefold() for value in evidence if value)
    values.add("ticker_scope:zero" if not tickers else "ticker_scope:single" if len(tickers) == 1 else "ticker_scope:multi")
    template = normalized_headline_template(title, tickers)
    values.update(
        f"title_family:{name}"
        for name, pattern in TITLE_FAMILY_PATTERNS.items()
        if re.search(pattern, template, re.I | re.S)
    )
    return tuple(sorted(values))


def normalize_signal(value: Any) -> str:
    return " ".join(str(value).casefold().split())
