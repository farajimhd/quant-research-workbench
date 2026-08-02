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
    ARTICLE_ISSUER_RE,
    EXCHANGE_TICKER_RE,
    ISSUER_RESOLUTION_VERSION,
    IssuerMatch,
    NewsIssuerResolver,
    normalize_issuer_alias,
)
from .schema import NEWS_EXTRACTOR_VERSION, ObservedReaction, RelevantTextUnit


MOVE_RE = re.compile(
    r"(?<!\w)(?:(?P<ticker>[A-Z][A-Z0-9.-]{0,9})\s+|"
    r"\((?i:(?:NASDAQ|NYSE|NYSEAMERICAN|NYSE\s+AMERICAN|AMEX|"
    r"OTC(?:QX|QB)?|TSX|TSXV|CSE))\s*:\s*"
    r"(?P<exchange_ticker>[A-Z][A-Z0-9.-]{0,9})\)\s+)?"
    r"(?i:(?:(?:shares?|stock)\s+)?"
    r"(?:(?:is|are|was|were)\s+)?"
    r"(?:(?:trading|moved)\s+)?"
    r"(?P<verb>up|higher|rose|gained|climbed|jumped|surged|rallied|"
    r"advanced|increased|upwards|down|lower|fell|dropped|declined|decreased|"
    r"downwards|"
    r"slid|plunged|tumbled|lost)(?:\s+by)?\s+"
    r"(?P<pct>\d+(?:\.\d+)?)%\s*(?:to|at)\s*"
    r"\$(?P<price>\d[\d,]*(?:\.\d+)?))",
)
SESSION_RE = re.compile(
    r"\b(pre[- ]market|premarket|after[- ]hours|after[- ]market|post[- ]market|"
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
    r"\b(?:gainers|losers)\s+and\s+(?:decliners|movers)\b|"
    r"\bmarket\s+(?:wrap|update|today|recap)\b|"
    r"\bstocks?\s+to\s+watch\b|\bdaily\s+(?:biotech\s+)?pulse\b|"
    r"\bweekend\s+m\s*&\s*a\s+chatter\b|\bmovers?\s*&\s*shakers?\b|"
    r"\ba\s+peek\s+into\s+the\s+markets\b",
    re.IGNORECASE,
)
ANALYST_ACTION_RE = re.compile(
    r"\b(?:upgrade[sd]?|downgrade[sd]?|maintain(?:s|ed)?|reiterate[sd]?|"
    r"initiate[sd]?|resume[sd]?|"
    r"price\s+target)\b",
    re.IGNORECASE,
)
ANALYST_ROLE_RE = re.compile(
    r"\b(?:analyst|brokerage|price\s+target|analyst\s+rating|"
    r"research\s+coverage)\b|"
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
RELATIONSHIP_RE = re.compile(
    r"\b(?:agree[sd]?\s+to\s+acquire|acquisition|merger|"
    r"partner(?:s|ed|ship)?|collaborat(?:e[sd]?|ion)|joint\s+venture|"
    r"license(?:s|d)?|sue[sd]?|lawsuit|litigation|settle[sd]?|"
    r"settlement)\b",
    re.IGNORECASE,
)
COORDINATED_PREDICATE_BOUNDARY_RE = re.compile(
    r"\s+(?:and|but)\s+(?="
    r"(?:(?:it|they|the\s+company)\s+)?"
    r"(?:plans?|will|expects?|intends?|announces?|resumes?|restarts?|"
    r"raises?|cuts?|reports?|files?|launches?|withdraws?|terminates?|"
    r"suspends?|declares?|authorizes?)\b)",
    re.IGNORECASE,
)
EVENT_EVIDENCE_RE = re.compile(
    r"\b(?:clinical\s+(?:trial|study|update|data)|"
    r"interim\s+(?:analysis|data)|enrollment|primary\s+endpoint|"
    r"topline\s+data|fda|guidance|earnings|revenue|margin|offering|"
    r"financing|acqui(?:re|sition)|merger|partner(?:ship|ed)?|"
    r"collaborat(?:ion|ed)?|contract|order|upgrade[sd]?|downgrade[sd]?|"
    r"price\s+target|lawsuit|litigation|settlement|bankruptcy|"
    r"restructuring|dividend|buyback|stock\s+split|share\s+split)\b",
    re.IGNORECASE,
)
ACQUISITION_RE = re.compile(
    r"\b(?:agree[sd]?\s+to\s+acquire|acquire[sd]?|acquisition(?:\s+of)?)\b",
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
    linked = tuple(dict.fromkeys(
        _normalize_provider_ticker(value)
        for value in tickers
        if _normalize_provider_ticker(value)
    ))
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
    resolved_subjects, subject_resolution_flags = _resolve_venue_actor_subjects(
        title,
        linked_tickers=linked,
        resolved_subjects=resolved_subjects,
    )
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
            semantic_text=semantic,
            start=min((fragment.start for fragment in primary_fragments), default=0),
            end=max((fragment.end for fragment in primary_fragments), default=len(clean)),
            tickers=(subject,),
            shared_context=False,
            event_id=_event_id(source_id, semantic),
            event_tickers=(subject,),
            issuer_role="primary_subject",
            evidence_scope="ticker_specific",
            trigger_candidate=not aggregation and _has_event_evidence(semantic),
            observed_reaction=reaction,
            reported_catalyst=(
                extract_reported_catalyst(semantic)
                if reaction.direction else ""
            ),
            extractor_version=NEWS_EXTRACTOR_VERSION,
            quality_flags=(
                "document_single_resolved_issuer",
                *subject_resolution_flags,
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
        publication_text="\n".join(
            fragment.text for fragment in primary_fragments
        ).strip(),
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


def extract_observed_reaction(
    text: str,
    *,
    ticker: str = "",
) -> ObservedReaction:
    matches = tuple(MOVE_RE.finditer(text))
    if not matches:
        return ObservedReaction()
    ticker = ticker.upper()
    exact = tuple(
        match
        for match in matches
        if str(
            match.group("ticker")
            or match.group("exchange_ticker")
            or ""
        ).upper() == ticker
    )
    unscoped = tuple(
        match
        for match in matches
        if not (match.group("ticker") or match.group("exchange_ticker"))
    )
    # An explicit ticker always wins. A tickerless reaction may be inherited
    # only when no other explicit ticker appears in the same evidence. This
    # prevents one issuer's reported move from leaking to another issuer in a
    # multi-company article.
    match = (
        exact[0]
        if ticker and exact
        else unscoped[0]
        if ticker and len(matches) == 1 and unscoped
        else matches[0]
        if not ticker
        else None
    )
    if match is None:
        return ObservedReaction()
    verb = match.group("verb").casefold()
    direction = "up" if verb in {
        "up", "higher", "rose", "gained", "climbed", "jumped", "surged",
        "rallied", "advanced", "increased", "upwards",
    } else "down"
    session_match = SESSION_RE.search(text)
    session = (
        session_match.group(1).casefold().replace(" ", "_").replace("-", "_")
        if session_match else ""
    )
    return ObservedReaction(
        direction=direction,
        move_pct=float(match.group("pct")),
        resulting_price=float(match.group("price").replace(",", "")),
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
    relational_subject_by_block: dict[int, str] = {}
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
        assigned_tickers: tuple[str, ...] = ()
        decision = ""
        flags: list[str] = []
        if len(direct) == 1:
            assigned_tickers = tuple(direct)
            decision = "assigned_explicit_passage_issuer"
            flags.append("passage_explicit_issuer")
            if fragment.unresolved_company_mention:
                decision = "assigned_known_issuer_with_unresolved_counterparty"
                flags.append("unresolved_counterparty_or_background")
        elif len(direct) > 1:
            assigned_tickers = tuple(sorted(direct))
            relational = bool(RELATIONSHIP_RE.search(fragment.text))
            decision = (
                "assigned_shared_relational_event"
                if relational
                else "assigned_shared_multi_issuer_evidence"
            )
            flags.extend(("passage_explicit_issuer", "shared_issuer_evidence"))
            if relational:
                lead = _relational_lead_subject(fragment)
                if lead:
                    previous_by_block[fragment.block_index] = lead
                    relational_subject_by_block[fragment.block_index] = lead
        elif fragment.unresolved_company_mention:
            decision = "abstained_unresolved_company_mention"
        elif len(heading) == 1:
            assigned_tickers = tuple(heading)
            decision = "assigned_heading_issuer"
            flags.append("passage_inherited_heading_issuer")
        elif fragment.block_index in previous_by_block:
            assigned_tickers = (previous_by_block[fragment.block_index],)
            if fragment.block_index in relational_subject_by_block:
                decision = "assigned_relational_subject_continuation"
                flags.append("passage_inherited_relational_subject")
            else:
                decision = "assigned_local_preceding_issuer"
                flags.append("passage_inherited_local_issuer")
        elif not aggregation and len(title_subjects) == 1:
            assigned_tickers = tuple(title_subjects)
            decision = "assigned_title_issuer"
            flags.append("passage_inherited_title_issuer")
        else:
            decision = "abstained_unresolved_passage"
        if assigned_tickers:
            if len(assigned_tickers) == 1:
                previous_by_block[fragment.block_index] = assigned_tickers[0]
            for assigned in assigned_tickers:
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
                assigned_ticker=",".join(assigned_tickers),
            )
        )
    return tuple(assignments), tuple(passages)


def _units_from_assignments(
    *,
    source_id: str,
    assignments: tuple[tuple[str, _Fragment, tuple[str, ...]], ...],
    publication_text: str,
    aggregation: bool,
    mixed: bool,
) -> tuple[RelevantTextUnit, ...]:
    # Parse once, then gather issuer evidence. The complete provider
    # publication remains available on every issuer unit; only semantic_text is
    # scoped, so an acquisition or partnership is not destructively split.
    grouped: dict[str, tuple[list[_Fragment], set[str]]] = {}
    for ticker, fragment, flags in assignments:
        parts, collected_flags = grouped.setdefault(ticker, ([], set()))
        if not parts or parts[-1].ordinal != fragment.ordinal:
            parts.append(fragment)
        collected_flags.update(flags)
    output: list[RelevantTextUnit] = []
    for ticker, (parts, flags) in grouped.items():
        semantic_parts = [
            part for part in parts
            if aggregation
            or _has_event_evidence(part.text)
            or not extract_observed_reaction(part.text).direction
        ]
        semantic_text = " ".join(
            part.text for part in (semantic_parts or parts)
        ).strip()
        if len(semantic_text) < 20:
            continue
        reaction_text = " ".join(part.text for part in parts)
        reaction = extract_observed_reaction(
            reaction_text,
            ticker=ticker,
        )
        event_tickers = tuple(sorted({
            assigned
            for assigned, fragment, _ in assignments
            if fragment in parts
        }))
        shared_event = len(event_tickers) > 1
        trigger_candidate = (
            not aggregation
            and any(_has_event_evidence(part.text) for part in parts)
        )
        role = _passage_role(
            aggregation=aggregation,
            mixed=mixed,
            reaction=reaction,
            text=semantic_text,
            trigger_candidate=trigger_candidate,
        )
        ordinal = len(output) + 1
        event_id = _event_id(
            source_id,
            " ".join(
                part.text for part in parts
                if _has_event_evidence(part.text)
            ) or semantic_text,
        )
        output.append(
            RelevantTextUnit(
                corpus="news",
                source_id=source_id,
                unit_id=_unit_id(source_id, ordinal, ticker, semantic_text),
                ordinal=ordinal,
                role=role,
                text=publication_text,
                semantic_text=semantic_text,
                start=parts[0].start,
                end=parts[-1].end,
                tickers=(ticker,),
                shared_context=shared_event,
                event_id=event_id,
                event_tickers=event_tickers or (ticker,),
                issuer_role=_issuer_role(ticker, parts, event_tickers),
                evidence_scope=(
                    "shared_relational"
                    if shared_event and any(
                        RELATIONSHIP_RE.search(part.text) for part in parts
                    )
                    else "shared_ambiguous"
                    if "unresolved_counterparty_or_background" in flags
                    else "shared_ambiguous"
                    if shared_event
                    else "ticker_specific"
                ),
                trigger_candidate=trigger_candidate,
                observed_reaction=reaction,
                reported_catalyst=extract_reported_catalyst(reaction_text),
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
    article_pairs = tuple(
        (
            normalize_issuer_alias(match.group("name")),
            match.group("ticker").upper(),
        )
        for match in ARTICLE_ISSUER_RE.finditer(text)
    )
    if article_pairs:
        matches = tuple(
            match
            for match in matches
            if not any(
                pair_ticker != match.ticker
                and any(
                    value.startswith("issuer_alias:")
                    and pair_name.startswith(value.partition(":")[2])
                    for value in match.evidence
                )
                for pair_name, pair_ticker in article_pairs
            )
        )
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


_NASDAQ_VENUE_HALT_ACTOR_RE = re.compile(
    r"\bnasdaq\s+"
    r"(?:comments?|commented|says?|said|tells?|told|confirms?|confirmed|notes?|noted)\s+"
    r"(?:to\s+[a-z][a-z0-9.-]*\s+)?(?:on|that)\b"
    r"(?=.{0,240}\b(?:trading\s+halt|stock\s+(?:remains?\s+)?halted|"
    r"shares?\s+(?:remains?\s+)?halted)\b)",
    re.I | re.S,
)


def _resolve_venue_actor_subjects(
    title: str,
    *,
    linked_tickers: tuple[str, ...],
    resolved_subjects: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Separate a listed venue operator from the security affected by its action.

    ``Nasdaq`` is both an exchange role and an issuer alias for ``NDAQ``.  A
    provider may consequently link NDAQ when Nasdaq is merely the venue
    speaking about another security.  Provider links remain candidates, but a
    venue-actor construction plus exactly one other linked instrument is
    stronger affected-subject evidence than the venue's issuer alias.

    The rule is deliberately narrow: it does not suppress NDAQ in ordinary
    issuer news, when NDAQ is the only linked instrument, or when several
    possible affected instruments would require guessing.
    """
    linked = tuple(dict.fromkeys(value.upper() for value in linked_tickers if value))
    if "NDAQ" not in linked or not _NASDAQ_VENUE_HALT_ACTOR_RE.search(title):
        return resolved_subjects, ()
    affected = tuple(value for value in linked if value != "NDAQ")
    if len(affected) != 1:
        return resolved_subjects, ()
    return affected, (
        "venue_actor_disambiguated_from_listed_issuer",
        "provider_link_used_as_affected_subject_candidate",
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
    trigger_candidate: bool,
) -> str:
    if aggregation:
        return "ticker_market_observation"
    if trigger_candidate:
        return "analyst_opinion" if _is_analyst_text(text) else "issuer_event_document"
    if _is_analyst_text(text):
        return "ticker_scoped_analyst_context"
    if mixed:
        return "ticker_scoped_editorial_context"
    if reaction.direction:
        return "editorial_reaction_explanation"
    return "primary_or_editorial_evidence"


def _is_analyst_text(text: str) -> bool:
    return bool(ANALYST_ROLE_RE.search(text))


def _normalize_provider_ticker(value: str) -> str:
    ticker = str(value or "").upper().strip()
    if not ticker:
        return ""
    exchange_pair = EXCHANGE_TICKER_RE.fullmatch(ticker)
    return exchange_pair.group(1).upper() if exchange_pair else ticker


def _has_event_evidence(text: str) -> bool:
    if extract_observed_reaction(text).direction and not (
        EVENT_EVIDENCE_RE.search(text) or RELATIONSHIP_RE.search(text)
    ):
        return False
    return bool(
        EVENT_SUBJECT_VERB_RE.search(text)
        or EVENT_EVIDENCE_RE.search(text)
        or RELATIONSHIP_RE.search(text)
    )


def _issuer_role(
    ticker: str,
    parts: Sequence[_Fragment],
    event_tickers: tuple[str, ...],
) -> str:
    relational = next(
        (part for part in parts if ACQUISITION_RE.search(part.text)),
        None,
    ) or next(
        (part for part in parts if RELATIONSHIP_RE.search(part.text)),
        None,
    )
    if relational is None or len(event_tickers) <= 1:
        return "primary_subject"
    acquisition = ACQUISITION_RE.search(relational.text)
    if acquisition:
        positions = {
            match.ticker: _match_position(relational.text, match)
            for match in relational.matches
            if match.ticker in event_tickers
        }
        position = positions.get(ticker, -1)
        if position >= 0:
            return "acquirer" if position < acquisition.start() else "target"
    return "affected_participant"


def _relational_lead_subject(fragment: _Fragment) -> str:
    """Return the grammatical lead whose omitted subject continues next clause.

    Relational clauses are still assigned to every explicitly named participant,
    but a following subjectless clause (for example, ``; plans to restart its
    buyback``) inherits only the participant before the relational predicate.
    """
    relation = ACQUISITION_RE.search(fragment.text) or RELATIONSHIP_RE.search(
        fragment.text
    )
    if relation is None:
        return ""
    preceding = tuple(
        (position, match.ticker)
        for match in fragment.matches
        if (position := _match_position(fragment.text, match)) >= 0
        and position < relation.start()
    )
    return max(preceding, default=(-1, ""))[1]


def _event_id(source_id: str, text: str) -> str:
    digest = hashlib.sha256(text.casefold().encode("utf-8")).hexdigest()[:16]
    return f"{source_id}:event:{digest}"


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
    """Split semantic clauses without destructively separating relationships.

    A semicolon or a coordinated new predicate begins a new evidence clause.
    The relationship clause retains all named participants, while subsequent
    subjectless clauses can inherit its grammatical lead in ``_resolve_passages``.
    """
    values: list[str] = []
    start = 0
    boundaries = re.compile(
        rf";|[.!?]\s+(?=[\"'(]*[A-Z$])|"
        rf"{COORDINATED_PREDICATE_BOUNDARY_RE.pattern}",
        re.IGNORECASE,
    )
    for match in boundaries.finditer(text):
        candidate_end = match.start() + 1 if match.group(0)[:1] in ".!?" else match.start()
        candidate = text[start:candidate_end].strip()
        last_word = candidate.rsplit(" ", 1)[-1].casefold() if candidate else ""
        if match.group(0)[:1] == "." and last_word in ABBREVIATIONS:
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
