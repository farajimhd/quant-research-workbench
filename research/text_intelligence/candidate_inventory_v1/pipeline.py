from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
import pickle
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipelines.news.benzinga.core.clickhouse_writer_v2 import (
    NewsV2TargetConfig,
    assert_v2_ready,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)

from .config import INVENTORY_VERSION, NORMALIZER_VERSION, CandidateInventoryConfig
from .mining import (
    CandidateAccumulator,
    CandidateStats,
    candidate_id,
    year_count,
)
from .normalize import STOP_WORDS, normalized_pmi
from .sources import WorkUnit, cursor_from_document, fetch_page, initial_cursor, work_units


KEYWORD_STOP_WORDS = STOP_WORDS | {
    "about", "after", "all", "also", "any", "before", "between", "both",
    "but", "can", "could", "did", "do", "does", "during", "each", "had",
    "he", "her", "here", "him", "his", "how", "i", "image", "just", "may",
    "might", "more", "most", "much", "no", "our", "out", "over", "provider",
    "she", "should", "so", "some", "source", "src", "such", "than", "them",
    "then", "they", "through", "under", "up", "us", "very", "we", "what",
    "when", "where", "which", "while", "who", "why", "you", "your",
}


class DocumentBudget:
    def __init__(self, limit: int, *, initial_used: dict[str, int] | None = None) -> None:
        self.limit = int(limit)
        self._used: dict[str, int] = {"news": 0, "sec": 0}
        if initial_used:
            self._used.update(
                {
                    corpus: max(0, int(count))
                    for corpus, count in initial_used.items()
                    if corpus in self._used
                }
            )
        self._lock = threading.Lock()

    def exhausted(self, corpus: str) -> bool:
        if self.limit <= 0:
            return False
        with self._lock:
            return self._used[corpus] >= self.limit

    def take(self, corpus: str, requested: int) -> int:
        if self.limit <= 0:
            return requested
        with self._lock:
            remaining = max(0, self.limit - self._used[corpus])
            accepted = min(requested, remaining)
            self._used[corpus] += accepted
            return accepted


class ActiveClients:
    """Allows interruption to close in-flight persistent HTTP connections."""

    def __init__(self) -> None:
        self._clients: set[ClickHouseHttpClient] = set()
        self._lock = threading.Lock()

    def add(self, client: ClickHouseHttpClient) -> None:
        with self._lock:
            self._clients.add(client)

    def remove(self, client: ClickHouseHttpClient) -> None:
        with self._lock:
            self._clients.discard(client)

    def close_all(self) -> None:
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.close()
            except Exception:
                pass


class ProgressReporter:
    def __init__(self, total_units: int) -> None:
        self.total_units = total_units
        self.completed_units = 0
        self.documents = 0
        self.skipped_units = 0
        self.started = time.perf_counter()
        self.last_print = 0.0
        self._lock = threading.Lock()

    def checkpoint(self, unit: str, documents: int, pages: int) -> None:
        with self._lock:
            now = time.perf_counter()
            if now - self.last_print < 15:
                return
            self.last_print = now
            elapsed = now - self.started
            print(
                f"ACTIVE {self.completed_units}/{self.total_units} units"
                f" | {unit} docs={documents:,} pages={pages:,}"
                f" | elapsed={elapsed / 60:.1f}m",
                flush=True,
            )

    def completed(self, unit: str, documents: int, state: str) -> None:
        with self._lock:
            self.completed_units += 1
            self.documents += documents
            if state == "skipped_budget":
                self.skipped_units += 1
                return
            elapsed = time.perf_counter() - self.started
            print(
                f"[{self.completed_units}/{self.total_units}] {unit} {state.upper()}"
                f" docs={documents:,} total={self.documents:,}"
                f" elapsed={elapsed / 60:.1f}m",
                flush=True,
            )


