from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    quote_ident,
    sql_string,
)
from research.text_intelligence.news_synthesis_v1.engine import ENGINE_VERSION
from research.text_intelligence.news_synthesis_v1.storage import SYNTHESIS_TABLE

from .config import INVENTORY_VERSION, NORMALIZER_VERSION, CandidateInventoryConfig
from .mining import SourceDocument, mining_text
from .normalize import candidate_ngrams, normalize_financial_text, tokens
from .pipeline import KEYWORD_STOP_WORDS, assert_external_runtime_root
from .seeds import PHRASE_TO_CONCEPT


AUDIT_VERSION = "text_candidate_method_audit_v1"
NEWS_STRATA: tuple[tuple[str, str, str], ...] = (
    (
        "financing_offering",
        "Financing or offering language",
        """
        positionCaseInsensitiveUTF8(e.title, 'offering') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'financing') > 0
        OR positionCaseInsensitiveUTF8(r.rendered_text, 'registered direct offering') > 0
        """,
    ),
    (
        "clinical_regulatory",
        "Clinical or FDA language",
        """
        positionCaseInsensitiveUTF8(e.title, 'FDA') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'clinical') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'trial') > 0
        """,
    ),
    (
        "earnings_guidance",
        "Earnings or guidance language",
        """
        positionCaseInsensitiveUTF8(e.title, 'earnings') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'revenue') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'guidance') > 0
        """,
    ),
    (
        "corporate_transaction",
        "Merger, acquisition, or contract language",
        """
        positionCaseInsensitiveUTF8(e.title, 'acquisition') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'merger') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'contract') > 0
        """,
    ),
    (
        "editorial_followup",
        "Editorial, mover, analyst, or why-moving format",
        """
        positionCaseInsensitiveUTF8(e.title, 'why') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'movers') > 0
        OR positionCaseInsensitiveUTF8(e.title, 'stocks moving') > 0
        OR has(e.channels, 'Analyst Ratings')
        """,
    ),
)

SEC_STRATA: tuple[tuple[str, str], ...] = (
    ("primary_document", "Primary filing document"),
    ("prospectus", "Prospectus"),
    ("press_release_exhibit", "Press-release exhibit"),
    ("material_exhibit", "Material exhibit"),
    ("other_text_exhibit", "Other text exhibit"),
)


@dataclass(frozen=True, slots=True)
class AuditCase:
    corpus: str
    stratum: str
    rationale: str
    row: dict[str, Any]


def create_audits(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
    output_root: Path,
) -> list[Path]:
    assert_external_runtime_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    cases = [*fetch_news_cases(client, config), *fetch_sec_cases(client, config)]
    if len(cases) != 10:
        raise RuntimeError(f"expected exactly 10 audit cases, received {len(cases)}")
    files: list[Path] = []
    manifest_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        document = document_from_case(case)
        report = render_case(case, document)
        filename = f"{index:02d}_{case.corpus}_{safe_slug(case.stratum)}.md"
        path = output_root / filename
        write_text_atomic(path, report)
        files.append(path)
        manifest_cases.append(
            {
                "file": filename,
                "corpus": case.corpus,
                "stratum": case.stratum,
                "source_id": document.source_id,
                "source_timestamp": document.timestamp,
                "source_sha256": sha256_text(document.text),
            }
        )
        print(
            f"[{index}/10] {case.corpus.upper():4} {case.stratum}"
            f" | chars={len(document.text):,} | {filename}",
            flush=True,
        )
    write_json_atomic(
        output_root / "manifest.json",
        {
            "audit_version": AUDIT_VERSION,
            "inventory_version": INVENTORY_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
            "created_at_utc": utc_now(),
            "case_count": len(files),
            "cases": manifest_cases,
        },
    )
    return files


