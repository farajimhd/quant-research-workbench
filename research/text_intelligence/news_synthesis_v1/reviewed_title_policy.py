from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class ReviewedTitlePolicy:
    label: str
    family: str
    purpose: str
    origin: str
    title_material_flag: str = ""


_ANALYST_FORECAST_RE = re.compile(
    r"\bthese\s+(?:most accurate\s+)?analysts?\s+"
    r"(?:boost|increase|raise|cut|lower|slash|revise|adjust)\s+their\s+forecasts?\b|"
    r"\banalysts?\b.{0,100}\b(?:boost|increase|raise|cut|lower|slash|revise|adjust)\b"
    r".{0,100}\b(?:forecasts?|estimates?|price targets?)\b",
    re.I,
)
_ROUTINE_FUND_HOLDING_RE = re.compile(
    r"\b(?:fund management|capital management|asset management|global investors?|partners?)\b"
    r".{0,120}\b(?:reports?|raises?|lowers?|cuts?|takes?|exits?|reduces?)\b"
    r".{0,100}\b(?:share stake|position|shares?)\b|"
    r"\b(?:13F|quarterly holdings?|unchanged from (?:the )?prior quarter)\b",
    re.I,
)
_EARNINGS_RESULT_RE = re.compile(
    r"\b(?:sales|revenue|adj(?:usted)?\.?\s+EPS|GAAP\s+EPS|EPS)\b.{0,180}"
    r"\b(?:beat(?:s|en)?|miss(?:es|ed)?|inline)\b.{0,80}\b(?:estimate|est\.?|consensus)\b|"
    r"\bearnings\s+(?:report|analysis|breakdown|summary|recap)\b|"
    r"\b(?:quarter|Q[1-4])\s+earnings\s+summary\b|"
    r"\bwhat\s+investors\s+need\s+to\s+know\b",
    re.I,
)
_NUMERIC_GUIDANCE_VS_ESTIMATE_RE = re.compile(
    r"\b(?:sees?|expects?|anticipates?|projects?|raises?|lowers?|cuts?|reaffirms?)\b"
    r".{0,180}\b(?:FY\s?\d{2,4}|Q[1-4]|sales|revenue|EPS|EBITDA|guidance|outlook)\b"
    r".{0,180}\b(?:vs\.?|versus)\s*\$?[\d(]",
    re.I,
)
_REPORTED_EARLIER_RE = re.compile(
    r"^\s*(?:reported\s+(?:earlier|sunday|monday|tuesday|wednesday|thursday|friday)|update\s*:)",
    re.I,
)
_CLINICAL_CONFERENCE_RE = re.compile(
    r"\b(?:present|presents|presented|presentation|showcase|showcases|highlight|highlights|share|shares)\b"
    r".{0,220}\b(?:clinical|trial|phase\s*[1-4]|preclinical|data|results?|abstract|poster)\b"
    r".{0,220}\b(?:congress|conference|symposium|annual meeting|EHA|ESMO|ASCO|EASL|EADV|ESC|EAACI|AACR)\b|"
    r"\b(?:clinical|trial|phase\s*[1-4]|preclinical|data|results?|abstract|poster)\b"
    r".{0,220}\b(?:present|presentation)\b.{0,180}\b(?:congress|conference|symposium|annual meeting)\b",
    re.I,
)


def classify_reviewed_title_policy(
    title: str,
    *,
    tickers: Sequence[str] = (),
) -> ReviewedTitlePolicy | None:
    """Return only operator-approved, generic title-policy decisions.

    Direct row adjudications remain gold-authority corrections and must never be
    added here as source-ID or issuer-specific exceptions.
    """
    normalized = " ".join(str(title or "").split())
    if not normalized:
        return None
    if _ANALYST_FORECAST_RE.search(normalized):
        return ReviewedTitlePolicy(
            "ineligible", "analyst_forecast_revision", "analyze", "analyst"
        )
    if _ROUTINE_FUND_HOLDING_RE.search(normalized):
        return ReviewedTitlePolicy(
            "ineligible", "routine_fund_holding", "analyze", "editorial"
        )
    if _REPORTED_EARLIER_RE.search(normalized):
        return ReviewedTitlePolicy(
            "ineligible", "reported_earlier_followup", "recap", "editorial"
        )
    if _EARNINGS_RESULT_RE.search(normalized):
        return ReviewedTitlePolicy(
            "ineligible", "earnings_result_or_recap", "recap", "editorial"
        )
    if not normalized.casefold().startswith("correction:") and _NUMERIC_GUIDANCE_VS_ESTIMATE_RE.search(normalized):
        return ReviewedTitlePolicy(
            "ineligible", "numeric_guidance_vs_estimate", "recap", "editorial"
        )
    if _CLINICAL_CONFERENCE_RE.search(normalized):
        if len(tuple(dict.fromkeys(str(value) for value in tickers if value))) > 1:
            return ReviewedTitlePolicy(
                "ineligible", "multi_ticker_clinical_conference", "recap", "editorial"
            )
        return ReviewedTitlePolicy(
            "eligible",
            "single_issuer_clinical_conference",
            "report",
            "issuer",
            "clinical_conference_material",
        )
    return None