def run(config: CandidateInventoryConfig, *, execute: bool) -> int:
    config.validate()
    assert_external_runtime_root(config.run_root)
    fingerprint = config_fingerprint(config)
    units = work_units(config)
    if not units:
        raise RuntimeError("no work units resolved")
    print(
        "TEXT CANDIDATE INVENTORY"
        f" | version={INVENTORY_VERSION}"
        f" | sources={','.join(config.sources)}"
        f" | units={len(units):,}"
        f" | workers={config.workers}"
        f" | execute={execute}",
        flush=True,
    )
    print(f"Runtime: {config.run_root}", flush=True)
    client = make_client(config)
    try:
        plan = preflight(client, config, units)
    finally:
        client.close()
    print(
        "PREFLIGHT"
        f" | news={plan.get('news_rows', 0):,}"
        f" | sec={plan.get('sec_rows', 0):,}"
        f" | bounded={'yes' if config.max_documents_per_source else 'no'}",
        flush=True,
    )
    if not execute:
        print("DRY RUN COMPLETE | add --execute to mine the corpus", flush=True)
        return 0

    config.run_root.mkdir(parents=True, exist_ok=True)
    write_manifest(config.run_root / "run_manifest.json", config, fingerprint, plan, "running")
    status_path = config.run_root / "status.jsonl"
    append_status(status_path, "started", units=len(units), plan=plan)
    reporter = ProgressReporter(len(units))
    budget = DocumentBudget(
        config.max_documents_per_source,
        initial_used=checkpoint_document_counts(config, units, fingerprint),
    )
    stop_event = threading.Event()
    active_clients = ActiveClients()
    failures: list[tuple[str, str]] = []
    try:
        with ThreadPoolExecutor(max_workers=min(config.workers, len(units))) as executor:
            futures = {
                executor.submit(
                    run_unit,
                    config,
                    unit,
                    fingerprint,
                    reporter,
                    budget,
                    stop_event,
                    status_path,
                    active_clients,
                ): unit
                for unit in units
            }
            try:
                for future in as_completed(futures):
                    unit = futures[future]
                    try:
                        result = future.result()
                        reporter.completed(
                            unit.key,
                            int(result["documents"]),
                            str(result["state"]),
                        )
                    except BaseException as exc:
                        failures.append((unit.key, f"{type(exc).__name__}: {exc}"))
                        stop_event.set()
                        active_clients.close_all()
                        append_status(
                            status_path,
                            "unit_failed",
                            unit=unit.key,
                            error_type=type(exc).__name__,
                            error=str(exc)[:1000],
                        )
                        raise
            except KeyboardInterrupt:
                stop_event.set()
                active_clients.close_all()
                for pending in futures:
                    pending.cancel()
                raise
    except KeyboardInterrupt:
        stop_event.set()
        append_status(status_path, "interrupted")
        print("INTERRUPTED | durable unit checkpoints retained; rerun the same command", flush=True)
        return 130

    merged = merge_units(config, units, fingerprint)
    audit = write_products(
        config,
        merged,
        plan,
        fingerprint,
        all_units_complete=all_units_complete(config, units),
    )
    final_state = "partial" if audit["partial"] else "complete"
    write_manifest(
        config.run_root / "run_manifest.json",
        config,
        fingerprint,
        plan,
        final_state,
        audit=audit,
    )
    append_status(status_path, final_state, **audit)
    print(
        f"{final_state.upper()}"
        f" | documents={audit['documents']:,}"
        f" | keywords={audit['keyword_rows']:,}"
        f" | candidates={audit['candidate_rows']:,}"
        f" | values={audit['value_rows']:,}"
        f" | budget-skipped-units={reporter.skipped_units:,}"
        f" | audit={config.run_root / 'AUDIT.md'}",
        flush=True,
    )
    return 0 if not failures else 1