def fetch_news_cases(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
) -> list[AuditCase]:
    db = quote_ident(config.database)
    event = quote_ident(config.news_event_table)
    rendered = quote_ident(config.news_rendered_table)
    excluded: list[str] = []
    cases: list[AuditCase] = []
    for stratum, rationale, predicate in NEWS_STRATA:
        excluded_sql = (
            " AND e.canonical_news_id NOT IN ("
            + ",".join(sql_string(value) for value in excluded)
            + ")"
            if excluded
            else ""
        )
        sql = f"""
SELECT
 e.canonical_news_id AS source_id,
 toString(e.published_at_utc) AS source_timestamp,
 e.provider_article_id,
 e.title,
 r.rendered_text AS text,
 e.tickers AS entity_terms,
 e.tickers,
 e.channels,
 e.provider_tags,
 e.links,
 e.author,
 e.url_domain,
 e.article_url,
 r.renderer_version,
 r.text_contract,
 r.quality_flags,
 r.rendered_text_hash,
 s.synthesis_json
FROM {db}.{event} AS e FINAL
INNER JOIN {db}.{rendered} AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
INNER JOIN {db}.{quote_ident(SYNTHESIS_TABLE)} AS s FINAL
 ON s.canonical_news_id=e.canonical_news_id
 AND s.engine_version={sql_string(ENGINE_VERSION)}
WHERE e.published_at_utc >= toDateTime64('2022-01-01', 9, 'UTC')
  AND e.published_at_utc < toDateTime64({sql_string(config.end_date_exclusive)}, 9, 'UTC')
  AND lengthUTF8(r.rendered_text) BETWEEN 1200 AND 20000
  AND ({predicate})
  {excluded_sql}
ORDER BY cityHash64(concat(e.canonical_news_id, {sql_string(stratum)}))
LIMIT 1
FORMAT JSONEachRow
"""
        row = one_json_row(client, sql, f"news stratum {stratum}")
        excluded.append(str(row["source_id"]))
        cases.append(AuditCase("news", stratum, rationale, row))
    return cases


def fetch_sec_cases(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
) -> list[AuditCase]:
    db = quote_ident(config.database)
    rendered = quote_ident(config.sec_rendered_table)
    document = quote_ident(config.sec_document_table)
    filing = quote_ident(config.sec_filing_table)
    cases: list[AuditCase] = []
    # The rendered authority contains hundreds of millions of compressed text rows.
    # Selecting `text` before LIMIT BY forces an unbounded decompression/sort. Choose
    # one lightweight primary-key identity from a bounded hash partition first, then
    # read only that exact document and its parent metadata.
    for lane, (kind, rationale) in zip((7, 19, 31, 43, 55), SEC_STRATA, strict=True):
        identity_sql = f"""
SELECT cik, accession_number, document_id, text_kind
FROM {db}.{rendered}
PREWHERE cityHash64(cik) % 64 = {lane}
WHERE text_kind={sql_string(kind)}
  AND source_archive_date >= toDate('2022-01-01')
  AND source_archive_date < toDate({sql_string(config.end_date_exclusive)})
  AND text_char_count BETWEEN 1200 AND 20000
ORDER BY cik, accession_number, document_id, text_kind
LIMIT 1
FORMAT JSONEachRow
"""
        identity = one_json_row(client, identity_sql, f"SEC stratum {kind} identity")
        cik = sql_string(str(identity["cik"]))
        accession = sql_string(str(identity["accession_number"]))
        document_id = sql_string(str(identity["document_id"]))
        exact_sql = f"""
SELECT
 r.document_id AS source_id,
 toString(coalesce(f.accepted_at_utc, toDateTime64(r.source_archive_date, 9, 'UTC')))
   AS source_timestamp,
 concat(ifNull(d.document_type, ''), ' ', ifNull(d.description, ''), ' ',
        ifNull(d.document_name, '')) AS title,
 r.text,
 [r.cik, ifNull(f.company_name, '')] AS entity_terms,
 r.cik,
 r.accession_number,
 r.filing_id,
 r.text_kind,
 r.text_char_count,
 r.text_sha256,
 r.normalizer_version AS source_normalizer_version,
 r.extraction_method,
 r.quality_flags,
 ifNull(d.document_type, '') AS document_type,
 ifNull(d.document_role, '') AS document_role,
 ifNull(d.description, '') AS description,
 ifNull(d.document_name, '') AS document_name,
 ifNull(f.company_name, '') AS company_name,
 ifNull(f.form_type, '') AS form_type,
 ifNull(f.items, '') AS filing_items,
 ifNull(toString(f.filing_date), '') AS filing_date,
 ifNull(toString(f.report_date), '') AS report_date
FROM
(
 SELECT *
 FROM {db}.{rendered} FINAL
 PREWHERE cik={cik}
 WHERE accession_number={accession}
   AND document_id={document_id}
   AND text_kind={sql_string(kind)}
 LIMIT 1
) AS r
LEFT JOIN
(
 SELECT *
 FROM {db}.{document} FINAL
 PREWHERE cik={cik}
 WHERE accession_number={accession}
   AND document_id={document_id}
 LIMIT 1
) AS d
 ON d.document_id=r.document_id
 AND d.cik=r.cik
 AND d.accession_number=r.accession_number
LEFT JOIN
(
 SELECT *
 FROM {db}.{filing} FINAL
 PREWHERE cik={cik}
 WHERE accession_number={accession}
 LIMIT 1
) AS f
 ON f.filing_id=r.filing_id
 AND f.cik=r.cik
 AND f.accession_number=r.accession_number
FORMAT JSONEachRow
"""
        row = one_json_row(client, exact_sql, f"SEC stratum {kind} document")
        cases.append(AuditCase("sec", kind, rationale, row))
    return cases


