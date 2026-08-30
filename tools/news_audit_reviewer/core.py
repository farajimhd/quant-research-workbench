from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pipelines.news.benzinga.core.clickhouse_writer_v2 import json_each_row_batches
from research.mlops.clickhouse import ClickHouseHttpClient, insert_json_each_row, quote_ident, sql_string


CAMPAIGN_ID = "news_synthesis_v61_training_mismatches_personal_review_v2"
CONTRACT_VERSION = "news_synthesis_clickhouse_personal_review_v2"
NEWS_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1")
DEFAULT_EVALUATION = NEWS_ROOT / "news_synthesis_v61_consolidated_gold_v2_evaluation_v1" / "MISMATCHES.jsonl"
DEFAULT_ASSIGNMENTS = (
    Path(r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4")
    / "forecast_eligibility_sentiment_authority_v59_calibrated_reaudit_v2"
    / "EVALUATION_ASSIGNMENTS.csv"
)
EXPECTED_ALL_MISMATCHES = 32_533
EXPECTED_TRAINING_MISMATCHES = 31_856
EXPECTED_HOLDOUT_MISMATCHES = 677
EXPECTED_ASSIGNMENTS = 352_559

SOURCE_TABLE = "news_synthesis_v61_review_source_v2"
MANIFEST_TABLE = "news_synthesis_v61_review_manifest_v2"
LABEL_HISTORY_TABLE = "news_synthesis_v61_operator_label_history_v2"
GROUP_HISTORY_TABLE = "news_synthesis_v61_review_group_history_v2"
NOTE_HISTORY_TABLE = "news_synthesis_v61_review_note_history_v2"

ALLOWED_LABELS = {"eligible", "ineligible", ""}
ALLOWED_DISPOSITIONS = {"all_eligible", "all_ineligible", "mixed", ""}
ALLOWED_GROUP_FIELDS = {
    "synthesis_path", "title_pattern_id", "normalized_title_template", "gold_label",
    "synthesis_label", "confusion_cell", "ticker", "channel", "provider_tag", "author",
    "provider", "year", "month", "review_status",
}
ARRAY_GROUP_FIELDS = {"ticker", "channel", "provider_tag"}
DEFAULT_GROUP_BY = ("synthesis_path", "title_pattern_id", "gold_label")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SOURCE_COLUMNS = [
    "campaign_id", "source_id", "published_at_utc", "published_date", "provider", "title",
    "normalized_title", "teaser", "rendered_text", "rendered_text_hash", "source_revision_key",
    "author", "tickers", "channels", "provider_tags", "content_quality_flags", "population_split",
    "gold_label", "synthesis_label", "confusion_cell", "synthesis_path", "title_pattern_id",
    "normalized_title_template", "forecast_policy_ids", "forecast_reasons", "evaluation_sha256",
    "assignments_sha256", "imported_at_utc",
]
LABEL_COLUMNS = [
    "campaign_id", "decision_id", "source_id", "original_gold_label", "operator_label",
    "decision_source", "group_id", "group_spec_json", "note", "reviewer", "revision",
    "updated_at_utc",
]
GROUP_COLUMNS = [
    "campaign_id", "decision_id", "group_id", "group_spec_json", "disposition", "completed",
    "matched_rows", "note", "reviewer", "revision", "updated_at_utc",
]
NOTE_COLUMNS = [
    "campaign_id", "note_id", "scope_type", "scope_key", "note", "reviewer", "revision",
    "updated_at_utc",
]

PATH_PART_MEANINGS = {
    "single_subject": "News Synthesis resolved the article as primarily about one subject.",
    "multi_subject_digest": "News Synthesis resolved the article as a digest covering multiple subjects.",
    "market_overview": "News Synthesis resolved the article as a market-level overview.",
    "reference_list": "News Synthesis resolved the article as a reference or list-oriented item.",
    "report": "Its language was interpreted as reporting an event or assertion.",
    "recap": "Its language was interpreted as recapping prior or contextual information.",
    "analyze": "Its language was interpreted as analysis rather than a new issuer event.",
    "preview": "Its language was interpreted as previewing a possible or scheduled event.",
    "explain_move": "Its language was interpreted as explaining a market-price move.",
    "issuer": "The assertion was attributed primarily to the issuer.",
    "editorial": "The assertion was attributed primarily to editorial context.",
    "analyst": "The assertion was attributed primarily to an analyst.",
    "regulator": "The assertion was attributed primarily to a regulator or exchange.",
    "mixed": "The attribution was mixed or unresolved.",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_now_clickhouse() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def revision() -> int:
    return time.time_ns()


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc


def split_pipe(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def pattern_explanation(pattern_id: str) -> str:
    if pattern_id == "unmatched":
        return "No deterministic primary title-pattern rule matched; the synthesis path and forecast policy still produced the displayed label."
    family, _, detail = pattern_id.partition(".")
    family_text = {
        "event": "an event pattern", "context": "a contextual or non-new-event pattern",
        "reaction": "a market-reaction pattern", "signal": "a structural language signal",
    }.get(family, "a deterministic title pattern")
    return f"The title matched {family_text}: {detail.replace('_', ' ') or family} ({pattern_id})."


def group_id(spec: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()[:24]


class ClickHouseReviewBackend:
    def __init__(
        self,
        client: ClickHouseHttpClient,
        *,
        database: str = "q_live",
        campaign_id: str = CAMPAIGN_ID,
        evaluation_path: Path = DEFAULT_EVALUATION,
        assignments_path: Path = DEFAULT_ASSIGNMENTS,
    ) -> None:
        self.client = client
        self.database = database
        self.campaign_id = campaign_id
        self.evaluation_path = Path(evaluation_path)
        self.assignments_path = Path(assignments_path)
        self.db = quote_ident(database)

    def execute(self, sql: str) -> str:
        return self.client.execute(sql)

    def rows(self, sql: str) -> list[dict[str, Any]]:
        return list(self.client.iter_json_each_row(sql))

    def ensure_tables(self) -> None:
        self.execute(f"""CREATE TABLE IF NOT EXISTS {self.db}.{quote_ident(SOURCE_TABLE)} (
campaign_id LowCardinality(String),source_id String,published_at_utc DateTime64(9,'UTC'),published_date Date,
provider LowCardinality(String),title String,normalized_title String,teaser String,rendered_text String,
rendered_text_hash String,source_revision_key String,author String,tickers Array(String),channels Array(String),
provider_tags Array(String),content_quality_flags Array(LowCardinality(String)),population_split LowCardinality(String),
gold_label LowCardinality(String),synthesis_label LowCardinality(String),confusion_cell LowCardinality(String),
synthesis_path LowCardinality(String),title_pattern_id LowCardinality(String),normalized_title_template String,
forecast_policy_ids Array(String),forecast_reasons Array(String),evaluation_sha256 FixedString(64),
assignments_sha256 FixedString(64),imported_at_utc DateTime64(6,'UTC')
) ENGINE=ReplacingMergeTree(imported_at_utc) PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (campaign_id,source_id)""")
        self.execute(f"""CREATE TABLE IF NOT EXISTS {self.db}.{quote_ident(MANIFEST_TABLE)} (
campaign_id LowCardinality(String),contract_version LowCardinality(String),status LowCardinality(String),
expected_rows UInt32,source_rows UInt32,holdout_rows UInt32,evaluation_sha256 FixedString(64),
assignments_sha256 FixedString(64),error String,updated_at_utc DateTime64(6,'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc) ORDER BY campaign_id""")
        self.execute(f"""CREATE TABLE IF NOT EXISTS {self.db}.{quote_ident(LABEL_HISTORY_TABLE)} (
campaign_id LowCardinality(String),decision_id UUID,source_id String,original_gold_label LowCardinality(String),
operator_label LowCardinality(String),decision_source LowCardinality(String),group_id String,group_spec_json String,
note String,reviewer String,revision UInt64,updated_at_utc DateTime64(6,'UTC')
) ENGINE=MergeTree PARTITION BY toYYYYMM(updated_at_utc)
ORDER BY (campaign_id,source_id,revision,decision_id)""")
        self.execute(f"""CREATE TABLE IF NOT EXISTS {self.db}.{quote_ident(GROUP_HISTORY_TABLE)} (
campaign_id LowCardinality(String),decision_id UUID,group_id String,group_spec_json String,
disposition LowCardinality(String),completed UInt8,matched_rows UInt32,note String,reviewer String,
revision UInt64,updated_at_utc DateTime64(6,'UTC')
) ENGINE=MergeTree PARTITION BY toYYYYMM(updated_at_utc)
ORDER BY (campaign_id,group_id,revision,decision_id)""")
        self.execute(f"""CREATE TABLE IF NOT EXISTS {self.db}.{quote_ident(NOTE_HISTORY_TABLE)} (
campaign_id LowCardinality(String),note_id UUID,scope_type LowCardinality(String),scope_key String,note String,
reviewer String,revision UInt64,updated_at_utc DateTime64(6,'UTC')
) ENGINE=MergeTree PARTITION BY toYYYYMM(updated_at_utc)
ORDER BY (campaign_id,scope_type,scope_key,revision,note_id)""")

    def prepare_source(self, progress: Any = print) -> dict[str, Any]:
        self.ensure_tables()
        evaluation_hash = sha256_path(self.evaluation_path)
        assignments_hash = sha256_path(self.assignments_path)
        ready = self._ready_manifest()
        if ready and ready.get("evaluation_sha256") == evaluation_hash and ready.get("assignments_sha256") == assignments_hash:
            return self.validate_source()
        self._write_manifest("building", 0, evaluation_hash, assignments_hash, "")
        try:
            mismatches, split_counts = self._load_mismatches()
            assignments = self._load_assignments(set(mismatches))
            existing = {
                str(row["source_id"])
                for row in self.rows(
                    f"SELECT source_id FROM {self.db}.{quote_ident(SOURCE_TABLE)} FINAL "
                    f"WHERE campaign_id={sql_string(self.campaign_id)} FORMAT JSONEachRow"
                )
            }
            missing = [source_id for source_id in mismatches if source_id not in existing]
            by_month: dict[str, list[str]] = defaultdict(list)
            for source_id in missing:
                by_month[str(mismatches[source_id]["published_at_utc"])[:7]].append(source_id)
            progress(f"[prepare] existing={len(existing):,} missing={len(missing):,} months={len(by_month):,}")
            imported = 0
            for month, source_ids in sorted(by_month.items()):
                canonical = self._query_canonical_month(month, source_ids)
                absent = sorted(set(source_ids) - set(canonical))
                if absent:
                    raise ValueError(f"ClickHouse canonical coverage missing {len(absent):,} rows for {month}: {absent[:5]}")
                output: list[dict[str, Any]] = []
                for source_id in source_ids:
                    mismatch, assignment, source = mismatches[source_id], assignments[source_id], canonical[source_id]
                    if str(assignment["population_split"]) != "training_development":
                        raise ValueError(f"holdout assignment entered source snapshot: {source_id}")
                    output.append({
                        "campaign_id": self.campaign_id, "source_id": source_id,
                        "published_at_utc": mismatch["published_at_utc"], "published_date": str(mismatch["published_at_utc"])[:10],
                        "provider": source.get("provider") or "", "title": source.get("title") or mismatch.get("title") or "",
                        "normalized_title": source.get("normalized_title") or "", "teaser": source.get("teaser") or "",
                        "rendered_text": source.get("rendered_text") or source.get("teaser") or source.get("title") or "",
                        "rendered_text_hash": source.get("rendered_text_hash") or "", "source_revision_key": source.get("source_revision_key") or "",
                        "author": source.get("author") or assignment.get("author") or "",
                        "tickers": list(source.get("tickers") or mismatch.get("tickers") or []),
                        "channels": list(source.get("channels") or split_pipe(assignment.get("channels"))),
                        "provider_tags": list(source.get("provider_tags") or split_pipe(assignment.get("provider_tags"))),
                        "content_quality_flags": list(source.get("content_quality_flags") or []),
                        "population_split": "training_development", "gold_label": mismatch["gold_label"],
                        "synthesis_label": mismatch["synthesis_label"], "confusion_cell": mismatch["confusion_cell"],
                        "synthesis_path": mismatch["synthesis_path"],
                        "title_pattern_id": str(assignment.get("primary_pattern_id") or "unmatched"),
                        "normalized_title_template": str(assignment.get("normalized_title_template") or ""),
                        "forecast_policy_ids": list(mismatch.get("forecast_policy_ids") or []),
                        "forecast_reasons": list(mismatch.get("forecast_reasons") or []),
                        "evaluation_sha256": evaluation_hash, "assignments_sha256": assignments_hash,
                        "imported_at_utc": utc_now_clickhouse(),
                    })
                self._insert_source_rows(output)
                imported += len(output)
                progress(f"[prepare] month={month} inserted={len(output):,} total_inserted={imported:,}")
            result = self.validate_source()
            expected_splits = Counter({"training_development": EXPECTED_TRAINING_MISMATCHES, "holdout_august_2026": EXPECTED_HOLDOUT_MISMATCHES})
            if split_counts != expected_splits:
                raise ValueError(f"evaluation split counts changed: {dict(split_counts)}")
            self._write_manifest("ready", result["articles"], evaluation_hash, assignments_hash, "")
            return self.validate_source()
        except Exception as exc:
            self._write_manifest("failed", 0, evaluation_hash, assignments_hash, f"{type(exc).__name__}: {exc}"[:2000])
            raise

    def _load_mismatches(self) -> tuple[dict[str, dict[str, Any]], Counter[str]]:
        all_rows = list(iter_jsonl(self.evaluation_path))
        if len(all_rows) != EXPECTED_ALL_MISMATCHES:
            raise ValueError(f"evaluation mismatch count changed: {len(all_rows):,}")
        result: dict[str, dict[str, Any]] = {}
        splits: Counter[str] = Counter()
        for row in all_rows:
            source_id, split = str(row["source_id"]), str(row["population_split"])
            splits[split] += 1
            if split != "training_development":
                continue
            if source_id in result or row["gold_label"] == row["synthesis_label"]:
                raise ValueError(f"invalid training mismatch: {source_id}")
            result[source_id] = row
        if len(result) != EXPECTED_TRAINING_MISMATCHES:
            raise ValueError(f"training mismatch count changed: {len(result):,}")
        return result, splits

    def _load_assignments(self, wanted: set[str]) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        count = 0
        with self.assignments_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                count += 1
                if str(row["source_id"]) in wanted:
                    result[str(row["source_id"])] = row
        if count != EXPECTED_ASSIGNMENTS:
            raise ValueError(f"assignment population changed: {count:,}")
        missing = wanted - set(result)
        if missing:
            raise ValueError(f"training mismatches missing assignments: {sorted(missing)[:5]}")
        return result

    def _query_canonical_month(self, month: str, source_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        year, month_number = (int(value) for value in month.split("-"))
        start = f"{year:04d}-{month_number:02d}-01"
        end = f"{year + 1:04d}-01-01" if month_number == 12 else f"{year:04d}-{month_number + 1:02d}-01"
        values = ",".join(sql_string(value) for value in source_ids)
        rows = self.rows(f"""
SELECT e.canonical_news_id source_id,e.provider,e.title,e.normalized_title,e.teaser,e.author,
       e.tickers,e.channels,e.provider_tags,e.content_quality_flags,e.source_revision_key
FROM {self.db}.benzinga_news_event_v2 e FINAL
PREWHERE e.published_date >= toDate({sql_string(start)}) AND e.published_date < toDate({sql_string(end)})
WHERE e.canonical_news_id IN ({values}) FORMAT JSONEachRow
""")
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            source_id = str(row["source_id"])
            if source_id in result:
                raise ValueError(f"canonical ClickHouse query returned duplicate source_id: {source_id}")
            result[source_id] = row
        rendered_rows = self.rows(f"""
SELECT r.canonical_news_id source_id,r.rendered_text,r.rendered_text_hash
FROM {self.db}.benzinga_news_rendered_v2 r FINAL
PREWHERE r.published_date >= toDate({sql_string(start)}) AND r.published_date < toDate({sql_string(end)})
WHERE r.canonical_news_id IN ({values}) FORMAT JSONEachRow
""")
        rendered: dict[str, dict[str, Any]] = {}
        for row in rendered_rows:
            source_id = str(row["source_id"])
            if source_id in rendered:
                raise ValueError(f"rendered ClickHouse query returned duplicate source_id: {source_id}")
            rendered[source_id] = row
        for source_id, row in result.items():
            render = rendered.get(source_id) or {}
            fallback = str(row.get("teaser") or row.get("title") or "")
            row["rendered_text"] = str(render.get("rendered_text") or fallback)
            row["rendered_text_hash"] = str(
                render.get("rendered_text_hash")
                or hashlib.sha256(fallback.encode("utf-8")).hexdigest()
            )
        return result

    def _insert_source_rows(self, rows: list[dict[str, Any]]) -> None:
        for batch in json_each_row_batches(rows, table=SOURCE_TABLE, max_rows=250, target_bytes=4 * 1024 * 1024, max_row_bytes=8 * 1024 * 1024):
            insert_json_each_row(self.client, self.database, SOURCE_TABLE, SOURCE_COLUMNS, batch.rows)

    def _write_manifest(self, status: str, source_rows: int, evaluation_hash: str, assignments_hash: str, error: str) -> None:
        row = {
            "campaign_id": self.campaign_id, "contract_version": CONTRACT_VERSION, "status": status,
            "expected_rows": EXPECTED_TRAINING_MISMATCHES, "source_rows": source_rows, "holdout_rows": 0,
            "evaluation_sha256": evaluation_hash, "assignments_sha256": assignments_hash, "error": error,
            "updated_at_utc": utc_now_clickhouse(),
        }
        insert_json_each_row(self.client, self.database, MANIFEST_TABLE, list(row), [row])

    def _ready_manifest(self) -> dict[str, Any] | None:
        rows = self.rows(
            f"SELECT * FROM {self.db}.{quote_ident(MANIFEST_TABLE)} FINAL "
            f"WHERE campaign_id={sql_string(self.campaign_id)} AND status='ready' LIMIT 1 FORMAT JSONEachRow"
        )
        return rows[0] if rows else None

    def validate_source(self) -> dict[str, Any]:
        row = self.rows(f"""
SELECT count() rows,uniqExact(source_id) unique_ids,countIf(population_split!='training_development') non_training,
       countIf(gold_label=synthesis_label) non_mismatches,uniqExact(synthesis_path) paths,
       uniqExact((synthesis_path,title_pattern_id)) groups
FROM {self.db}.{quote_ident(SOURCE_TABLE)} FINAL WHERE campaign_id={sql_string(self.campaign_id)} FORMAT JSONEachRow
""")[0]
        if int(row["rows"]) != EXPECTED_TRAINING_MISMATCHES or int(row["unique_ids"]) != EXPECTED_TRAINING_MISMATCHES:
            raise ValueError(f"ClickHouse source coverage changed: rows={row['rows']} unique={row['unique_ids']}")
        if int(row["non_training"]) or int(row["non_mismatches"]):
            raise ValueError("ClickHouse source contains non-training or non-mismatch rows")
        return {
            "status": "ready", "articles": int(row["rows"]), "holdout_rows": 0,
            "paths": int(row["paths"]), "groups": int(row["groups"]), "database": self.database,
            "source_table": SOURCE_TABLE, "label_history_table": LABEL_HISTORY_TABLE,
        }

    def _current_labels_sql(self) -> str:
        return f"""(
SELECT campaign_id,source_id,argMax(operator_label,(revision,toString(decision_id))) operator_label,
       argMax(decision_source,(revision,toString(decision_id))) decision_source,
       argMax(group_id,(revision,toString(decision_id))) label_group_id,
       argMax(note,(revision,toString(decision_id))) article_comment,max(revision) label_revision,
       argMax(updated_at_utc,(revision,toString(decision_id))) label_updated_at
FROM {self.db}.{quote_ident(LABEL_HISTORY_TABLE)} WHERE campaign_id={sql_string(self.campaign_id)}
GROUP BY campaign_id,source_id)"""

    def summary(self) -> dict[str, Any]:
        row = self.rows(f"""
SELECT count() articles,countIf(notEmpty(l.operator_label)) reviewed_articles,
       countIf(notEmpty(l.operator_label) AND l.operator_label!=s.gold_label) changed_articles,
       countIf(l.operator_label='eligible') operator_eligible,countIf(l.operator_label='ineligible') operator_ineligible
FROM {self.db}.{quote_ident(SOURCE_TABLE)} s FINAL
LEFT JOIN {self._current_labels_sql()} l ON l.campaign_id=s.campaign_id AND l.source_id=s.source_id
WHERE s.campaign_id={sql_string(self.campaign_id)} FORMAT JSONEachRow
""")[0]
        result = {key: int(value) for key, value in row.items()}
        result["unreviewed_articles"] = result["articles"] - result["reviewed_articles"]
        return result

    def notes(self) -> dict[str, str]:
        rows = self.rows(f"""
SELECT scope_type,scope_key,argMax(note,(revision,toString(note_id))) note
FROM {self.db}.{quote_ident(NOTE_HISTORY_TABLE)} WHERE campaign_id={sql_string(self.campaign_id)}
GROUP BY scope_type,scope_key FORMAT JSONEachRow
""")
        return {f"{row['scope_type']}:{row['scope_key']}": str(row["note"]) for row in rows}

    def _all_group_states(self) -> dict[str, dict[str, Any]]:
        rows = self.rows(f"""
SELECT group_id,argMax(disposition,(revision,toString(decision_id))) disposition,
       argMax(completed,(revision,toString(decision_id))) completed,
       argMax(note,(revision,toString(decision_id))) note,
       argMax(matched_rows,(revision,toString(decision_id))) matched_rows,
       max(revision) latest_revision
FROM {self.db}.{quote_ident(GROUP_HISTORY_TABLE)}
WHERE campaign_id={sql_string(self.campaign_id)} GROUP BY group_id FORMAT JSONEachRow
""")
        return {str(row["group_id"]): row for row in rows}

    def set_note(self, scope_type: str, scope_key_value: str, note: str, reviewer: str) -> dict[str, Any]:
        if scope_type not in {"campaign", "workspace", "group"}:
            raise ValueError("invalid note scope")
        row = {
            "campaign_id": self.campaign_id, "note_id": str(uuid.uuid4()), "scope_type": scope_type,
            "scope_key": scope_key_value, "note": note, "reviewer": reviewer, "revision": revision(),
            "updated_at_utc": utc_now_clickhouse(),
        }
        insert_json_each_row(self.client, self.database, NOTE_HISTORY_TABLE, NOTE_COLUMNS, [row])
        return row

    def _group_expression(self, field: str) -> str:
        expressions = {
            "synthesis_path": "s.synthesis_path", "title_pattern_id": "s.title_pattern_id",
            "normalized_title_template": "s.normalized_title_template", "gold_label": "s.gold_label",
            "synthesis_label": "s.synthesis_label", "confusion_cell": "s.confusion_cell",
            "ticker": "arrayJoin(s.tickers)", "channel": "arrayJoin(s.channels)",
            "provider_tag": "arrayJoin(s.provider_tags)", "author": "s.author", "provider": "s.provider",
            "year": "toString(toYear(s.published_at_utc))", "month": "toString(toYYYYMM(s.published_at_utc))",
            "review_status": "multiIf(empty(l.operator_label),'unreviewed',l.operator_label!=s.gold_label,'changed','confirmed')",
        }
        if field not in ALLOWED_GROUP_FIELDS:
            raise ValueError(f"unsupported group field: {field}")
        return expressions[field]

    def normalize_group_by(self, fields: Sequence[str]) -> tuple[str, ...]:
        result = tuple(dict.fromkeys(field for field in fields if field)) or DEFAULT_GROUP_BY
        if len(result) > 3:
            raise ValueError("group by supports at most three fields")
        invalid = set(result) - ALLOWED_GROUP_FIELDS
        if invalid:
            raise ValueError(f"unsupported group fields: {sorted(invalid)}")
        if len(set(result) & ARRAY_GROUP_FIELDS) > 1:
            raise ValueError("choose at most one array-valued group field")
        return result

    def _where(self, filters: Mapping[str, Any], selection: Mapping[str, Any] | None = None) -> str:
        values = {**dict(filters), **dict(selection or {})}
        clauses = [f"s.campaign_id={sql_string(self.campaign_id)}"]
        q = str(values.get("q") or "").strip()
        if q:
            parts = [
                f"positionCaseInsensitiveUTF8(s.title,{sql_string(q)})>0",
                f"positionCaseInsensitiveUTF8(s.normalized_title_template,{sql_string(q)})>0",
                f"positionCaseInsensitiveUTF8(s.teaser,{sql_string(q)})>0",
            ]
            if values.get("search_scope") == "full_text":
                parts.append(f"positionCaseInsensitiveUTF8(s.rendered_text,{sql_string(q)})>0")
            clauses.append("(" + " OR ".join(parts) + ")")
        scalar = {
            "synthesis_path": "s.synthesis_path", "title_pattern_id": "s.title_pattern_id",
            "normalized_title_template": "s.normalized_title_template", "gold_label": "s.gold_label",
            "synthesis_label": "s.synthesis_label", "confusion_cell": "s.confusion_cell",
            "author": "s.author", "provider": "s.provider",
        }
        for key, expression in scalar.items():
            value = str(values.get(key) or "").strip()
            if value:
                clauses.append(f"{expression}={sql_string(value)}")
        for key, column in (("ticker", "s.tickers"), ("channel", "s.channels"), ("provider_tag", "s.provider_tags")):
            value = str(values.get(key) or "").strip()
            if value:
                clauses.append(f"has({column},{sql_string(value)})")
        year, month = str(values.get("year") or "").strip(), str(values.get("month") or "").strip()
        if year:
            if not re.fullmatch(r"\d{4}", year):
                raise ValueError("year must be YYYY")
            clauses.append(f"toYear(s.published_at_utc)={int(year)}")
        if month:
            if not re.fullmatch(r"\d{6}", month):
                raise ValueError("month must be YYYYMM")
            clauses.append(f"toYYYYMM(s.published_at_utc)={int(month)}")
        for key, op in (("date_from", ">="), ("date_to", "<=")):
            value = str(values.get(key) or "").strip()
            if value:
                if not DATE_RE.fullmatch(value):
                    raise ValueError(f"{key} must be YYYY-MM-DD")
                clauses.append(f"s.published_date{op}toDate({sql_string(value)})")
        status = str(values.get("review_status") or "").strip()
        status_clauses = {
            "unreviewed": "empty(l.operator_label)", "reviewed": "notEmpty(l.operator_label)",
            "changed": "notEmpty(l.operator_label) AND l.operator_label!=s.gold_label",
            "confirmed": "notEmpty(l.operator_label) AND l.operator_label=s.gold_label",
            "eligible": "l.operator_label='eligible'", "ineligible": "l.operator_label='ineligible'",
        }
        if status:
            if status not in status_clauses:
                raise ValueError("invalid review status")
            clauses.append(status_clauses[status])
        return " AND ".join(f"({clause})" for clause in clauses)

    def groups(self, filters: Mapping[str, Any], group_by: Sequence[str]) -> dict[str, Any]:
        fields = self.normalize_group_by(group_by)
        group_states = self._all_group_states()
        select_parts = [f"{self._group_expression(field)} AS {quote_ident(field)}" for field in fields]
        group_parts = [quote_ident(field) for field in fields]
        rows = self.rows(f"""
SELECT {','.join(select_parts)},count() rows,countIf(notEmpty(l.operator_label)) reviewed,
       countIf(notEmpty(l.operator_label) AND l.operator_label!=s.gold_label) changed,
       countIf(s.gold_label='eligible') gold_eligible,countIf(s.gold_label='ineligible') gold_ineligible,
       countIf(s.synthesis_label='eligible') synthesis_eligible,countIf(s.synthesis_label='ineligible') synthesis_ineligible
FROM {self.db}.{quote_ident(SOURCE_TABLE)} s FINAL
LEFT JOIN {self._current_labels_sql()} l ON l.campaign_id=s.campaign_id AND l.source_id=s.source_id
WHERE {self._where(filters)} GROUP BY {','.join(group_parts)} ORDER BY rows DESC,{','.join(group_parts)}
LIMIT 2001 FORMAT JSONEachRow
""")
        truncated = len(rows) > 2000
        rows = rows[:2000]
        result = []
        for row in rows:
            selection = {field: str(row.get(field) or "") for field in fields}
            spec = {"filters": dict(filters), "selection": selection}
            identifier = group_id(spec)
            saved = group_states.get(identifier, {})
            result.append({
                "group_id": identifier, "selection": selection,
                "disposition": str(saved.get("disposition") or ""),
                "completed": bool(saved.get("completed") or False),
                **{key: int(row[key]) for key in ("rows", "reviewed", "changed", "gold_eligible", "gold_ineligible", "synthesis_eligible", "synthesis_ineligible")},
            })
        return {
            "group_by": list(fields), "groups": result, "total_groups": len(result),
            "truncated": truncated,
        }

    def articles(self, filters: Mapping[str, Any], selection: Mapping[str, Any], *, page: int, page_size: int) -> dict[str, Any]:
        if page < 1 or not 1 <= page_size <= 250:
            raise ValueError("invalid pagination")
        where = self._where(filters, selection)
        count_row = self.rows(f"""
SELECT count() rows,countIf(notEmpty(l.operator_label)) reviewed,
       countIf(notEmpty(l.operator_label) AND l.operator_label!=s.gold_label) changed,
       countIf(s.gold_label='eligible') gold_eligible,countIf(s.synthesis_label='eligible') synthesis_eligible
FROM {self.db}.{quote_ident(SOURCE_TABLE)} s FINAL
LEFT JOIN {self._current_labels_sql()} l ON l.campaign_id=s.campaign_id AND l.source_id=s.source_id
WHERE {where} FORMAT JSONEachRow
""")[0]
        offset = (page - 1) * page_size
        rows = self.rows(f"""
SELECT s.source_id,toString(s.published_at_utc) published_at_utc,s.provider,s.title,s.normalized_title_template,
       leftUTF8(s.teaser,500) teaser,s.author,s.tickers,s.channels,s.provider_tags,s.gold_label,s.synthesis_label,
       s.confusion_cell,s.synthesis_path,s.title_pattern_id,s.forecast_policy_ids,s.forecast_reasons,
       l.operator_label,l.decision_source,l.article_comment,toString(l.label_updated_at) label_updated_at
FROM {self.db}.{quote_ident(SOURCE_TABLE)} s FINAL
LEFT JOIN {self._current_labels_sql()} l ON l.campaign_id=s.campaign_id AND l.source_id=s.source_id
WHERE {where} ORDER BY s.published_at_utc,s.source_id LIMIT {page_size} OFFSET {offset} FORMAT JSONEachRow
""")
        spec = {"filters": dict(filters), "selection": dict(selection)}
        total = int(count_row["rows"])
        return {
            "group_id": group_id(spec), "group_spec": spec,
            "summary": {key: int(value) for key, value in count_row.items()},
            "context": self.group_context(filters, selection), "rows": rows, "page": page,
            "page_size": page_size, "total_pages": max(1, (total + page_size - 1) // page_size),
            "group_state": self.group_state(spec),
        }

    def group_context(self, filters: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, Any]:
        where = self._where(filters, selection)
        reasons = self.rows(f"""
SELECT reason value,count() count FROM (
 SELECT arrayJoin(s.forecast_reasons) reason FROM {self.db}.{quote_ident(SOURCE_TABLE)} s FINAL
 LEFT JOIN {self._current_labels_sql()} l ON l.campaign_id=s.campaign_id AND l.source_id=s.source_id
 WHERE {where}) WHERE notEmpty(reason) GROUP BY reason ORDER BY count DESC LIMIT 8 FORMAT JSONEachRow
""")
        path, pattern = str(selection.get("synthesis_path") or ""), str(selection.get("title_pattern_id") or "")
        return {
            "path_reasons": [PATH_PART_MEANINGS.get(part.strip(), f"News Synthesis emitted the {part.strip()} path component.") for part in path.split(">") if part.strip()],
            "pattern_reason": pattern_explanation(pattern) if pattern else "This custom group can contain multiple title patterns.",
            "decision_reasons": [{"value": str(row["value"]), "count": int(row["count"])} for row in reasons],
        }

    def article_detail(self, source_id: str) -> dict[str, Any]:
        rows = self.rows(f"""
SELECT s.*,l.operator_label,l.decision_source,l.article_comment,toString(l.label_updated_at) label_updated_at
FROM {self.db}.{quote_ident(SOURCE_TABLE)} s FINAL
LEFT JOIN {self._current_labels_sql()} l ON l.campaign_id=s.campaign_id AND l.source_id=s.source_id
WHERE s.campaign_id={sql_string(self.campaign_id)} AND s.source_id={sql_string(source_id)} LIMIT 1 FORMAT JSONEachRow
""")
        if not rows:
            raise KeyError("unknown source_id")
        return rows[0]

    def set_article_label(self, source_id: str, label: str, note: str, reviewer: str) -> dict[str, Any]:
        if label not in ALLOWED_LABELS:
            raise ValueError("operator label must be eligible, ineligible, or empty")
        source = self.article_detail(source_id)
        row = {
            "campaign_id": self.campaign_id, "decision_id": str(uuid.uuid4()), "source_id": source_id,
            "original_gold_label": source["gold_label"], "operator_label": label,
            "decision_source": "article" if label else "cleared", "group_id": "", "group_spec_json": "",
            "note": note, "reviewer": reviewer, "revision": revision(), "updated_at_utc": utc_now_clickhouse(),
        }
        insert_json_each_row(self.client, self.database, LABEL_HISTORY_TABLE, LABEL_COLUMNS, [row])
        return row

    def save_group(
        self, *, filters: Mapping[str, Any], selection: Mapping[str, Any], disposition: str,
        completed: bool, note: str, reviewer: str, apply_label: str = "",
    ) -> dict[str, Any]:
        if disposition not in ALLOWED_DISPOSITIONS or apply_label not in ALLOWED_LABELS:
            raise ValueError("invalid group disposition or bulk label")
        spec = {"filters": dict(filters), "selection": dict(selection)}
        identifier, spec_json = group_id(spec), canonical_json(spec)
        existing = self.group_state(spec)
        sources: list[dict[str, Any]] = []
        if apply_label or not int(existing.get("latest_revision") or 0):
            sources = self.rows(f"""
SELECT s.source_id,s.gold_label FROM {self.db}.{quote_ident(SOURCE_TABLE)} s FINAL
LEFT JOIN {self._current_labels_sql()} l ON l.campaign_id=s.campaign_id AND l.source_id=s.source_id
WHERE {self._where(filters, selection)} ORDER BY s.source_id FORMAT JSONEachRow
""")
            if not sources:
                raise ValueError("group query matched no articles")
            matched_rows = len(sources)
        else:
            matched_rows = int(existing["matched_rows"])
        if apply_label:
            now, base_revision = utc_now_clickhouse(), revision()
            decisions = [
                {
                    "campaign_id": self.campaign_id, "decision_id": str(uuid.uuid4()), "source_id": source["source_id"],
                    "original_gold_label": source["gold_label"], "operator_label": apply_label,
                    "decision_source": "group_bulk", "group_id": identifier, "group_spec_json": spec_json,
                    "note": "", "reviewer": reviewer, "revision": base_revision + index, "updated_at_utc": now,
                }
                for index, source in enumerate(sources)
            ]
            for batch in json_each_row_batches(decisions, table=LABEL_HISTORY_TABLE, max_rows=500, target_bytes=4 * 1024 * 1024, max_row_bytes=8 * 1024 * 1024):
                insert_json_each_row(self.client, self.database, LABEL_HISTORY_TABLE, LABEL_COLUMNS, batch.rows)
        group_row = {
            "campaign_id": self.campaign_id, "decision_id": str(uuid.uuid4()), "group_id": identifier,
            "group_spec_json": spec_json, "disposition": disposition, "completed": int(completed),
            "matched_rows": matched_rows, "note": note, "reviewer": reviewer, "revision": revision(),
            "updated_at_utc": utc_now_clickhouse(),
        }
        insert_json_each_row(self.client, self.database, GROUP_HISTORY_TABLE, GROUP_COLUMNS, [group_row])
        return {**group_row, "summary": self.summary()}

    def group_state(self, spec: Mapping[str, Any]) -> dict[str, Any]:
        identifier = group_id(spec)
        rows = self.rows(f"""
SELECT group_id,argMax(disposition,(revision,toString(decision_id))) disposition,
       argMax(completed,(revision,toString(decision_id))) completed,argMax(note,(revision,toString(decision_id))) note,
       argMax(matched_rows,(revision,toString(decision_id))) matched_rows,max(revision) latest_revision
FROM {self.db}.{quote_ident(GROUP_HISTORY_TABLE)}
WHERE campaign_id={sql_string(self.campaign_id)} AND group_id={sql_string(identifier)} GROUP BY group_id FORMAT JSONEachRow
""")
        return rows[0] if rows else {"group_id": identifier, "disposition": "", "completed": 0, "note": "", "matched_rows": 0, "latest_revision": 0}
