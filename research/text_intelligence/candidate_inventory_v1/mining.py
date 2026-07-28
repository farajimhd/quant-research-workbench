from __future__ import annotations

import hashlib
import heapq
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .normalize import candidate_ngrams, normalize_financial_text, tokens
from .seeds import PHRASE_TO_CONCEPT


@dataclass(frozen=True, slots=True)
class SourceDocument:
    corpus: str
    source_id: str
    timestamp: str
    title: str
    text: str
    entity_terms: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CandidateStats:
    phrase: str
    token_count: int
    document_count: int = 0
    occurrence_count: int = 0
    error_bound: int = 0
    first_seen: str = ""
    last_seen: str = ""
    year_mask: int = 0
    headline_documents: int = 0
    body_documents: int = 0
    examples: list[dict[str, str]] = field(default_factory=list)
    concept: str = ""

    def merge(self, other: "CandidateStats", example_limit: int) -> None:
        self.document_count += other.document_count
        self.occurrence_count += other.occurrence_count
        self.error_bound += other.error_bound
        self.first_seen = earliest(self.first_seen, other.first_seen)
        self.last_seen = latest(self.last_seen, other.last_seen)
        self.year_mask |= other.year_mask
        self.headline_documents += other.headline_documents
        self.body_documents += other.body_documents
        if not self.concept:
            self.concept = other.concept
        self.examples = merge_examples(self.examples, other.examples, example_limit)


@dataclass(slots=True)
class TokenStats:
    token: str
    document_count: int = 0
    error_bound: int = 0


@dataclass(slots=True)
class ValueTypeStats:
    value_type: str
    document_count: int = 0
    occurrence_count: int = 0
    examples: list[dict[str, str]] = field(default_factory=list)

    def merge(self, other: "ValueTypeStats", example_limit: int) -> None:
        self.document_count += other.document_count
        self.occurrence_count += other.occurrence_count
        self.examples = merge_examples(self.examples, other.examples, example_limit)


@dataclass(slots=True)
class MiningCounters:
    documents: int = 0
    characters: int = 0
    values: int = 0
    candidates_observed: int = 0
    candidate_truncated_documents: int = 0
    failed_documents: int = 0

    def merge(self, other: "MiningCounters") -> None:
        self.documents += other.documents
        self.characters += other.characters
        self.values += other.values
        self.candidates_observed += other.candidates_observed
        self.candidate_truncated_documents += other.candidate_truncated_documents
        self.failed_documents += other.failed_documents