def render_case(case: AuditCase, document: SourceDocument) -> str:
    semantic = mining_text(document)
    normalized_title = normalize_financial_text(
        document.title,
        entity_terms=document.entity_terms,
    )
    normalized_body = normalize_financial_text(
        semantic,
        entity_terms=document.entity_terms,
    )
    combined = " ".join(value for value in (normalized_title.text, normalized_body.text) if value)
    keyword_counts = Counter(tokens(combined))
    keywords = [
        (token, count)
        for token, count in sorted(keyword_counts.items(), key=lambda item: (-item[1], item[0]))
        if token not in KEYWORD_STOP_WORDS and not token.startswith("<")
    ]
    phrase_counts = Counter(
        phrase
        for normalized in (normalized_title.text, normalized_body.text)
        for phrase, _ in candidate_ngrams(normalized, min_ngram=2, max_ngram=6)
    )
    seed_matches = [
        (phrase, concept, phrase_counts.get(phrase, 0))
        for phrase, concept in sorted(PHRASE_TO_CONCEPT.items())
        if phrase in normalized_title.text or phrase in normalized_body.text
    ]
    phrases = sorted(
        phrase_counts.items(),
        key=lambda item: (
            item[0] not in PHRASE_TO_CONCEPT,
            -item[1],
            -len(item[0].split()),
            item[0],
        ),
    )
    values = [
        ("title", value)
        for value in normalized_title.values
    ] + [
        ("body", value)
        for value in normalized_body.values
    ]
    classification = current_classification(case, semantic)
    observations = method_observations(
        case=case,
        document=document,
        values=values,
        seed_matches=seed_matches,
        classification=classification,
    )
    metadata = public_metadata(case.row)
    lines = [
        f"# {case.corpus.upper()} method audit — {case.stratum}",
        "",
        "## Audit verdict",
        "",
        f"- Selection rationale: {case.rationale}.",
        f"- Source identity: `{document.source_id}`.",
        f"- Source timestamp: `{document.timestamp}`.",
        f"- Original rendered characters: {len(document.text):,}.",
        f"- Semantic characters after provenance removal: {len(semantic):,}.",
        f"- Original SHA-256: `{sha256_text(document.text)}`.",
        f"- Semantic SHA-256: `{sha256_text(semantic)}`.",
        f"- Candidate inventory: `{INVENTORY_VERSION}`.",
        f"- Financial normalizer: `{NORMALIZER_VERSION}`.",
        "",
        "This file audits deterministic evidence extraction. Candidate concepts are "
        "**proposed evidence**, not an approved semantic taxonomy. The current News "
        "classifier is shown separately. SEC currently has no semantic classifier, so "
        "this audit does not invent one.",
        "",
        "## Method audit observations",
        "",
        *[f"- {value}" for value in observations],
        "",
        "## Current classification boundary",
        "",
        fence_block(json.dumps(classification, indent=2, ensure_ascii=False)),
        "",
        "## Source metadata supplied to the method",
        "",
        markdown_key_value_table(metadata),
        "",
        "## Stage 1 — provenance cleaning",
        "",
        "News renderer title/source/image provenance is excluded from semantic mining. "
        "SEC renderer structure is retained because headings and table labels carry filing meaning.",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Removed characters | {len(document.text) - len(semantic):,} |",
        f"| Entity terms normalized | {len(document.entity_terms):,} |",
        f"| Typed values extracted | {len(values):,} |",
        f"| Unique keyword candidates | {len(keywords):,} |",
        f"| Unique phrase candidates | {len(phrases):,} |",
        f"| Curated seed phrases matched | {len(seed_matches):,} |",
        "",
        "## Stage 2 — typed values",
        "",
        "Specialized patterns run before the generic number fallback, preventing one "
        "number from receiving conflicting types.",
        "",
        markdown_value_table(values),
        "",
        "## Stage 3 — keyword candidates",
        "",
        "Counts below are occurrences inside this document. Corpus importance is later "
        "based on one presence per document, not repetition.",
        "",
        markdown_count_table("Keyword", keywords[:100]),
        "",
        "## Stage 4 — phrase and n-gram candidates",
        "",
        "The audit lists up to 150 highest-priority 2–6 token phrases. Seed matches are "
        "listed first; remaining phrases are ordered by document occurrence and specificity.",
        "",
        markdown_phrase_table(phrases[:150]),
        "",
        "## Stage 5 — curated seed evidence",
        "",
        markdown_seed_table(seed_matches),
        "",
        "## What the corpus pass does next",
        "",
        "1. Counts each keyword or phrase at most once per source document.",
        "2. Combines independent work units with bounded Space-Saving estimates.",
        "3. Stores the estimate, replacement error, and conservative frequency lower bound.",
        "4. Uses the lower bound—not raw repetition—for minimum-support filtering.",
        "5. Leaves every retained row in `proposed` review status.",
        "",
        "## Auditor checklist",
        "",
        "- [ ] Renderer provenance was removed without deleting semantic content.",
        "- [ ] Entity names/tickers/CIKs were normalized where supplied.",
        "- [ ] Numeric values received the correct specialized type.",
        "- [ ] Generic `<number>` did not replace a specialized value.",
        "- [ ] Candidate phrases preserve negation and event meaning.",
        "- [ ] Seed matches are supported verbatim by the normalized text.",
        "- [ ] The current classification is distinguishable from candidate evidence.",
        "- [ ] No reaction, sentiment, or SEC semantic label was inferred without an authority.",
        "",
        "## Original rendered input",
        "",
        fence_block(document.text),
        "",
        "## Semantic text used for mining",
        "",
        fence_block(semantic),
        "",
        "## Fully normalized text used for candidate generation",
        "",
        fence_block(combined),
        "",
    ]
    return "\n".join(lines)


