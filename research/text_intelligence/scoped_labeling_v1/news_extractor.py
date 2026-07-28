from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from research.text_intelligence.semantic_label_authority_v1.structure import (
    normalize_source_text,
    segment_rendered_text,
)

from .news_identity import (
    ISSUER_RESOLUTION_VERSION,
    IssuerMatch,
    NewsIssuerResolver,
)
from .schema import NEWS_EXTRACTOR_VERSION, ObservedReaction, RelevantTextUnit


MOVE_RE = re.compile(
    r"\b(?:shares?|stock)\s+"
    r"(?P<verb>rose|gained|climbed|jumped|surged|rallied|advanced|"
    r"fell|dropped|declined|slid|plunged|tumbled|lost)\s+"
    r"(?P<pct>\d+(?:\.\d+)?)%\s*(?:to|at)\s*\$(?P<price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
SESSION_RE = re.compile(
    r"\b(pre[- ]market|premarket|after[- ]hours|post[- ]market|"
    r"regular(?:[- ]hours)? trading|midday|mid-day)\b",
    re.IGNORECASE,
)
CATALYST_RE = re.compile(
    r"\b(?:after|following|because|as\s+(?=(?:the company|it|shares?|stock)\b))"
    r"\s+(?P<catalyst>[^.;]{8,320})",
    re.IGNORECASE,
)
AGGREGATION_TITLE_RE = re.compile(
    r"\b(?:\d+\s+)?(?:biggest|top)\s+(?:stock\s+)?"
    r"(?:gainers|losers|movers)\b|"
    r"\b\d+\s+stocks?\s+(?:moving|to watch)\b|"
    r"\bstocks?\s+moving\s+(?:in|during|today|pre[- ]market|after[- ]hours)\b|"
    r"\b(?:pre[- ]market|after[- ]hours|mid[- ]day)\s+movers?\b|"
    r"\b(?:gainers|losers)\s+and\s+(?:decliners|movers)\b",
    re.IGNORECASE,
)
ANALYST_ACTION_RE = re.compile(
    r"\b(?:upgrade[sd]?|downgrade[sd]?|maintain(?:s|ed)?|reiterate[sd]?|"
    r"initiate[sd]?|resume[sd]?|"
    r"price\s+target)\b",
    re.IGNORECASE,
)
ANALYST_ROLE_RE = re.compile(
    r"\b(?:analyst|brokerage|price\s+target|rating|coverage)\b|"
    r"\b(?:upgrade[sd]?|downgrade[sd]?|maintain(?:s|ed)?|reiterate[sd]?|"
    r"initiate[sd]?|resume[sd]?)\b.{0,100}\b(?:buy|sell|hold|"
    r"overweight|underweight|outperform|underperform|neutral)\b",
    re.IGNORECASE,
)
EVENT_SUBJECT_VERB_RE = re.compile(
    r"\b(?:announce[sd]?|report(?:s|ed)?|raise[sd]?|lower(?:s|ed)?|"
    r"cut(?:s)?|receive[sd]?|file[sd]?|enter(?:s|ed)?|agree[sd]?|"
    r"terminate[sd]?|launch(?:es|ed)?|secure[sd]?|win(?:s)?|won|"
    r"acquire[sd]?|merge[sd]?|offer(?:s|ed)?|price[sd]?|"
    r"upgrade[sd]?|downgrade[sd]?|maintain(?:s|ed)?|"
    r"initiate[sd]?|resume[sd]?|expect(?:s|ed)?|plan(?:s|ned)?)\b",
    re.IGNORECASE,
)
COMPANY_LIKE_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*){0,6}\s+"
    r"(?:Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|plc|"
    r"Holdings?|Therapeutics|Technologies|Pharmaceuticals?)\b"
)
ABBREVIATIONS = {
    "co.",
    "corp.",
    "dr.",
    "inc.",
    "ltd.",
    "mr.",
    "mrs.",
    "ms.",
    "no.",
    "st.",
    "u.s.",
}
CONTEXT_ONLY_ROLES = {
    "ticker_market_observation",
    "ticker_scoped_editorial_context",
    "ticker_scoped_analyst_context",
}


@dataclass(frozen=True, slots=True)
class PassageResolution:
    ordinal: int
    text: str
    start: int
    end: int
    resolved_tickers: tuple[str, ...]
    evidence: tuple[str, ...]
    decision: str
    assigned_ticker: str = ""