class CandidateAccumulator:
    """Bounded Space-Saving inventory with article-level presence counts."""

    def __init__(
        self,
        *,
        corpus: str,
        capacity: int,
        example_limit: int,
        evidence_chars: int,
        min_ngram: int,
        max_ngram: int,
        max_unique_per_document: int,
    ) -> None:
        self.corpus = corpus
        self.capacity = int(capacity)
        self.example_limit = int(example_limit)
        self.evidence_chars = int(evidence_chars)
        self.min_ngram = int(min_ngram)
        self.max_ngram = int(max_ngram)
        self.max_unique_per_document = int(max_unique_per_document)
        self.candidates: dict[str, CandidateStats] = {}
        self.tokens: dict[str, TokenStats] = {}
        self.values: dict[str, ValueTypeStats] = {}
        self.counters = MiningCounters()
        self._candidate_heap: list[tuple[int, int, str]] = []
        self._token_heap: list[tuple[int, int, str]] = []
        self._serial = 0
        self._token_capacity = max(10_000, min(self.capacity, 100_000))

    def add_document(self, document: SourceDocument) -> None:
        if document.corpus != self.corpus:
            raise ValueError(
                f"document corpus {document.corpus!r} does not match accumulator {self.corpus!r}"
            )
        timestamp = str(document.timestamp or "")
        year = timestamp_year(timestamp)
        body_text = mining_text(document)
        normalized_body = normalize_financial_text(
            body_text,
            entity_terms=document.entity_terms,
            evidence_chars=self.evidence_chars,
        )
        normalized_title = normalize_financial_text(
            document.title,
            entity_terms=document.entity_terms,
            evidence_chars=self.evidence_chars,
        )
        self.counters.documents += 1
        self.counters.characters += len(body_text)
        self.counters.values += len(normalized_body.values) + len(normalized_title.values)
        self._add_values(document, normalized_title.values + normalized_body.values)
        token_presence = set(tokens(normalized_title.text)) | set(tokens(normalized_body.text))
        for token in token_presence:
            self._add_token(token, 1)

        occurrences: Counter[str] = Counter()
        locations: dict[str, set[str]] = {}
        sizes: dict[str, int] = {}
        for location, value in (("headline", normalized_title.text), ("body", normalized_body.text)):
            for phrase, size in candidate_ngrams(
                value,
                min_ngram=self.min_ngram,
                max_ngram=self.max_ngram,
            ):
                occurrences[phrase] += 1
                locations.setdefault(phrase, set()).add(location)
                sizes[phrase] = size
        for seed_phrase in PHRASE_TO_CONCEPT:
            headline_count = normalized_title.text.count(seed_phrase)
            body_count = normalized_body.text.count(seed_phrase)
            count = headline_count + body_count
            if count:
                occurrences[seed_phrase] += count
                seed_locations = locations.setdefault(seed_phrase, set())
                if headline_count:
                    seed_locations.add("headline")
                if body_count:
                    seed_locations.add("body")
                sizes[seed_phrase] = len(seed_phrase.split())

        limit = self.max_unique_per_document
        if limit and len(occurrences) > limit:
            self.counters.candidate_truncated_documents += 1
            ordered = sorted(
                occurrences,
                key=lambda phrase: (
                    phrase not in PHRASE_TO_CONCEPT,
                    -occurrences[phrase],
                    -len(phrase.split()),
                    phrase,
                ),
            )[:limit]
            occurrences = Counter({phrase: occurrences[phrase] for phrase in ordered})
            locations = {phrase: locations[phrase] for phrase in ordered}
            sizes = {phrase: sizes[phrase] for phrase in ordered}

        self.counters.candidates_observed += len(occurrences)
        for phrase, count in occurrences.items():
            self._add_candidate(
                phrase=phrase,
                token_count=sizes[phrase],
                document_count=1,
                occurrence_count=count,
                timestamp=timestamp,
                year=year,
                locations=locations[phrase],
                example={
                    "source_id": document.source_id,
                    "timestamp": timestamp,
                    "title": document.title[: self.evidence_chars],
                },
                concept=PHRASE_TO_CONCEPT.get(phrase, ""),
            )

    def merge(self, other: "CandidateAccumulator") -> None:
        if other.corpus != self.corpus:
            raise ValueError("cannot merge accumulators from different corpora")
        self.counters.merge(other.counters)
        for entry in other.candidates.values():
            self._merge_candidate(entry)
        for entry in other.tokens.values():
            self._add_token(entry.token, entry.document_count, entry.error_bound)
        for value_type, entry in other.values.items():
            current = self.values.get(value_type)
            if current is None:
                self.values[value_type] = ValueTypeStats(
                    value_type=value_type,
                    document_count=entry.document_count,
                    occurrence_count=entry.occurrence_count,
                    examples=list(entry.examples),
                )
            else:
                current.merge(entry, self.example_limit)

    def _add_candidate(
        self,
        *,
        phrase: str,
        token_count: int,
        document_count: int,
        occurrence_count: int,
        timestamp: str,
        year: int | None,
        locations: set[str],
        example: dict[str, str],
        concept: str,
        error_bound: int = 0,
    ) -> None:
        current = self.candidates.get(phrase)
        if current is None:
            current = self._new_candidate(phrase, token_count, document_count, error_bound)
        else:
            current.document_count += document_count
            current.error_bound += error_bound
        current.occurrence_count += occurrence_count
        current.first_seen = earliest(current.first_seen, timestamp)
        current.last_seen = latest(current.last_seen, timestamp)
        if year is not None and 1900 <= year <= 2200:
            current.year_mask |= 1 << (year - 1900)
        if "headline" in locations:
            current.headline_documents += document_count
        if "body" in locations:
            current.body_documents += document_count
        if concept and not current.concept:
            current.concept = concept
        current.examples = merge_examples(current.examples, [example], self.example_limit)
        self._push_candidate(current)

    def _merge_candidate(self, entry: CandidateStats) -> None:
        current = self.candidates.get(entry.phrase)
        if current is None:
            current = self._new_candidate(
                entry.phrase,
                entry.token_count,
                entry.document_count,
                entry.error_bound,
            )
            current.occurrence_count = entry.occurrence_count
            current.first_seen = entry.first_seen
            current.last_seen = entry.last_seen
            current.year_mask = entry.year_mask
            current.headline_documents = entry.headline_documents
            current.body_documents = entry.body_documents
            current.examples = list(entry.examples)
            current.concept = entry.concept
        else:
            current.merge(entry, self.example_limit)
        self._push_candidate(current)

    def _new_candidate(
        self,
        phrase: str,
        token_count: int,
        document_count: int,
        error_bound: int,
    ) -> CandidateStats:
        if len(self.candidates) >= self.capacity:
            minimum = self._pop_candidate_minimum()
            if minimum is not None:
                self.candidates.pop(minimum.phrase, None)
                error_bound += minimum.document_count
                document_count += minimum.document_count
        entry = CandidateStats(
            phrase=phrase,
            token_count=token_count,
            document_count=document_count,
            error_bound=error_bound,
            concept=PHRASE_TO_CONCEPT.get(phrase, ""),
        )
        self.candidates[phrase] = entry
        return entry

    def _push_candidate(self, entry: CandidateStats) -> None:
        self._serial += 1
        heapq.heappush(
            self._candidate_heap,
            (entry.document_count, self._serial, entry.phrase),
        )

    def _pop_candidate_minimum(self) -> CandidateStats | None:
        while self._candidate_heap:
            count, _, phrase = heapq.heappop(self._candidate_heap)
            entry = self.candidates.get(phrase)
            if entry is not None and entry.document_count == count:
                return entry
        if not self.candidates:
            return None
        return min(self.candidates.values(), key=lambda value: (value.document_count, value.phrase))

    def _add_token(self, token: str, count: int, error_bound: int = 0) -> None:
        current = self.tokens.get(token)
        if current is None:
            if len(self.tokens) >= self._token_capacity:
                minimum = self._pop_token_minimum()
                if minimum is not None:
                    self.tokens.pop(minimum.token, None)
                    error_bound += minimum.document_count
                    count += minimum.document_count
            current = TokenStats(token=token)
            self.tokens[token] = current
        current.document_count += count
        current.error_bound += error_bound
        self._serial += 1
        heapq.heappush(self._token_heap, (current.document_count, self._serial, token))

    def _pop_token_minimum(self) -> TokenStats | None:
        while self._token_heap:
            count, _, token = heapq.heappop(self._token_heap)
            entry = self.tokens.get(token)
            if entry is not None and entry.document_count == count:
                return entry
        if not self.tokens:
            return None
        return min(self.tokens.values(), key=lambda value: (value.document_count, value.token))

    def _add_values(self, document: SourceDocument, values: Iterable[Any]) -> None:
        grouped: dict[str, list[Any]] = {}
        for value in values:
            grouped.setdefault(value.value_type, []).append(value)
        for value_type, rows in grouped.items():
            current = self.values.setdefault(value_type, ValueTypeStats(value_type=value_type))
            current.document_count += 1
            current.occurrence_count += len(rows)
            examples = [
                {
                    "source_id": document.source_id,
                    "timestamp": document.timestamp,
                    "raw": row.raw,
                    "normalized_number": row.normalized_number,
                    "context": row.context,
                }
                for row in rows[: self.example_limit]
            ]
            current.examples = merge_examples(current.examples, examples, self.example_limit)