def method_observations(
    *,
    case: AuditCase,
    document: SourceDocument,
    values: list[tuple[str, Any]],
    seed_matches: list[tuple[str, str, int]],
    classification: dict[str, Any],
) -> list[str]:
    observations = [
        (
            f"`{case.stratum}` is a deterministic sampling stratum, not a semantic "
            "label emitted by the method."
        )
    ]
    if case.corpus == "news":
        observations.append(
            "News Synthesis V1 emits document structure "
            f"`{classification.get('document_structure')}`, purpose "
            f"`{classification.get('communication_purpose')}`, and origin "
            f"`{classification.get('information_origin')}`."
        )
    else:
        observations.append(
            "No SEC semantic class is emitted; only rendered structure, typed values, "
            "keywords, and phrase candidates are auditable here."
        )
    if not seed_matches:
        observations.append(
            "No curated seed phrase matched; all phrase rows in this case remain "
            "corpus-discovered candidates."
        )
    title_fallbacks = sum(
        1
        for location, value in values
        if location == "title" and value.value_type == "number"
    )
    if title_fallbacks:
        observations.append(
            f"The title produced {title_fallbacks} generic numeric fallback"
            f"{'s' if title_fallbacks != 1 else ''}; inspect dates, form numbers, and "
            "filenames for lexical noise before taxonomy approval."
        )
    if case.corpus == "sec":
        text_kind = str(case.row.get("text_kind") or "")
        document_type = str(case.row.get("document_type") or "")
        if text_kind == "prospectus" and document_type.upper().startswith("EX-25"):
            observations.append(
                "The renderer identifies this row as `prospectus`, while the source "
                f"document type is `{document_type}`. Treat this as a renderer "
                "classification discrepancy requiring source-level review."
            )
    if document.text == mining_text(document):
        observations.append(
            "No renderer provenance was removed for this case; the rendered text was "
            "passed through intact."
        )
    return observations