@dataclass(frozen=True, slots=True)
class NewsScopeAnalysis:
    units: tuple[RelevantTextUnit, ...]
    passages: tuple[PassageResolution, ...]
    linked_tickers: tuple[str, ...]
    resolved_subjects: tuple[str, ...]
    document_decision: str
    aggregation: bool
    resolver_version: str = ISSUER_RESOLUTION_VERSION


@dataclass(frozen=True, slots=True)
class _Fragment:
    ordinal: int
    text: str
    start: int
    end: int
    block_index: int
    source_lane: str
    heading_matches: tuple[IssuerMatch, ...]
    matches: tuple[IssuerMatch, ...]
    unresolved_company_mention: bool


def extract_news_units(
    *,
    source_id: str,
    title: str,
    text: str,
    tickers: tuple[str, ...],
    timestamp: str = "",
    issuer_resolver: NewsIssuerResolver | None = None,
    metadata: Mapping[str, object] | None = None,
) -> tuple[RelevantTextUnit, ...]:
    return analyze_news_scope(
        source_id=source_id,
        title=title,
        text=text,
        tickers=tickers,
        timestamp=timestamp,
        issuer_resolver=issuer_resolver,
        metadata=metadata,
    ).units


def analyze_news_scope(
    *,
    source_id: str,
    title: str,
    text: str,
    tickers: tuple[str, ...],
    timestamp: str = "",
    issuer_resolver: NewsIssuerResolver | None = None,
    metadata: Mapping[str, object] | None = None,
) -> NewsScopeAnalysis:
    clean = normalize_source_text(text)
    linked = tuple(dict.fromkeys(value.upper() for value in tickers if value))
    resolver = issuer_resolver or NewsIssuerResolver.from_metadata(metadata or {})
    resolver = resolver.with_article_identities(clean)
    blocks = segment_rendered_text("news", clean)
    aggregation = bool(AGGREGATION_TITLE_RE.search(title))
    title_matches = _subject_matches(
        title,
        resolver.resolve(
            title,
            timestamp=timestamp,
            linked_tickers=linked,
        ),
        linked_tickers=linked,
    )
    fragments = _fragments(
        blocks,
        resolver=resolver,
        timestamp=timestamp,
        linked_tickers=linked,
    )
    primary_fragments = tuple(
        fragment
        for fragment in fragments
        if fragment.source_lane != "external_enrichment"
    )
    resolved_subjects = tuple(sorted({
        match.ticker
        for match in title_matches
    } | {
        match.ticker
        for fragment in primary_fragments
        for match in (*fragment.heading_matches, *fragment.matches)
    }))
    unresolved_company = any(
        fragment.unresolved_company_mention
        for fragment in primary_fragments
    )

    if (
        not aggregation
        and resolver.issuer_group_count(
            resolved_subjects,
            timestamp=timestamp,
        ) == 1
        and not unresolved_company
    ):
        subject = resolved_subjects[0]
        semantic = "\n".join(
            fragment.text for fragment in primary_fragments
        ).strip()
        if not semantic:
            return _empty_analysis(
                linked,
                resolved_subjects,
                "abstained_no_semantic_text",
                aggregation,
            )
        linked_subject = resolver.one_provider_linked_issuer(
            subject,
            linked,
            timestamp=timestamp,
        )
        role = (
            _single_document_role(semantic)
            if linked_subject
            else "ticker_scoped_editorial_context"
        )
        reaction = extract_observed_reaction(semantic)
        unit = RelevantTextUnit(
            corpus="news",
            source_id=source_id,
            unit_id=_unit_id(source_id, 1, subject, semantic),
            ordinal=1,
            role=role,
            text=semantic,
            start=min((fragment.start for fragment in primary_fragments), default=0),
            end=max((fragment.end for fragment in primary_fragments), default=len(clean)),
            tickers=(subject,),
            shared_context=False,
            observed_reaction=reaction,
            reported_catalyst=(
                extract_reported_catalyst(semantic)
                if reaction.direction else ""
            ),
            extractor_version=NEWS_EXTRACTOR_VERSION,
            quality_flags=(
                "document_single_resolved_issuer",
                *(
                    ()
                    if linked_subject
                    else ("resolved_issuer_not_exclusive_provider_link",)
                ),
            ),
        )
        passages = tuple(
            PassageResolution(
                ordinal=fragment.ordinal,
                text=fragment.text,
                start=fragment.start,
                end=fragment.end,
                resolved_tickers=tuple(
                    sorted({match.ticker for match in fragment.matches})
                ),
                evidence=_match_evidence(fragment.matches),
                decision="included_in_single_issuer_document",
                assigned_ticker=subject,
            )
            for fragment in primary_fragments
        )
        passages += tuple(
            PassageResolution(
                ordinal=fragment.ordinal,
                text=fragment.text,
                start=fragment.start,
                end=fragment.end,
                resolved_tickers=tuple(
                    sorted({match.ticker for match in fragment.matches})
                ),
                evidence=_match_evidence(fragment.matches),
                decision="abstained_external_enrichment",
            )
            for fragment in fragments
            if fragment.source_lane == "external_enrichment"
        )
        passages = tuple(sorted(passages, key=lambda value: value.ordinal))
        return NewsScopeAnalysis(
            units=(unit,),
            passages=passages,
            linked_tickers=linked,
            resolved_subjects=resolved_subjects,
            document_decision=(
                "single_resolved_issuer"
                if linked_subject
                else "single_resolved_context_only"
            ),
            aggregation=False,
        )

    assignments, passages = _resolve_passages(
        fragments,
        title_matches=title_matches,
        aggregation=aggregation,
    )
    units = _units_from_assignments(
        source_id=source_id,
        assignments=assignments,
        aggregation=aggregation,
        mixed=len(resolved_subjects) > 1 or len(linked) > 1,
    )
    if aggregation:
        decision = "aggregation_passage_scoping"
    elif resolver.issuer_group_count(
        resolved_subjects,
        timestamp=timestamp,
    ) > 1:
        decision = "mixed_issuer_passage_scoping"
    elif unresolved_company:
        decision = "unresolved_issuer_passage_abstention"
    elif not resolved_subjects:
        decision = "abstained_no_resolved_issuer"
    else:
        decision = "passage_scoping_required"
    return NewsScopeAnalysis(
        units=units,
        passages=passages,
        linked_tickers=linked,
        resolved_subjects=resolved_subjects,
        document_decision=decision,
        aggregation=aggregation,
    )