def candidate_id(corpus: str, phrase: str) -> str:
    return hashlib.sha256(f"{corpus}\0{phrase}".encode("utf-8")).hexdigest()


NEWS_RENDER_HEADER_RE = re.compile(
    r"\A(?:Title:[^\n]*\n)?(?:Source\s+\[[^\]]+\]\s+\S+\s*\n)?",
    re.IGNORECASE,
)
NEWS_RENDER_PROVENANCE_LINE_RE = re.compile(
    r"^(?:Source\s+\[[^\]]+\]\s+\S+|Image:\s+src=\S+)\s*$",
    re.IGNORECASE,
)


def mining_text(document: SourceDocument) -> str:
    """Return semantic content, excluding renderer provenance scaffolding."""
    text = str(document.text or "")
    if document.corpus == "news":
        without_header = NEWS_RENDER_HEADER_RE.sub("", text, count=1)
        return "\n".join(
            line
            for line in without_header.splitlines()
            if not NEWS_RENDER_PROVENANCE_LINE_RE.match(line)
        )
    return text


def merge_examples(
    left: list[dict[str, str]],
    right: list[dict[str, str]],
    limit: int,
) -> list[dict[str, str]]:
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    for value in [*left, *right]:
        key = (str(value.get("source_id", "")), str(value.get("raw", value.get("title", ""))))
        by_key.setdefault(key, value)
    return [
        by_key[key]
        for key in sorted(by_key, key=lambda item: hashlib.sha256(repr(item).encode()).hexdigest())[:limit]
    ]


def timestamp_year(value: str) -> int | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).year
    except (TypeError, ValueError):
        match = str(value)[:4]
        return int(match) if match.isdigit() else None


def year_count(mask: int) -> int:
    return int(mask).bit_count()


def earliest(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return min(left, right)


def latest(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)