def current_classification(case: AuditCase, semantic_text: str) -> dict[str, Any]:
    if case.corpus == "sec":
        return {
            "authority": None,
            "status": "not_implemented",
            "semantic_label_emitted": False,
            "reason": (
                "SEC has rendered-text and lexical-evidence authorities but no reviewed "
                "deterministic semantic labeler yet."
            ),
        }
    del semantic_text
    try:
        synthesis = json.loads(str(case.row.get("synthesis_json") or "{}"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("News Synthesis V1 output is required for a News audit") from exc
    if not isinstance(synthesis, dict) or not synthesis.get("envelope"):
        raise RuntimeError("News Synthesis V1 output is required for a News audit")
    envelope = synthesis["envelope"]
    return {
        "authority": ENGINE_VERSION,
        "contract_version": synthesis.get("contract_version"),
        "status": "news_synthesis_v1_output",
        "semantic_label_emitted": True,
        "document_structure": envelope["document_structure"]["value"],
        "communication_purpose": envelope["communication_purpose"]["value"],
        "information_origin": envelope["information_origin"]["value"],
        "production_method": envelope["production_method"]["value"],
        "quality_flags": synthesis.get("quality_flags", []),
    }


def document_from_case(case: AuditCase) -> SourceDocument:
    row = case.row
    excluded = {"source_id", "source_timestamp", "title", "text", "entity_terms"}
    return SourceDocument(
        corpus=case.corpus,
        source_id=str(row.get("source_id") or ""),
        timestamp=str(row.get("source_timestamp") or ""),
        title=str(row.get("title") or ""),
        text=str(row.get("text") or ""),
        entity_terms=tuple(str(value) for value in row.get("entity_terms") or [] if str(value)),
        metadata={key: value for key, value in row.items() if key not in excluded},
    )


def public_metadata(row: dict[str, Any]) -> dict[str, Any]:
    excluded = {"text", "entity_terms"}
    return {key: value for key, value in row.items() if key not in excluded}


def markdown_key_value_table(values: dict[str, Any]) -> str:
    lines = ["| Field | Value |", "|---|---|"]
    for key in sorted(values):
        value = values[key]
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
        lines.append(f"| `{escape_cell(key)}` | {escape_cell(rendered)} |")
    return "\n".join(lines)


def markdown_value_table(values: list[tuple[str, Any]]) -> str:
    if not values:
        return "_No typed values extracted._"
    lines = [
        "| Location | Type | Raw | Normalized | Placeholder | Context |",
        "|---|---|---|---:|---|---|",
    ]
    for location, value in values:
        lines.append(
            f"| {location} | `{escape_cell(value.value_type)}`"
            f" | {escape_cell(value.raw)} | {escape_cell(value.normalized_number)}"
            f" | `{escape_cell(value.placeholder)}` | {escape_cell(value.context)} |"
        )
    return "\n".join(lines)


def markdown_count_table(label: str, values: list[tuple[str, int]]) -> str:
    if not values:
        return "_No candidates._"
    lines = [f"| {label} | In-document occurrences |", "|---|---:|"]
    lines.extend(f"| `{escape_cell(value)}` | {count:,} |" for value, count in values)
    return "\n".join(lines)


def markdown_phrase_table(values: list[tuple[str, int]]) -> str:
    if not values:
        return "_No phrase candidates._"
    lines = [
        "| Phrase | Tokens | Occurrences | Seed concept |",
        "|---|---:|---:|---|",
    ]
    for phrase, count in values:
        lines.append(
            f"| `{escape_cell(phrase)}` | {len(phrase.split())}"
            f" | {count:,} | {escape_cell(PHRASE_TO_CONCEPT.get(phrase, '')) or '—'} |"
        )
    return "\n".join(lines)


def markdown_seed_table(values: list[tuple[str, str, int]]) -> str:
    if not values:
        return "_No curated seed phrase matched this document._"
    lines = ["| Phrase | Proposed concept | Occurrences |", "|---|---|---:|"]
    for phrase, concept, count in values:
        lines.append(
            f"| `{escape_cell(phrase)}` | `{escape_cell(concept)}` | {count:,} |"
        )
    return "\n".join(lines)


def fence_block(value: str) -> str:
    text = str(value or "")
    fence = "````" if "```" in text else "```"
    return f"{fence}text\n{text}\n{fence}"


def escape_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def safe_slug(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value).strip("_")


def one_json_row(client: ClickHouseHttpClient, sql: str, label: str) -> dict[str, Any]:
    rows = json_rows(client.execute(sql))
    if len(rows) != 1:
        raise RuntimeError(f"{label} expected one row, received {len(rows)}")
    return rows[0]


def json_rows(value: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in value.splitlines() if line.strip()]


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def write_text_atomic(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