def extract_observed_reaction(text: str) -> ObservedReaction:
    match = MOVE_RE.search(text)
    if not match:
        return ObservedReaction()
    verb = match.group("verb").casefold()
    direction = "up" if verb in {
        "rose", "gained", "climbed", "jumped", "surged", "rallied", "advanced"
    } else "down"
    session_match = SESSION_RE.search(text)
    session = (
        session_match.group(1).casefold().replace(" ", "_").replace("-", "_")
        if session_match else ""
    )
    return ObservedReaction(
        direction=direction,
        move_pct=float(match.group("pct")),
        resulting_price=float(match.group("price")),
        market_session=session,
        evidence=match.group(0),
    )


def extract_reported_catalyst(text: str) -> str:
    match = CATALYST_RE.search(text)
    return re.sub(r"\s+", " ", match.group("catalyst")).strip() if match else ""


def _fragments(
    blocks: Sequence,
    *,
    resolver: NewsIssuerResolver,
    timestamp: str,
    linked_tickers: tuple[str, ...],
) -> tuple[_Fragment, ...]:
    output: list[_Fragment] = []
    heading_matches: tuple[IssuerMatch, ...] = ()
    source_lane = "article_header"
    ordinal = 0
    for block_index, block in enumerate(blocks):
        if block.kind == "renderer_provenance":
            source_lane = (
                "external_enrichment"
                if re.search(r"^Source\s+\[external:", block.text, re.I)
                else "provider_body"
                if re.search(r"^Source\s+\[provider_body:", block.text, re.I)
                else "article_attachment"
            )
            continue
        if block.kind == "heading":
            heading_matches = _subject_matches(
                block.text,
                resolver.resolve(
                    block.text,
                    timestamp=timestamp,
                    linked_tickers=linked_tickers,
                ),
                linked_tickers=linked_tickers,
            )
            continue
        if not block.semantic or block.kind == "blank":
            continue
        payload = _strip_renderer_label(block.text)
        if not payload:
            continue
        cursor = block.start
        for sentence in _split_sentences(payload):
            compact = re.sub(r"\s+", " ", sentence).strip()
            if len(compact) < 20:
                cursor += len(sentence)
                continue
            ordinal += 1
            matches = _subject_matches(
                compact,
                resolver.resolve(
                    compact,
                    timestamp=timestamp,
                    linked_tickers=linked_tickers,
                ),
                linked_tickers=linked_tickers,
            )
            output.append(
                _Fragment(
                    ordinal=ordinal,
                    text=compact,
                    start=cursor,
                    end=cursor + len(sentence),
                    block_index=block_index,
                    source_lane=source_lane,
                    heading_matches=heading_matches,
                    matches=matches,
                    unresolved_company_mention=_has_unresolved_company_mention(
                        compact,
                        resolver=resolver,
                        timestamp=timestamp,
                        linked_tickers=linked_tickers,
                    ),
                )
            )
            cursor += len(sentence)
    return tuple(output)