def run_unit(
    config: CandidateInventoryConfig,
    unit: WorkUnit,
    fingerprint: str,
    reporter: ProgressReporter,
    budget: DocumentBudget,
    stop_event: threading.Event,
    status_path: Path,
    active_clients: ActiveClients,
) -> dict[str, Any]:
    checkpoint_path = unit_checkpoint_path(config.run_root, unit)
    state = load_checkpoint(checkpoint_path)
    if state:
        if state.get("fingerprint") != fingerprint:
            raise RuntimeError(f"checkpoint drift for {unit.key}")
        accumulator = state["accumulator"]
        cursor = tuple(state["cursor"])
        pages = int(state["pages"])
        complete = bool(state.get("complete"))
        if complete:
            return {
                "unit": unit.key,
                "documents": accumulator.counters.documents,
                "state": "complete",
            }
    else:
        if budget.exhausted(unit.corpus):
            return {"unit": unit.key, "documents": 0, "state": "skipped_budget"}
        accumulator = new_accumulator(config, unit.corpus)
        cursor = initial_cursor(unit)
        pages = 0
    client = make_client(config)
    active_clients.add(client)
    budget_stopped = False
    try:
        while not stop_event.is_set():
            if budget.exhausted(unit.corpus):
                budget_stopped = True
                break
            rows = fetch_page(client, config, unit, cursor)
            if not rows:
                break
            allowed = budget.take(unit.corpus, len(rows))
            if allowed <= 0:
                budget_stopped = True
                break
            for document in rows[:allowed]:
                try:
                    accumulator.add_document(document)
                except BaseException:
                    accumulator.counters.failed_documents += 1
                    raise
                cursor = cursor_from_document(document)
            pages += 1
            if allowed < len(rows):
                budget_stopped = True
            if pages % config.checkpoint_pages == 0 or budget_stopped:
                save_checkpoint(
                    checkpoint_path,
                    fingerprint=fingerprint,
                    cursor=cursor,
                    pages=pages,
                    complete=False,
                    budget_stopped=budget_stopped,
                    accumulator=accumulator,
                )
                append_status(
                    status_path,
                    "unit_checkpoint",
                    unit=unit.key,
                    corpus=unit.corpus,
                    documents=accumulator.counters.documents,
                    pages=pages,
                )
                reporter.checkpoint(unit.key, accumulator.counters.documents, pages)
            if budget_stopped:
                break
        complete = not budget_stopped and not stop_event.is_set()
        save_checkpoint(
            checkpoint_path,
            fingerprint=fingerprint,
            cursor=cursor,
            pages=pages,
            complete=complete,
            budget_stopped=budget_stopped,
            accumulator=accumulator,
        )
        append_status(
            status_path,
            "unit_complete" if complete else "unit_partial",
            unit=unit.key,
            corpus=unit.corpus,
            documents=accumulator.counters.documents,
            pages=pages,
        )
        return {
            "unit": unit.key,
            "documents": accumulator.counters.documents,
            "state": "complete" if complete else "partial",
        }
    finally:
        active_clients.remove(client)
        client.close()


def preflight(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
    units: list[WorkUnit],
) -> dict[str, int]:
    db = quote_ident(config.database)
    tables = [
        config.news_event_table,
        config.news_rendered_table,
        config.news_authority_table,
        config.sec_filing_table,
        config.sec_document_table,
        config.sec_rendered_table,
    ]
    requested = (
        tables[:3] if config.sources == ("news",)
        else tables[3:] if config.sources == ("sec",)
        else tables
    )
    table_list = ",".join(sql_string(value) for value in requested)
    present = {
        line.strip()
        for line in client.execute(
            f"SELECT name FROM system.tables WHERE database={sql_string(config.database)}"
            f" AND name IN ({table_list}) FORMAT TSV"
        ).splitlines()
        if line.strip()
    }
    missing = sorted(set(requested) - present)
    if missing:
        raise RuntimeError(f"missing source tables in {config.database}: {missing}")
    if "news" in config.sources:
        assert_v2_ready(
            client,
            NewsV2TargetConfig(
                database=config.database,
                event_table=config.news_event_table,
                rendered_table=config.news_rendered_table,
                authority_table=config.news_authority_table,
            ),
        )
    news_rows = 0
    sec_rows = 0
    if "news" in config.sources:
        news_rows = scalar_int(
            client,
            f"SELECT count() FROM {db}.{quote_ident(config.news_rendered_table)} FINAL"
            f" WHERE published_at_utc >= toDateTime64({sql_string(config.start_date)},9,'UTC')"
            f" AND published_at_utc < toDateTime64({sql_string(config.end_date_exclusive)},9,'UTC')",
        )
    if "sec" in config.sources:
        sec_rows = scalar_int(
            client,
            "SELECT sum(rows) FROM system.parts"
            f" WHERE database={sql_string(config.database)}"
            f" AND table={sql_string(config.sec_rendered_table)}"
            " AND active",
        )
    return {
        "news_rows": news_rows,
        "sec_rows": sec_rows,
        "units": len(units),
    }


def merge_units(
    config: CandidateInventoryConfig,
    units: list[WorkUnit],
    fingerprint: str,
) -> dict[str, CandidateAccumulator]:
    merged: dict[str, CandidateAccumulator] = {}
    for corpus in config.sources:
        merged[corpus] = CandidateAccumulator(
            corpus=corpus,
            capacity=config.merged_candidate_capacity,
            example_limit=config.evidence_examples,
            evidence_chars=config.evidence_chars,
            min_ngram=config.min_ngram,
            max_ngram=config.max_ngram,
            max_unique_per_document=config.max_unique_candidates_per_document,
        )
    for unit in units:
        state = load_checkpoint(unit_checkpoint_path(config.run_root, unit))
        if not state and config.max_documents_per_source:
            continue
        if not state or state.get("fingerprint") != fingerprint:
            raise RuntimeError(f"missing or drifted unit checkpoint for {unit.key}")
        merged[unit.corpus].merge(state["accumulator"])
    return merged


