from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_pipeline.config import sec_user_agent
from pipelines.sec.edgar.sec_pipeline.rate_limit import SecRateLimiter
from pipelines.sec.edgar.sec_taxonomy import (
    DOCUMENT_RULES,
    EMBEDDING_MODEL,
    MANUAL_FORM_DEFINITIONS,
    POLICY_VERSION,
    TAXONOMY_VERSION,
    normalize_title,
    normalize_type,
    semantic_label,
    taxonomy_key,
    title_match_metrics,
)
from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, quote_ident, sql_string
from research.mlops.env import discover_env_files, load_env_files


FORMS_URL = "https://www.sec.gov/submit-filings/forms-index"
CONFORMANCE_URL = "https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/understand-automated-conformance-rules-edgar-data-fields"
DEFAULT_OUTPUT_ROOT = Path("D:/market-data/prepared/sec_taxonomy")
REPORT_PATH = Path(__file__).with_name("SEC_DISCLOSURE_TAXONOMY_V3_ANALYSIS.md")


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[dict[str, Any]]] = []
        self._row: list[dict[str, Any]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = {"text": [], "links": []}
        elif tag == "a" and self._cell is not None:
            href = dict(attrs).get("href")
            if href:
                self._cell["links"].append(href)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._cell["text"] = " ".join("".join(self._cell["text"]).split())
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build, manually approved SEC disclosure taxonomy and Qwen embedding policy from official SEC definitions and observed v3 data.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--model-database", default="market_sip_compact")
    parser.add_argument("--publish", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--refresh-web", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-form-pages", type=int, default=20)
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    return parser.parse_args()


def fetch(url: str, user_agent: str, limiter: SecRateLimiter) -> bytes:
    limiter.wait()
    request = Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity", "Accept": "text/html,application/xhtml+xml"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def scrape_forms(output: Path, args: argparse.Namespace, user_agent: str, limiter: SecRateLimiter) -> list[dict[str, Any]]:
    cached = output / "official_forms.json"
    if cached.exists() and not args.refresh_web:
        return json.loads(cached.read_text(encoding="utf-8"))
    forms: dict[str, dict[str, Any]] = {}
    for page in range(args.max_form_pages):
        url = f"{FORMS_URL}?page={page}"
        body = fetch(url, user_agent, limiter)
        (output / f"forms_index_page_{page}.html").write_bytes(body)
        parser = TableParser(); parser.feed(body.decode("utf-8", errors="replace"))
        page_forms = 0
        for row in parser.rows:
            if len(row) < 5 or row[0]["text"] == "Number":
                continue
            number = normalize_type(row[0]["text"])
            description = re.sub(r"\s*\(PDF\)\s*$", "", row[1]["text"], flags=re.I).strip()
            if not number:
                continue
            links = [urljoin(url, link) for cell in row for link in cell["links"]]
            forms[number] = {
                "form_type": number,
                "canonical_title": description,
                "last_updated": row[2]["text"],
                "sec_number": row[3]["text"],
                "topics": row[4]["text"],
                "form_pdf_url": next((link for link in links if link.lower().endswith(".pdf")), ""),
                "source_url": url,
            }
            page_forms += 1
        if page_forms == 0:
            break
    rows = sorted(forms.values(), key=lambda row: row["form_type"])
    required = {"1-A", "8-K", "10-K", "10-Q", "20-F", "N-PX", "S-1"}
    missing = sorted(required - set(forms))
    if missing:
        raise RuntimeError(f"SEC forms scrape failed structural validation; missing anchor forms: {missing}")
    cached.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return rows


def scrape_conformance(output: Path, args: argparse.Namespace, user_agent: str, limiter: SecRateLimiter) -> list[dict[str, Any]]:
    cached = output / "official_conformance_rows.json"
    if cached.exists() and not args.refresh_web:
        return json.loads(cached.read_text(encoding="utf-8"))
    body = fetch(CONFORMANCE_URL, user_agent, limiter)
    (output / "edgar_conformance_rules.html").write_bytes(body)
    parser = TableParser(); parser.feed(body.decode("utf-8", errors="replace"))
    rows = []
    for row in parser.rows:
        values = [cell["text"] for cell in row]
        joined = " | ".join(values)
        if re.search(r"\b(?:EX-\d|DOCUMENT|SUBMISSION|FORM TYPE|TYPE)\b", joined, flags=re.I):
            rows.append({"cells": values, "source_url": CONFORMANCE_URL})
    cached.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return rows


def query_json(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    payload = client.execute(sql.strip().rstrip(";") + " FORMAT JSONEachRow")
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def observed_groups(client: ClickHouseHttpClient, database: str) -> list[dict[str, Any]]:
    db = quote_ident(database)
    return query_json(client, f"""
SELECT upperUTF8(trimBoth(document_type)) AS document_type, document_role,
       count() AS source_rows, uniqExact(accession_number) AS filings,
       sum(source_text_char_count) AS source_characters,
       quantilesExact(0.5, 0.9, 0.99)(source_text_char_count) AS source_char_quantiles,
       max(source_text_char_count) AS source_max_characters,
       topKWeighted(5)(ifNull(description, ''), source_text_char_count) AS dominant_descriptions
FROM {db}.sec_filing_text_v3 FINAL
GROUP BY document_type, document_role
ORDER BY source_characters DESC
SETTINGS max_threads=32, max_memory_usage='64G'
""")


def approved_taxonomy(forms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    rows: list[dict[str, Any]] = []
    for form in forms:
        label = semantic_label(form["form_type"], form["canonical_title"], scope="form")
        rows.append(taxonomy_row("form", "exact", form["form_type"], form["canonical_title"], label, form["source_url"], now, form))
    official_types = {row["submitted_type"] for row in rows}
    for form_type, title in MANUAL_FORM_DEFINITIONS.items():
        if form_type in official_types:
            continue
        label = semantic_label(form_type, title, scope="form")
        evidence = {"manual_definition": True, "form_type": form_type, "title": title}
        rows.append(taxonomy_row("form", "exact", form_type, title, label, CONFORMANCE_URL, now, evidence))
    for match_kind, document_type, title in DOCUMENT_RULES:
        label = semantic_label(document_type, title, scope="document")
        rows.append(taxonomy_row("document", match_kind, document_type, title, label, CONFORMANCE_URL, now, {}))
    return rows


def taxonomy_row(scope: str, match_kind: str, submitted_type: str, title: str, label: Any, source_url: str, now: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "taxonomy_key": taxonomy_key(scope, submitted_type), "taxonomy_version": TAXONOMY_VERSION,
        "taxonomy_scope": scope, "match_kind": match_kind, "submitted_type": normalize_type(submitted_type),
        "canonical_title": title, "normalized_title": normalize_title(title), "category": label.category,
        "impact_label": label.impact_label, "impact_score": label.impact_score,
        "affected_security_scope": label.affected_security_scope, "impact_rationale": label.rationale,
        "classification_status": "approved", "approval_method": "manual_review_2026_07_16",
        "approved_by": "taxonomy_admin", "approved_at_utc": now, "source_authority": "SEC",
        "source_url": source_url, "source_evidence_json": json.dumps(evidence, separators=(",", ":"), ensure_ascii=False),
        "source_hash": hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest(), "updated_at_utc": now,
    }


def build_candidates(groups: list[dict[str, Any]], approved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    exact = {(row["taxonomy_scope"], row["submitted_type"]): row for row in approved if row["match_kind"] == "exact"}
    prefixes = [row for row in approved if row["match_kind"] == "prefix"]
    candidates = []
    for group in groups:
        dtype = normalize_type(group["document_type"])
        resolved = exact.get(("document", dtype))
        resolution = "exact_document_type" if resolved else ""
        if not resolved:
            matches = [row for row in prefixes if dtype.startswith(row["submitted_type"])]
            if matches:
                resolved = max(matches, key=lambda row: len(row["submitted_type"]))
                resolution = "approved_document_prefix"
        if not resolved:
            resolved = exact.get(("form", dtype))
            if resolved:
                resolution = "exact_form_type_as_document_type"
        if not resolved and dtype.endswith("/A"):
            resolved = exact.get(("form", dtype[:-2]))
            if resolved:
                resolution = "approved_form_amendment"
        descriptions = [value for value in group.get("dominant_descriptions", []) if str(value).strip()]
        metrics = {"score": 0.0, "token_coverage": 0.0, "ordered_coverage": 0.0, "span_density": 0.0, "char_similarity": 0.0}
        suggested = None
        if not resolved and descriptions:
            ranked = []
            for row in approved:
                for description in descriptions:
                    ranked.append((title_match_metrics(str(description), row["canonical_title"]), row))
            metrics, suggested = max(ranked, key=lambda item: item[0]["score"])
        candidates.append({
            "candidate_key": hashlib.sha256(f"{dtype}|{group['document_role']}".encode()).hexdigest(),
            "document_type": dtype, "document_role": group["document_role"],
            "dominant_descriptions": descriptions, "source_rows": group["source_rows"], "filings": group["filings"],
            "source_characters": group["source_characters"], "source_char_quantiles": group["source_char_quantiles"],
            "source_max_characters": group["source_max_characters"],
            "resolution_status": "approved" if resolved else "manual_review_required",
            "resolution_method": resolution or ("fuzzy_title_candidate" if suggested else "unmatched"),
            "resolved_taxonomy_key": resolved["taxonomy_key"] if resolved else "",
            "suggested_taxonomy_key": suggested["taxonomy_key"] if suggested else "",
            **metrics, "taxonomy_version": TAXONOMY_VERSION,
            "observed_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        })
    return candidates


def policy_rows(approved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    rows = []
    for row in approved:
        label = semantic_label(row["submitted_type"], row["canonical_title"], scope=row["taxonomy_scope"])
        rows.append({"taxonomy_key": row["taxonomy_key"], "policy_version": POLICY_VERSION, "embedding_model": EMBEDDING_MODEL,
                     "embedding_enabled": int(label.embedding_enabled), "input_strategy": label.input_strategy,
                     "chunk_tokens": 1024, "max_chunks": None, "max_total_tokens": None,
                     "renderer_text_limit_chars": None,
                     "policy_rationale": "Embed complete rendered text in uncapped 1024-token chunks." if label.embedding_enabled else label.rationale,
                     "approved_by": "taxonomy_admin", "approved_at_utc": now, "updated_at_utc": now})
    return rows


def ensure_and_publish(client: ClickHouseHttpClient, database: str, model_database: str, approved: list[dict[str, Any]], candidates: list[dict[str, Any]], policies: list[dict[str, Any]]) -> None:
    db, mdb = quote_ident(database), quote_ident(model_database)
    client.execute(f"CREATE DATABASE IF NOT EXISTS {db}"); client.execute(f"CREATE DATABASE IF NOT EXISTS {mdb}")
    ddls = {
        f"{db}.sec_disclosure_taxonomy_v3": "taxonomy_key String, taxonomy_version LowCardinality(String), taxonomy_scope LowCardinality(String), match_kind LowCardinality(String), submitted_type String, canonical_title String, normalized_title String, category LowCardinality(String), impact_label String, impact_score UInt8, affected_security_scope String, impact_rationale String, classification_status LowCardinality(String), approval_method LowCardinality(String), approved_by String, approved_at_utc DateTime64(3, 'UTC'), source_authority LowCardinality(String), source_url String, source_evidence_json String, source_hash String, updated_at_utc DateTime64(3, 'UTC')",
        f"{db}.sec_disclosure_taxonomy_candidate_v3": "candidate_key String, document_type String, document_role LowCardinality(String), dominant_descriptions Array(String), source_rows UInt64, filings UInt64, source_characters UInt64, source_char_quantiles Array(UInt64), source_max_characters UInt64, resolution_status LowCardinality(String), resolution_method LowCardinality(String), resolved_taxonomy_key String, suggested_taxonomy_key String, token_coverage Float64, ordered_coverage Float64, span_density Float64, char_similarity Float64, score Float64, taxonomy_version LowCardinality(String), observed_at_utc DateTime64(3, 'UTC')",
        f"{mdb}.sec_embedding_policy_v3": "taxonomy_key String, policy_version LowCardinality(String), embedding_model LowCardinality(String), embedding_enabled UInt8, input_strategy LowCardinality(String), chunk_tokens UInt16, max_chunks Nullable(UInt32), max_total_tokens Nullable(UInt64), renderer_text_limit_chars Nullable(UInt64), policy_rationale String, approved_by String, approved_at_utc DateTime64(3, 'UTC'), updated_at_utc DateTime64(3, 'UTC')",
    }
    payloads = {f"{db}.sec_disclosure_taxonomy_v3": approved, f"{db}.sec_disclosure_taxonomy_candidate_v3": candidates, f"{mdb}.sec_embedding_policy_v3": policies}
    for target, columns in ddls.items():
        staging = target + "__staging"
        client.execute(f"DROP TABLE IF EXISTS {staging}")
        client.execute(f"CREATE TABLE {staging} ({columns}) ENGINE=MergeTree ORDER BY tuple()")
        body = "\n".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) for row in payloads[target])
        client.execute(f"INSERT INTO {staging} SETTINGS date_time_input_format='best_effort' FORMAT JSONEachRow\n{body}")
        expected = len(payloads[target]); actual = int(client.execute(f"SELECT count() FROM {staging}").strip())
        if actual != expected:
            raise RuntimeError(f"staging validation failed for {target}: expected={expected} actual={actual}")
        client.execute(f"CREATE TABLE IF NOT EXISTS {target} AS {staging}")
        client.execute(f"EXCHANGE TABLES {target} AND {staging}")
        client.execute(f"DROP TABLE {staging}")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows: return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})


def fmt_chars(value: int) -> str:
    for divisor, suffix in ((10**12, "T"), (10**9, "B"), (10**6, "M"), (10**3, "K")):
        if value >= divisor: return f"{value/divisor:.3f}{suffix}"
    return str(value)


def corpus_statistics(client: ClickHouseHttpClient, database: str, model_database: str) -> dict[str, Any]:
    db, mdb = quote_ident(database), quote_ident(model_database)
    source = query_json(client, f"""
SELECT count() AS rows, uniqExact(accession_number) AS filings, sum(source_text_char_count) AS characters,
       quantilesExact(0.5,0.9,0.99,0.999)(source_text_char_count) AS quantiles,
       max(source_text_char_count) AS maximum
FROM {db}.sec_filing_text_v3 FINAL
SETTINGS max_threads=32,max_memory_usage='64G'
""")[0]
    rendered = query_json(client, f"""
SELECT count() AS rendered_rows, sum(r.text_char_count) AS rendered_characters,
       quantilesExact(0.5,0.9,0.99,0.999)(r.text_char_count) AS rendered_quantiles,
       max(r.text_char_count) AS rendered_max_characters,
       countIf(p.embedding_enabled=1) AS eligible_rendered_rows,
       sumIf(r.text_char_count,p.embedding_enabled=1) AS eligible_rendered_characters,
       quantilesExactIf(0.5,0.9,0.99)(r.text_char_count,p.embedding_enabled=1) AS eligible_rendered_quantiles
FROM (SELECT document_id,text_char_count FROM {db}.sec_filing_text_rendered_v3 FINAL) AS r
INNER JOIN (SELECT document_id,upperUTF8(trimBoth(document_type)) AS document_type,document_role FROM {db}.sec_filing_text_v3 FINAL) AS s
    ON r.document_id=s.document_id
INNER JOIN {db}.sec_disclosure_taxonomy_candidate_v3 AS c
    ON s.document_type=c.document_type AND s.document_role=c.document_role
INNER JOIN {mdb}.sec_embedding_policy_v3 AS p ON c.resolved_taxonomy_key=p.taxonomy_key
WHERE c.resolution_status='approved'
SETTINGS max_threads=32,max_memory_usage='96G',join_algorithm='grace_hash'
""")[0]
    source_by_taxonomy = query_json(client, f"""
SELECT p.taxonomy_key, count() AS source_rows, uniqExact(s.accession_number) AS source_filings,
       sum(s.source_text_char_count) AS source_characters,
       quantilesExact(0.5,0.9,0.99,0.999)(s.source_text_char_count) AS source_quantiles,
       max(s.source_text_char_count) AS source_max_characters
FROM (
    SELECT accession_number,upperUTF8(trimBoth(document_type)) AS document_type,document_role,source_text_char_count
    FROM {db}.sec_filing_text_v3 FINAL
) AS s
INNER JOIN {db}.sec_disclosure_taxonomy_candidate_v3 AS c
    ON s.document_type=c.document_type AND s.document_role=c.document_role
INNER JOIN {mdb}.sec_embedding_policy_v3 AS p ON c.resolved_taxonomy_key=p.taxonomy_key
WHERE c.resolution_status='approved'
GROUP BY p.taxonomy_key
SETTINGS max_threads=32,max_memory_usage='96G',join_algorithm='grace_hash'
""")
    rendered_by_taxonomy = query_json(client, f"""
SELECT p.taxonomy_key, count() AS rendered_rows, uniqExact(s.accession_number) AS rendered_filings,
       sum(r.text_char_count) AS rendered_characters,
       quantilesExact(0.5,0.9,0.99,0.999)(r.text_char_count) AS rendered_quantiles,
       max(r.text_char_count) AS rendered_max_characters
FROM (SELECT document_id,text_char_count FROM {db}.sec_filing_text_rendered_v3 FINAL) AS r
INNER JOIN (SELECT document_id,accession_number,upperUTF8(trimBoth(document_type)) AS document_type,document_role FROM {db}.sec_filing_text_v3 FINAL) AS s
    ON r.document_id=s.document_id
INNER JOIN {db}.sec_disclosure_taxonomy_candidate_v3 AS c
    ON s.document_type=c.document_type AND s.document_role=c.document_role
INNER JOIN {mdb}.sec_embedding_policy_v3 AS p ON c.resolved_taxonomy_key=p.taxonomy_key
WHERE c.resolution_status='approved'
GROUP BY p.taxonomy_key
SETTINGS max_threads=32,max_memory_usage='96G',join_algorithm='grace_hash'
""")
    table_rows = query_json(client, f"""
SELECT name, total_rows
FROM system.tables
WHERE database={sql_string(model_database)}
  AND name IN ('sec_filing_text_tokens_v3','sec_filing_text_embeddings_v3')
""")
    products = {row["name"]: int(row["total_rows"] or 0) for row in table_rows}
    return {
        "source": source,
        "rendered": rendered,
        "source_by_taxonomy": source_by_taxonomy,
        "rendered_by_taxonomy": rendered_by_taxonomy,
        "model_products": products,
    }


def write_report(approved: list[dict[str, Any]], candidates: list[dict[str, Any]], policies: list[dict[str, Any]], statistics: dict[str, Any]) -> None:
    policy = {row["taxonomy_key"]: row for row in policies}
    approved_candidates = [row for row in candidates if row["resolution_status"] == "approved"]
    unresolved = [row for row in candidates if row["resolution_status"] != "approved"]
    eligible_rows = sum(int(row["source_rows"]) for row in approved_candidates if policy[row["resolved_taxonomy_key"]]["embedding_enabled"])
    eligible_chars = sum(int(row["source_characters"]) for row in approved_candidates if policy[row["resolved_taxonomy_key"]]["embedding_enabled"])
    unresolved_chars = sum(int(row["source_characters"]) for row in unresolved)
    official_forms = sum(row["taxonomy_scope"] == "form" and FORMS_URL in row["source_url"] for row in approved)
    supplemental_forms = sum(row["taxonomy_scope"] == "form" and FORMS_URL not in row["source_url"] for row in approved)
    document_rules = sum(row["taxonomy_scope"] == "document" for row in approved)
    source = statistics["source"]
    rendered = statistics["rendered"]
    product_rows = statistics["model_products"]
    source_by_taxonomy = {row["taxonomy_key"]: row for row in statistics["source_by_taxonomy"]}
    rendered_by_taxonomy = {row["taxonomy_key"]: row for row in statistics["rendered_by_taxonomy"]}
    token_rows = product_rows.get("sec_filing_text_tokens_v3")
    embedding_rows = product_rows.get("sec_filing_text_embeddings_v3")
    product_status = (
        f"token rows `{token_rows:,}` and embedding rows `{embedding_rows:,}`"
        if token_rows is not None and embedding_rows is not None
        else "the v3 token and embedding tables do not yet exist"
    )
    lines = [
        "# SEC Disclosure Taxonomy and Embedding Workload", "",
        f"Generated UTC: `{datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00','Z')}`", "",
        "## Verdict", "",
        f"The approved taxonomy contains `{len(approved):,}` manually reviewed rules: `{official_forms:,}` numbered definitions scraped from the SEC Forms Index, `{supplemental_forms:,}` manually curated EDGAR submission types absent from that index, and `{document_rules:,}` document rules. Fuzzy title distance is candidate evidence only and never changes the authoritative label.", "",
        f"Observed source workload resolved by approved taxonomy rules: `{sum(int(r['source_rows']) for r in approved_candidates):,}` rows / `{fmt_chars(sum(int(r['source_characters']) for r in approved_candidates))}` characters. Source-policy accounting marks `{eligible_rows:,}` rows / `{fmt_chars(eligible_chars)}` source characters for embedding.", "",
        f"Actual rendered input is smaller: `{int(rendered['rendered_rows']):,}` resolved rendered rows / `{fmt_chars(int(rendered['rendered_characters']))}` characters. Of these, `{int(rendered['eligible_rendered_rows']):,}` rows / `{fmt_chars(int(rendered['eligible_rendered_characters']))}` characters are currently eligible for Qwen embedding. At report time, {product_status}.", "",
        f"Unresolved observed types remain blocked for manual review: `{len(unresolved):,}` groups / `{fmt_chars(unresolved_chars)}` characters. They are preserved in source and rendered storage; they are not silently embedded.", "",
        "The renderer remains uncapped for every class. `renderer_text_limit_chars` is NULL. Eligible documents use complete 1,024-token chunks with NULL `max_chunks` and NULL `max_total_tokens`; structured datasets and technical duplicates are routed to structured extraction or preservation rather than lossy text clipping.", "",
        "## Matching Method", "",
        "1. Exact submitted SEC form number is authoritative.",
        "2. Explicitly approved document prefix or exact rules are authoritative.",
        "3. Title candidates use normalized token coverage, ordered coverage, minimum ordered-span density, and character similarity.",
        "4. Fuzzy matches remain `manual_review_required`; approval requires an edited taxonomy publication.", "",
        "## Actual Database Size Distribution", "",
        "| Layer | Rows | Filings | Characters | P50 | P90 | P99 | P99.9 | Maximum |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| Full source text | {int(source['rows']):,} | {int(source['filings']):,} | {fmt_chars(int(source['characters']))} | {' | '.join(fmt_chars(int(v)) for v in source['quantiles'])} | {fmt_chars(int(source['maximum']))} |",
        f"| Resolved rendered text | {int(rendered['rendered_rows']):,} | n/a | {fmt_chars(int(rendered['rendered_characters']))} | {' | '.join(fmt_chars(int(v)) for v in rendered['rendered_quantiles'])} | {fmt_chars(int(rendered['rendered_max_characters']))} |",
        f"| Eligible rendered text | {int(rendered['eligible_rendered_rows']):,} | n/a | {fmt_chars(int(rendered['eligible_rendered_characters']))} | {' | '.join(fmt_chars(int(v)) for v in rendered['eligible_rendered_quantiles'])} | n/a | n/a |",
        "",
        "Counts use logical current rows from `FINAL`. Eligible rendered rows are joined by `document_id` to full source metadata, resolved through the approved taxonomy, and filtered by the model policy. They are the actual documents the v3 embedding extractor should process.", "",
        "## Approved Taxonomy", "",
        "| Scope | Match | Type | Official or canonical title | Category | Impact | Score | Source rows | Source filings | Source chars | Source P50/P90/P99/P99.9 | Source max | Rendered rows | Rendered filings | Rendered chars | Rendered P50/P90/P99/P99.9 | Rendered max | Embed | Strategy |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | --- | --- |",
    ]
    for row in sorted(approved, key=lambda r: (r["taxonomy_scope"], r["submitted_type"])):
        p = policy[row["taxonomy_key"]]
        source_values = source_by_taxonomy.get(
            row["taxonomy_key"],
            {"source_rows": 0, "source_filings": 0, "source_characters": 0, "source_quantiles": [], "source_max_characters": 0},
        )
        rendered_values = rendered_by_taxonomy.get(
            row["taxonomy_key"],
            {"rendered_rows": 0, "rendered_filings": 0, "rendered_characters": 0, "rendered_quantiles": [], "rendered_max_characters": 0},
        )
        source_quantiles = " / ".join(fmt_chars(int(value)) for value in source_values["source_quantiles"]) or "n/a"
        rendered_quantiles = " / ".join(fmt_chars(int(value)) for value in rendered_values["rendered_quantiles"]) or "n/a"
        cells = [
            row["taxonomy_scope"], row["match_kind"], row["submitted_type"], row["canonical_title"],
            row["category"], row["impact_label"], row["impact_score"], f"{int(source_values['source_rows']):,}",
            f"{int(source_values['source_filings']):,}", fmt_chars(int(source_values["source_characters"])),
            source_quantiles, fmt_chars(int(source_values["source_max_characters"])),
            f"{int(rendered_values['rendered_rows']):,}", f"{int(rendered_values['rendered_filings']):,}",
            fmt_chars(int(rendered_values["rendered_characters"])), rendered_quantiles,
            fmt_chars(int(rendered_values["rendered_max_characters"])),
            "yes" if p["embedding_enabled"] else "no", p["input_strategy"],
        ]
        lines.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in cells) + " |")
    lines += ["", "## Largest Observed Groups", "", "| Type | Role | Rows | Filings | Characters | P50 / P90 / P99 | Maximum | Resolution | Embed |", "| --- | --- | ---: | ---: | ---: | --- | ---: | --- | --- |"]
    for row in sorted(candidates, key=lambda r: int(r["source_characters"]), reverse=True)[:100]:
        embed = bool(row["resolved_taxonomy_key"] and policy[row["resolved_taxonomy_key"]]["embedding_enabled"])
        q = " / ".join(fmt_chars(int(v)) for v in row["source_char_quantiles"])
        lines.append(f"| {row['document_type'].replace('|','\\|')} | {row['document_role']} | {int(row['source_rows']):,} | {int(row['filings']):,} | {fmt_chars(int(row['source_characters']))} | {q} | {fmt_chars(int(row['source_max_characters']))} | {row['resolution_method']} | {'yes' if embed else 'no'} |")
    lines += ["", "## Manual Review Queue", "", "The complete queue is published in `q_live.sec_disclosure_taxonomy_candidate_v3`. The largest unresolved candidates are:", "", "| Type | Role | Rows | Characters | Suggested score | Method |", "| --- | --- | ---: | ---: | ---: | --- |"]
    for row in sorted(unresolved, key=lambda r: int(r["source_characters"]), reverse=True)[:100]:
        lines.append(f"| {row['document_type'].replace('|','\\|')} | {row['document_role']} | {int(row['source_rows']):,} | {fmt_chars(int(row['source_characters']))} | {row['score']:.3f} | {row['resolution_method']} |")
    lines += ["", "## Database Products", "", "- `q_live.sec_disclosure_taxonomy_v3`: manually approved semantic authority.", "- `q_live.sec_disclosure_taxonomy_candidate_v3`: observed types, actual source statistics, fuzzy evidence, and unresolved review queue.", "- `market_sip_compact.sec_embedding_policy_v3`: model-specific complete-text chunking policy.", "", "## Sources", "", f"- SEC Forms Index: {FORMS_URL}", f"- EDGAR Filer Manual conformance guidance: {CONFORMANCE_URL}", ""]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    artifact_root = Path(args.output_root)
    output = artifact_root / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    official_cache = artifact_root / "official_current"
    output.mkdir(parents=True, exist_ok=True)
    official_cache.mkdir(parents=True, exist_ok=True)
    user_agent = sec_user_agent()
    if not user_agent: raise SystemExit("SEC_USER_AGENT is required")
    limiter = SecRateLimiter(0.12)
    forms = scrape_forms(official_cache, args, user_agent, limiter)
    conformance = scrape_conformance(official_cache, args, user_agent, limiter)
    if len(forms) < 140:
        raise RuntimeError(f"SEC numbered-form scrape unexpectedly small: {len(forms)} rows")
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    groups = observed_groups(client, args.database)
    approved = approved_taxonomy(forms); candidates = build_candidates(groups, approved); policies = policy_rows(approved)
    write_csv(output / "approved_taxonomy.csv", approved); write_csv(output / "observed_candidates.csv", candidates); write_csv(output / "embedding_policy.csv", policies)
    (output / "manifest.json").write_text(json.dumps({"forms": len(forms), "conformance_rows": len(conformance), "approved": len(approved), "candidates": len(candidates), "taxonomy_version": TAXONOMY_VERSION, "policy_version": POLICY_VERSION, "official_cache": str(official_cache)}, indent=2), encoding="utf-8")
    if args.publish:
        ensure_and_publish(client, args.database, args.model_database, approved, candidates, policies)
    statistics = corpus_statistics(client, args.database, args.model_database)
    write_report(approved, candidates, policies, statistics)
    print(json.dumps({"status":"ok", "output":str(output), "report":str(REPORT_PATH), "forms":len(forms), "approved":len(approved), "candidates":len(candidates), "published":args.publish}, indent=2))


if __name__ == "__main__": main()