def _resolve_passages(
    fragments: tuple[_Fragment, ...],
    *,
    title_matches: tuple[IssuerMatch, ...],
    aggregation: bool,
) -> tuple[
    tuple[tuple[str, _Fragment, tuple[str, ...]], ...],
    tuple[PassageResolution, ...],
]:
    assignments: list[tuple[str, _Fragment, tuple[str, ...]]] = []
    passages: list[PassageResolution] = []
    title_subjects = {match.ticker for match in title_matches}
    previous_by_block: dict[int, str] = {}
    for fragment in fragments:
        if fragment.source_lane == "external_enrichment":
            passages.append(
                PassageResolution(
                    ordinal=fragment.ordinal,
                    text=fragment.text,
                    start=fragment.start,
                    end=fragment.end,
                    resolved_tickers=tuple(
                        sorted({match.ticker for match in fragment.matches})
                    ),
                    evidence=_match_evidence(fragment.matches),
                    decision="abstained_external_enrichment",
                )
            )
            continue
        direct = {match.ticker for match in fragment.matches}
        heading = {match.ticker for match in fragment.heading_matches}
        assigned = ""
        decision = ""
        flags: list[str] = []
        if fragment.unresolved_company_mention:
            decision = "abstained_unresolved_company_mention"
        elif len(direct) == 1:
            assigned = next(iter(direct))
            decision = "assigned_explicit_passage_issuer"
            flags.append("passage_explicit_issuer")
        elif len(direct) > 1:
            decision = "abstained_conflicting_issuer_mentions"
        elif len(heading) == 1:
            assigned = next(iter(heading))
            decision = "assigned_heading_issuer"
            flags.append("passage_inherited_heading_issuer")
        elif fragment.block_index in previous_by_block:
            assigned = previous_by_block[fragment.block_index]
            decision = "assigned_local_preceding_issuer"
            flags.append("passage_inherited_local_issuer")
        elif not aggregation and len(title_subjects) == 1:
            assigned = next(iter(title_subjects))
            decision = "assigned_title_issuer"
            flags.append("passage_inherited_title_issuer")
        else:
            decision = "abstained_unresolved_passage"
        if assigned:
            previous_by_block[fragment.block_index] = assigned
            assignments.append((assigned, fragment, tuple(flags)))
        passages.append(
            PassageResolution(
                ordinal=fragment.ordinal,
                text=fragment.text,
                start=fragment.start,
                end=fragment.end,
                resolved_tickers=tuple(sorted(direct)),
                evidence=_match_evidence(fragment.matches),
                decision=decision,
                assigned_ticker=assigned,
            )
        )
    return tuple(assignments), tuple(passages)


def _units_from_assignments(
    *,
    source_id: str,
    assignments: tuple[tuple[str, _Fragment, tuple[str, ...]], ...],
    aggregation: bool,
    mixed: bool,
) -> tuple[RelevantTextUnit, ...]:
    grouped: list[tuple[str, list[_Fragment], set[str]]] = []
    for ticker, fragment, flags in assignments:
        if grouped and grouped[-1][0] == ticker \
                and grouped[-1][1][-1].block_index == fragment.block_index:
            grouped[-1][1].append(fragment)
            grouped[-1][2].update(flags)
        else:
            grouped.append((ticker, [fragment], set(flags)))
    output: list[RelevantTextUnit] = []
    seen: set[tuple[str, str]] = set()
    for ticker, parts, flags in grouped:
        text = " ".join(part.text for part in parts).strip()
        key = (ticker, text.casefold())
        if len(text) < 20 or key in seen:
            continue
        seen.add(key)
        reaction = extract_observed_reaction(text)
        role = _passage_role(
            aggregation=aggregation,
            mixed=mixed,
            reaction=reaction,
            text=text,
        )
        ordinal = len(output) + 1
        output.append(
            RelevantTextUnit(
                corpus="news",
                source_id=source_id,
                unit_id=_unit_id(source_id, ordinal, ticker, text),
                ordinal=ordinal,
                role=role,
                text=text,
                start=parts[0].start,
                end=parts[-1].end,
                tickers=(ticker,),
                shared_context=False,
                observed_reaction=reaction,
                reported_catalyst=extract_reported_catalyst(text),
                extractor_version=NEWS_EXTRACTOR_VERSION,
                quality_flags=tuple(dict.fromkeys((
                    *sorted(flags),
                    *(
                        ("context_only_market_observation",)
                        if role in CONTEXT_ONLY_ROLES
                        else ()
                    ),
                ))),
            )
        )
    return tuple(output)