def all_units_complete(
    config: CandidateInventoryConfig,
    units: list[WorkUnit],
) -> bool:
    for unit in units:
        state = load_checkpoint(unit_checkpoint_path(config.run_root, unit))
        if not state or not bool(state.get("complete")):
            return False
    return True


def write_products(
    config: CandidateInventoryConfig,
    merged: dict[str, CandidateAccumulator],
    plan: dict[str, int],
    fingerprint: str,
    *,
    all_units_complete: bool,
) -> dict[str, Any]:
    sqlite_path = config.run_root / "candidate_inventory.sqlite"
    temporary_sqlite = sqlite_path.with_suffix(".sqlite.tmp")
    if temporary_sqlite.exists():
        temporary_sqlite.unlink()
    connection = sqlite3.connect(temporary_sqlite)
    try:
        create_product_schema(connection)
        candidate_rows = 0
        keyword_rows = 0
        value_rows = 0
        csv_rows: list[dict[str, Any]] = []
        keyword_csv_rows: list[dict[str, Any]] = []
        total_documents = 0
        truncated_documents = 0
        failed_documents = 0
        for corpus, accumulator in merged.items():
            documents = accumulator.counters.documents
            total_documents += documents
            truncated_documents += accumulator.counters.candidate_truncated_documents
            failed_documents += accumulator.counters.failed_documents
            ordered = sorted(
                accumulator.candidates.values(),
                key=lambda value: (
                    -(value.document_count - value.error_bound),
                    -value.document_count,
                    value.phrase,
                ),
            )
            retained = [
                value
                for value in ordered
                if value.concept
                or value.document_count - value.error_bound >= config.min_document_frequency
            ][: config.top_output_candidates]
            for value in retained:
                row = product_candidate_row(corpus, value, accumulator, documents)
                connection.execute(
                    """
                    INSERT INTO phrase_candidates VALUES
                    (:candidate_id,:inventory_version,:normalizer_version,:corpus,
                     :normalized_phrase,:token_count,:estimated_document_frequency,
                     :document_frequency_lower_bound,:occurrence_count,:error_bound,
                     :npmi,:first_seen,:last_seen,:year_count,:headline_documents,
                     :body_documents,:concept,:is_seed,:examples_json,:review_status)
                    """,
                    row,
                )
                csv_rows.append(row)
                candidate_rows += 1
            retained_tokens = sorted(
                (
                    value
                    for value in accumulator.tokens.values()
                    if value.token not in KEYWORD_STOP_WORDS
                    and not value.token.startswith("<")
                    and value.document_count - value.error_bound
                    >= config.min_document_frequency
                ),
                key=lambda value: (
                    -(value.document_count - value.error_bound),
                    -value.document_count,
                    value.token,
                ),
            )[: config.top_output_candidates]
            for value in retained_tokens:
                row = {
                    "keyword_id": candidate_id(corpus, f"keyword:{value.token}"),
                    "inventory_version": INVENTORY_VERSION,
                    "normalizer_version": NORMALIZER_VERSION,
                    "corpus": corpus,
                    "keyword": value.token,
                    "estimated_document_frequency": value.document_count,
                    "document_frequency_lower_bound": max(
                        0, value.document_count - value.error_bound
                    ),
                    "error_bound": value.error_bound,
                    "review_status": "proposed",
                }
                connection.execute(
                    """
                    INSERT INTO keyword_candidates VALUES
                    (:keyword_id,:inventory_version,:normalizer_version,:corpus,
                     :keyword,:estimated_document_frequency,
                     :document_frequency_lower_bound,:error_bound,:review_status)
                    """,
                    row,
                )
                keyword_csv_rows.append(row)
                keyword_rows += 1
            for value in sorted(accumulator.values.values(), key=lambda item: item.value_type):
                connection.execute(
                    "INSERT INTO value_types VALUES (?,?,?,?,?,?,?)",
                    (
                        INVENTORY_VERSION,
                        corpus,
                        value.value_type,
                        value.document_count,
                        value.occurrence_count,
                        json.dumps(value.examples, ensure_ascii=False),
                        "proposed",
                    ),
                )
                value_rows += 1
            connection.execute(
                "INSERT INTO corpus_stats VALUES (?,?,?,?,?,?,?,?)",
                (
                    INVENTORY_VERSION,
                    corpus,
                    documents,
                    accumulator.counters.characters,
                    accumulator.counters.values,
                    accumulator.counters.candidates_observed,
                    accumulator.counters.candidate_truncated_documents,
                    accumulator.counters.failed_documents,
                ),
            )
        connection.execute(
            "INSERT INTO metadata VALUES (?,?)",
            ("config_fingerprint", fingerprint),
        )
        connection.execute(
            "INSERT INTO metadata VALUES (?,?)",
            ("created_at_utc", utc_now()),
        )
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary_sqlite, sqlite_path)
    write_candidate_csv(config.run_root / "phrase_candidates.csv", csv_rows)
    write_keyword_csv(config.run_root / "keyword_candidates.csv", keyword_csv_rows)
    partial = bool(
        config.max_documents_per_source
        or truncated_documents
        or failed_documents
        or not all_units_complete
    )
    audit = {
        "documents": total_documents,
        "candidate_rows": candidate_rows,
        "keyword_rows": keyword_rows,
        "value_rows": value_rows,
        "candidate_truncated_documents": truncated_documents,
        "failed_documents": failed_documents,
        "partial": partial,
    }
    write_audit(config.run_root / "AUDIT.md", config, merged, plan, audit)
    return audit


