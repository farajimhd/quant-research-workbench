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
    r"\b(?:fund management|capital management|asset management|global management|"
    r"global investors?|investment management|investment advisors?|hedge fund)\b"
    r".{0,120}\b(?:reports?|raises?|lowers?|cuts?|takes?|exits?|reduces?)\b"
    r".{0,100}\b(?:new )?(?:share stake|stake|position|shares?)\b|"
    r"\b(?:takes?|reports?|raises?|lowers?|cuts?|exits?|reduces?)\b.{0,100}"
    r"\b(?:new )?(?:stake|position)\b.{0,100}\b(?:shares?|stock)\b|"
    r"\b(?:13F|quarterly holdings?|unchanged from (?:the )?prior quarter)\b",
    re.I,
)
_MATERIAL_OWNERSHIP_RE = re.compile(
    r"\b(?:Schedule\s+13D|Schedule\s+13G|13D filing|13G filing|beneficial ownership|"
    r"beneficial owner|activist investor|activist stake|\d+(?:\.\d+)?%\s+(?:ownership|stake))\b",
    re.I,
)
_EARNINGS_RESULT_RE = re.compile(
    r"\b(?:sales|revenue|adj(?:usted)?\.?\s+EPS|GAAP\s+EPS|EPS)\b.{0,180}"
    r"\b(?:beat(?:s|en)?|miss(?:es|ed)?|inline)\b.{0,80}\b(?:estimate|est\.?|consensus)\b|"
    r"\bearnings\s+(?:report|analysis|breakdown|summary|recap)\b|"
    r"\b(?:quarter|Q[1-4])\s+earnings\s+summary\b|"
    r"\bQ[1-4]\s+earnings\b|"
    r"\b(?:Q[1-4]|quarter(?:ly)?|FY\s?\d{2,4}|holiday)\b.{0,100}"
    r"\b(?:sales|revenue|EPS|income|profit|loss)\b.{0,100}"
    r"\b(?:grew|growth|rose|fell|increased|decreased|surged|declined|up|down|YoY)\b|"
    r"\b(?:reports?|posts?|announces?)\b.{0,100}\b(?:Q[1-4]|quarter(?:ly)?)\b"
    r".{0,120}\b(?:results?|revenue|sales|EPS|income|loss|comparable sales)\b|"
    r"\b(?:Q[1-4]|quarter(?:ly)?)\b.{0,100}\b(?:results?|revenue|sales|EPS|income|loss)\b|"
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
_LIVE_BROADCAST_RE = re.compile(
    r"\b(?:live broadcast|live webcast|broadcasting live|streaming live)\b",
    re.I,
)
_PRICE_REACTION_RE = re.compile(
    r"\b(?:why\s+(?:is|are|did|has|have|was|were)|what(?:'s| is)\s+going\s+on)\b"
    r".{0,220}\b(?:stock|shares?)\b|"
    r"\bshares?\b.{0,100}\btrading\s+(?:higher|lower|up|down)\b|"
    r"\b(?:stock|shares?)\b.{0,40}\b(?:jumps?|surges?|soars?|rallies|rises?|gains?|"
    r"slides?|slumps?|sinks?|falls?|drops?|tumbles?|plunges?)\b.{0,120}"
    r"\b(?:today|premarket|after hours|here's why|what(?:'s| is) going on)\b",
    re.I,
)
_ROUNDUP_LIST_RE = re.compile(
    r"^\s*\d+\s+.{0,80}\bstocks\b|"
    r"\b(?:stocks?|industrials?|consumer stocks?|tech stocks?|health care stocks?)\s+"
    r"moving\s+in\s+.{0,80}\bsession\b|"
    r"\b(?:top|best|worst)\s+\d+\b.{0,80}\bstocks?\b|"
    r"\bstocks?\s+to\s+(?:watch|buy|sell)\b|"
    r"\b(?:gainers?|losers?|movers?)\s+(?:roundup|recap|list)\b",
    re.I,
)
_TRADING_HALT_RE = re.compile(
    r"\b(?:trading halt|halted at|halt news pending|trading resumes?|resume trading)\b",
    re.I,
)
_QUESTION_HYPOTHESIS_RE = re.compile(
    r"(?:\?|\b(?:is|are|will|would|could|can|should|does|do|has|have)\b.{0,180}\?)\s*$",
    re.I,
)
_VALUATION_COMPARISON_RE = re.compile(
    r"\b(?:competitor analysis|industry comparison|peer comparison|compared to (?:its )?peers|"
    r"valuation overview|valuation analysis|P/E ratio|PEG ratio|price-to-earnings|"
    r"is (?:the )?.{0,80}(?:undervalued|overvalued)|how (?:cheap|expensive) is|"
    r"^\s*this\b.{0,100}\b(?:undervalued|overvalued))\b",
    re.I,
)
_LEGAL_ACTION_RE = re.compile(
    r"\b(?:lawsuit|class action|litigation|legal action|investigation|probe|subpoena|"
    r"settlement|sues?|sued|court rules?|judge rules?|SEC charges?|FTC action|"
    r"enforcement actions?)\b|"
    r"\b(?:FTC|SEC|DOJ|attorney general|regulator)\b.{0,60}"
    r"\b(?:takes? action|files?|charges?|sues?|orders?)\b",
    re.I,
)
_OPTIONS_ACTIVITY_RE = re.compile(
    r"\b(?:options? (?:activity|trades?|market)|unusual options?|whale activity|"
    r"call volume|put volume|options? sweep)\b",
    re.I,
)
_TECHNICAL_TRADING_RE = re.compile(
    r"\b(?:technical analysis|RSI|MACD|moving average|support level|resistance level|"
    r"overbought|oversold|death cross|golden cross)\b",
    re.I,
)
_PREVIEW_SCHEDULE_RE = re.compile(
    r"\b(?:ahead of earnings|earnings preview|what to expect|set to report|"
    r"scheduled to report|will report|earnings date|before the opening bell|"
    r"after the closing bell|conference schedule)\b",
    re.I,
)
_OPINION_PREDICTION_RE = re.compile(
    r"\b(?:prediction|price prediction|here's what .* thinks|expert says|analyst says|"
    r"investor says)\b.{0,180}\b(?:stock|shares?|price|market)\b|"
    r"\b(?:stock|shares?|price|market)\b.{0,120}\b(?:could|may|might|is poised to)\b"
    r".{0,120}\b(?:rise|fall|gain|drop|rally|surge|plunge|double|reach|hit)\b",
    re.I,
)
_NONISSUER_CONTEXT_RE = re.compile(
    r"\b(?:white house|president trump|donald trump|congress|senate|election|"
    r"federal reserve|inflation|jobs report|geopolitic|terrorism|lifestyle|celebrity|"
    r"cryptocurrency market|bond buyers?)\b",
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
    if _MATERIAL_OWNERSHIP_RE.search(normalized) and not re.search(r"\b13F\b", normalized, re.I):
        return ReviewedTitlePolicy(
            "eligible", "material_ownership", "report", "issuer", "material_ownership_event"
        )
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
    if _LIVE_BROADCAST_RE.search(normalized):
        return ReviewedTitlePolicy(
            "eligible", "live_broadcast", "report", "issuer", "live_broadcast_material"
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
    for pattern, family, purpose, origin in (
        (_PRICE_REACTION_RE, "price_reaction", "explain_move", "editorial"),
        (_ROUNDUP_LIST_RE, "roundup_or_reference_list", "recap", "editorial"),
        (_TRADING_HALT_RE, "trading_halt_status", "recap", "regulator"),
        (_QUESTION_HYPOTHESIS_RE, "question_or_hypothesis", "analyze", "editorial"),
        (_VALUATION_COMPARISON_RE, "valuation_peer_comparison", "analyze", "editorial"),
        (_LEGAL_ACTION_RE, "legal_regulatory_action", "recap", "editorial"),
        (_OPTIONS_ACTIVITY_RE, "options_activity", "analyze", "editorial"),
        (_TECHNICAL_TRADING_RE, "technical_trading", "analyze", "editorial"),
        (_PREVIEW_SCHEDULE_RE, "preview_schedule", "preview", "editorial"),
        (_OPINION_PREDICTION_RE, "opinion_or_prediction", "analyze", "editorial"),
    ):
        if pattern.search(normalized):
            return ReviewedTitlePolicy("ineligible", family, purpose, origin)
    if _NONISSUER_CONTEXT_RE.search(normalized) and len(tuple(dict.fromkeys(
        str(value) for value in tickers if value
    ))) != 1:
        return ReviewedTitlePolicy(
            "ineligible", "nonissuer_politics_macro_lifestyle", "recap", "editorial"
        )
    if not normalized.casefold().startswith("correction:") and _NUMERIC_GUIDANCE_VS_ESTIMATE_RE.search(normalized):
        return ReviewedTitlePolicy(
            "ineligible", "numeric_guidance_vs_estimate", "recap", "editorial"
        )
    if _EARNINGS_RESULT_RE.search(normalized):
        return ReviewedTitlePolicy(
            "ineligible", "earnings_result_or_recap", "recap", "editorial"
        )
    return None