def _subject_matches(
    text: str,
    matches: tuple[IssuerMatch, ...],
    *,
    linked_tickers: tuple[str, ...],
) -> tuple[IssuerMatch, ...]:
    action = ANALYST_ACTION_RE.search(text)
    selected = matches
    if action and len(matches) >= 2:
        positions = {
            match.ticker: _match_position(text, match)
            for match in matches
        }
        targets = tuple(
            match
            for match in matches
            if positions[match.ticker] >= action.end()
        )
        selected = targets or matches
    return tuple(
        match
        for match in selected
        if (
            match.ticker in linked_tickers
            or any(value.startswith("symbol:") for value in match.evidence)
            or _issuer_is_event_subject(text, match)
        )
    )


def _match_position(text: str, match: IssuerMatch) -> int:
    lowered = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    positions: list[int] = []
    for value in match.evidence:
        kind, _, evidence = value.partition(":")
        if kind == "issuer_alias":
            position = lowered.find(evidence)
        else:
            position = text.upper().find(evidence.upper())
        if position >= 0:
            positions.append(position)
    return min(positions, default=-1)


def _issuer_is_event_subject(text: str, match: IssuerMatch) -> bool:
    position = _match_position(text, match)
    if position < 0:
        return False
    trailing = text[position : position + 180]
    return bool(EVENT_SUBJECT_VERB_RE.search(trailing))


def _single_document_role(text: str) -> str:
    if _is_analyst_text(text):
        return "analyst_opinion"
    return "primary_or_editorial_document"


def _passage_role(
    *,
    aggregation: bool,
    mixed: bool,
    reaction: ObservedReaction,
    text: str,
) -> str:
    if aggregation:
        return "ticker_market_observation"
    if _is_analyst_text(text):
        return "ticker_scoped_analyst_context" if mixed else "analyst_opinion"
    if mixed:
        return "ticker_scoped_editorial_context"
    if reaction.direction:
        return "editorial_reaction_explanation"
    return "primary_or_editorial_evidence"


def _is_analyst_text(text: str) -> bool:
    return bool(ANALYST_ROLE_RE.search(text))


def _has_unresolved_company_mention(
    text: str,
    *,
    resolver: NewsIssuerResolver,
    timestamp: str,
    linked_tickers: tuple[str, ...],
) -> bool:
    mentions = {
        match.group(0).strip(" ,.")
        for match in COMPANY_LIKE_RE.finditer(text)
    }
    return any(
        not resolver.resolve(
            mention,
            timestamp=timestamp,
            linked_tickers=linked_tickers,
        )
        for mention in mentions
    )


def _split_sentences(text: str) -> tuple[str, ...]:
    values: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?]\s+(?=[\"'(]*[A-Z$])", text):
        candidate = text[start : match.end() - 1].strip()
        last_word = candidate.rsplit(" ", 1)[-1].casefold() if candidate else ""
        if last_word in ABBREVIATIONS:
            continue
        if candidate:
            values.append(candidate)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        values.append(tail)
    return tuple(values)


def _strip_renderer_label(text: str) -> str:
    value = re.sub(r"^\s*(?:Title|Teaser|Summary|Body)\s*:\s*", "", text, flags=re.I)
    value = re.sub(r"^\s*[-*]\s*", "", value)
    return re.sub(r"^\s*(?:Gainers|Losers)\s+", "", value, flags=re.I).strip()


def _match_evidence(matches: Sequence[IssuerMatch]) -> tuple[str, ...]:
    return tuple(
        f"{match.ticker}<-{value}"
        for match in matches
        for value in match.evidence
    )


def _unit_id(source_id: str, ordinal: int, ticker: str, text: str) -> str:
    digest = hashlib.sha256(
        f"{ticker}\0{text}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{source_id}:news:{ordinal}:{digest}"


def _empty_analysis(
    linked: tuple[str, ...],
    resolved: tuple[str, ...],
    decision: str,
    aggregation: bool,
) -> NewsScopeAnalysis:
    return NewsScopeAnalysis(
        units=(),
        passages=(),
        linked_tickers=linked,
        resolved_subjects=resolved,
        document_decision=decision,
        aggregation=aggregation,
    )