def create_product_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE corpus_stats (
          inventory_version TEXT, corpus TEXT PRIMARY KEY, documents INTEGER,
          characters INTEGER, extracted_values INTEGER, candidates_observed INTEGER,
          candidate_truncated_documents INTEGER, failed_documents INTEGER
        );
        CREATE TABLE phrase_candidates (
          candidate_id TEXT PRIMARY KEY, inventory_version TEXT, normalizer_version TEXT,
          corpus TEXT, normalized_phrase TEXT, token_count INTEGER,
          estimated_document_frequency INTEGER, document_frequency_lower_bound INTEGER,
          occurrence_count INTEGER, error_bound INTEGER, npmi REAL,
          first_seen TEXT, last_seen TEXT, year_count INTEGER,
          headline_documents INTEGER, body_documents INTEGER, concept TEXT,
          is_seed INTEGER, examples_json TEXT, review_status TEXT
        );
        CREATE INDEX phrase_candidates_rank
          ON phrase_candidates(corpus, document_frequency_lower_bound DESC);
        CREATE TABLE keyword_candidates (
          keyword_id TEXT PRIMARY KEY, inventory_version TEXT, normalizer_version TEXT,
          corpus TEXT, keyword TEXT, estimated_document_frequency INTEGER,
          document_frequency_lower_bound INTEGER, error_bound INTEGER,
          review_status TEXT
        );
        CREATE INDEX keyword_candidates_rank
          ON keyword_candidates(corpus, document_frequency_lower_bound DESC);
        CREATE TABLE value_types (
          inventory_version TEXT, corpus TEXT, value_type TEXT,
          document_count INTEGER, occurrence_count INTEGER,
          examples_json TEXT, review_status TEXT,
          PRIMARY KEY(corpus, value_type)
        );
        """
    )


def product_candidate_row(
    corpus: str,
    value: CandidateStats,
    accumulator: CandidateAccumulator,
    documents: int,
) -> dict[str, Any]:
    phrase_tokens = value.phrase.split()
    left = accumulator.tokens.get(phrase_tokens[0])
    right = accumulator.tokens.get(phrase_tokens[-1])
    npmi = (
        normalized_pmi(
            value.document_count,
            left.document_count,
            right.document_count,
            documents,
        )
        if left and right and documents
        else None
    )
    return {
        "candidate_id": candidate_id(corpus, value.phrase),
        "inventory_version": INVENTORY_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "corpus": corpus,
        "normalized_phrase": value.phrase,
        "token_count": value.token_count,
        "estimated_document_frequency": value.document_count,
        "document_frequency_lower_bound": max(0, value.document_count - value.error_bound),
        "occurrence_count": value.occurrence_count,
        "error_bound": value.error_bound,
        "npmi": npmi,
        "first_seen": value.first_seen,
        "last_seen": value.last_seen,
        "year_count": year_count(value.year_mask),
        "headline_documents": value.headline_documents,
        "body_documents": value.body_documents,
        "concept": value.concept,
        "is_seed": int(bool(value.concept)),
        "examples_json": json.dumps(value.examples, ensure_ascii=False),
        "review_status": "proposed",
    }


def write_candidate_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "candidate_id",
        "corpus",
        "normalized_phrase",
        "token_count",
        "document_frequency_lower_bound",
        "estimated_document_frequency",
        "error_bound",
        "npmi",
        "first_seen",
        "last_seen",
        "year_count",
        "headline_documents",
        "body_documents",
        "concept",
        "review_status",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            sorted(
                rows,
                key=lambda row: (
                    row["corpus"],
                    -int(row["document_frequency_lower_bound"]),
                    row["normalized_phrase"],
                ),
            )
        )
    os.replace(temporary, path)


def write_keyword_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "keyword_id",
        "corpus",
        "keyword",
        "document_frequency_lower_bound",
        "estimated_document_frequency",
        "error_bound",
        "review_status",
    ]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            sorted(
                rows,
                key=lambda row: (
                    row["corpus"],
                    -int(row["document_frequency_lower_bound"]),
                    row["keyword"],
                ),
            )
        )
    os.replace(temporary, path)


def write_audit(
    path: Path,
    config: CandidateInventoryConfig,
    merged: dict[str, CandidateAccumulator],
    plan: dict[str, int],
    audit: dict[str, Any],
) -> None:
    lines = [
        "# Text Candidate Inventory V1",
        "",
        f"- Status: **{'partial' if audit['partial'] else 'complete'}**",
        f"- Inventory: `{INVENTORY_VERSION}`",
        f"- Period: `{config.start_date}` to `{config.end_date_exclusive}`",
        f"- Documents processed: {audit['documents']:,}",
        f"- Phrase candidates retained: {audit['candidate_rows']:,}",
        f"- Keyword candidates retained: {audit['keyword_rows']:,}",
        f"- Documents exceeding the per-document candidate bound: {audit['candidate_truncated_documents']:,}",
        f"- Failed documents: {audit['failed_documents']:,}",
        "",
        "A partial run is not a certified corpus inventory. Source text is never copied into this report;",
        "only bounded evidence examples are retained in the runtime SQLite product.",
        "",
    ]
    for corpus, accumulator in merged.items():
        lines.extend(
            [
                f"## {corpus.upper()}",
                "",
                f"- Planned source rows: {plan.get(f'{corpus}_rows', 0):,}",
                f"- Processed documents: {accumulator.counters.documents:,}",
                f"- Processed characters: {accumulator.counters.characters:,}",
                f"- Typed values observed: {accumulator.counters.values:,}",
                "",
                "### Keywords",
                "",
                "| Keyword | DF lower bound | Estimated DF |",
                "|---|---:|---:|",
            ]
        )
        top_tokens = sorted(
            (
                value
                for value in accumulator.tokens.values()
                if value.token not in KEYWORD_STOP_WORDS
                and not value.token.startswith("<")
            ),
            key=lambda value: (
                -(value.document_count - value.error_bound),
                value.token,
            ),
        )[:30]
        for value in top_tokens:
            lines.append(
                f"| `{value.token}` | {max(0, value.document_count - value.error_bound):,}"
                f" | {value.document_count:,} |"
            )
        lines.extend(
            [
                "",
                "### Phrases",
                "",
                "| Candidate | DF lower bound | Estimated DF | Years | Seed concept |",
                "|---|---:|---:|---:|---|",
            ]
        )
        top = sorted(
            accumulator.candidates.values(),
            key=lambda value: (
                -(value.document_count - value.error_bound),
                value.phrase,
            ),
        )[:50]
        for value in top:
            lines.append(
                f"| `{value.phrase}` | {max(0, value.document_count - value.error_bound):,}"
                f" | {value.document_count:,} | {year_count(value.year_mask)}"
                f" | {value.concept or '—'} |"
            )
        lines.append("")
        lines.append("### Typed values")
        lines.append("")
        lines.append("| Type | Documents | Occurrences |")
        lines.append("|---|---:|---:|")
        for value in sorted(accumulator.values.values(), key=lambda item: item.value_type):
            lines.append(
                f"| {value.value_type} | {value.document_count:,} | {value.occurrence_count:,} |"
            )
        lines.append("")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def new_accumulator(config: CandidateInventoryConfig, corpus: str) -> CandidateAccumulator:
    return CandidateAccumulator(
        corpus=corpus,
        capacity=config.unit_candidate_capacity,
        example_limit=config.evidence_examples,
        evidence_chars=config.evidence_chars,
        min_ngram=config.min_ngram,
        max_ngram=config.max_ngram,
        max_unique_per_document=config.max_unique_candidates_per_document,
    )


def unit_checkpoint_path(run_root: Path, unit: WorkUnit) -> Path:
    return run_root / "units" / f"{unit.key}.pickle.gz"


def save_checkpoint(
    path: Path,
    *,
    fingerprint: str,
    cursor: tuple[Any, ...],
    pages: int,
    complete: bool,
    budget_stopped: bool,
    accumulator: CandidateAccumulator,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "inventory_version": INVENTORY_VERSION,
        "fingerprint": fingerprint,
        "cursor": list(cursor),
        "pages": pages,
        "complete": complete,
        "budget_stopped": budget_stopped,
        "accumulator": accumulator,
    }
    with gzip.open(temporary, "wb", compresslevel=3) as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(temporary, path)


def load_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with gzip.open(path, "rb") as handle:
        value = pickle.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid checkpoint payload: {path}")
    return value


def config_fingerprint(config: CandidateInventoryConfig) -> str:
    # This is the representation/checkpoint contract, not an execution-settings
    # hash. Workers, page sizes, checkpoint cadence, timeout, and runtime path may
    # be tuned safely between resumptions without invalidating mined evidence.
    payload = {
        "inventory_version": INVENTORY_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "database": config.database,
        "news_event_table": config.news_event_table,
        "news_rendered_table": config.news_rendered_table,
        "news_authority_table": config.news_authority_table,
        "sec_filing_table": config.sec_filing_table,
        "sec_document_table": config.sec_document_table,
        "sec_rendered_table": config.sec_rendered_table,
        "sources": config.sources,
        "start_date": config.start_date,
        "end_date_exclusive": config.end_date_exclusive,
        "min_ngram": config.min_ngram,
        "max_ngram": config.max_ngram,
        "unit_candidate_capacity": config.unit_candidate_capacity,
        "merged_candidate_capacity": config.merged_candidate_capacity,
        "max_unique_candidates_per_document": config.max_unique_candidates_per_document,
        "min_document_frequency": config.min_document_frequency,
        "top_output_candidates": config.top_output_candidates,
        "evidence_examples": config.evidence_examples,
        "evidence_chars": config.evidence_chars,
        "max_documents_per_source": config.max_documents_per_source,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def checkpoint_document_counts(
    config: CandidateInventoryConfig,
    units: list[WorkUnit],
    fingerprint: str,
) -> dict[str, int]:
    counts = {"news": 0, "sec": 0}
    for unit in units:
        state = load_checkpoint(unit_checkpoint_path(config.run_root, unit))
        if not state:
            continue
        if state.get("fingerprint") != fingerprint:
            raise RuntimeError(f"checkpoint drift for {unit.key}")
        accumulator = state.get("accumulator")
        if not isinstance(accumulator, CandidateAccumulator):
            raise RuntimeError(f"invalid checkpoint accumulator for {unit.key}")
        counts[unit.corpus] += int(accumulator.counters.documents)
    return counts


def make_client(config: CandidateInventoryConfig) -> ClickHouseHttpClient:
    return ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=config.clickhouse_timeout_seconds,
        persistent=True,
    )


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    value = client.execute(sql + " FORMAT TSV").strip()
    return int(value or 0)


def write_manifest(
    path: Path,
    config: CandidateInventoryConfig,
    fingerprint: str,
    plan: dict[str, int],
    status: str,
    *,
    audit: dict[str, Any] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "inventory_version": INVENTORY_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "status": status,
        "config_fingerprint": fingerprint,
        "config": {
            **asdict(config),
            "runtime_root": str(config.runtime_root),
        },
        "plan": plan,
        "audit": audit or {},
        "updated_at_utc": utc_now(),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def append_status(path: Path, event: str, **values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at_utc": utc_now(), "event": event, **values}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        handle.flush()


def assert_external_runtime_root(path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    resolved = path.resolve()
    try:
        resolved.relative_to(repo)
    except ValueError:
        return
    raise RuntimeError(f"generated inventory output cannot be written inside the repository: {resolved}")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()
