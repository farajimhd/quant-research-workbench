from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.reference_data.sec_issuer_relationships import (  # noqa: E402
    DEFAULT_RELATIONSHIP_PATH,
    load_relationship_definitions,
)
from pipelines.sec.edgar.sec_pipeline.xbrl_context import (  # noqa: E402
    SecXbrlContextSync,
    XbrlContextSyncConfig,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


RELATIONSHIP_SYNC_SCRIPT = REPO_ROOT / "pipelines" / "reference_data" / "sync_sec_issuer_relationships.py"
BRIDGE_BUILD_SCRIPT = REPO_ROOT / "pipelines" / "reference_data" / "migration" / "step_06_build_q_live_bridge_features.py"
EMBEDDING_BUILD_SCRIPT = REPO_ROOT / "pipelines" / "market_sip" / "events" / "clickhouse_build_text_tokens.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish curated SEC issuer-parent relationships, rebuild the SEC market bridge, "
            "reconcile pending XBRL context, and audit remaining unmapped CIKs and embedding eligibility."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--source-database", default="q_live")
    parser.add_argument("--context-database", default="market_sip_compact")
    parser.add_argument("--bridge-table", default="id_sec_market_bridge_v3")
    parser.add_argument("--relationship-table", default="id_issuer_relationship_v1")
    parser.add_argument("--relationships-json", default=str(DEFAULT_RELATIONSHIP_PATH))
    parser.add_argument("--context-table", default="sec_xbrl_context_v3")
    parser.add_argument("--context-manifest-table", default="sec_xbrl_context_sync_manifest_v3")
    parser.add_argument("--token-table", default="sec_filing_text_tokens_v3")
    parser.add_argument("--embedding-table", default="sec_filing_text_embeddings_v3")
    parser.add_argument("--reconcile-limit", type=int, default=100_000)
    parser.add_argument("--output-root-win", default="D:/market-data/prepared/sec_issuer_relationship_repair")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    definitions = load_relationship_definitions(Path(args.relationships_json))
    curated_ciks = tuple(sorted({str(row["child_cik"]) for row in definitions}))
    run_id = "sec_issuer_relationship_repair_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    pending_before = pending_rows(client, args, curated_ciks)
    subprocess_results: list[dict[str, Any]] = []
    reconciled: list[dict[str, Any]] = []
    if args.execute:
        subprocess_results.append(
            run_required(
                [
                    sys.executable,
                    str(RELATIONSHIP_SYNC_SCRIPT),
                    "--execute",
                    "--database",
                    args.source_database,
                    "--table",
                    args.relationship_table,
                    "--relationships-json",
                    args.relationships_json,
                    "--output-root-win",
                    str(run_root / "relationships"),
                ],
                "relationship_sync",
            )
        )
        subprocess_results.append(
            run_required(
                [
                    sys.executable,
                    str(BRIDGE_BUILD_SCRIPT),
                    "--execute",
                    "--allow-non-empty-targets",
                    "--target-database",
                    args.source_database,
                    "--sec-bridge-table",
                    args.bridge_table,
                    "--specs",
                    "sec_market_bridge",
                    "--output-root-win",
                    str(run_root / "bridge"),
                ],
                "bridge_rebuild",
            )
        )
        sync = SecXbrlContextSync(
            client,
            XbrlContextSyncConfig(
                source_database=args.source_database,
                bridge_database=args.source_database,
                context_database=args.context_database,
                bridge_table=args.bridge_table,
                context_table=args.context_table,
                manifest_table=args.context_manifest_table,
            ),
        )
        sync.ensure_tables()
        stale_results = sync.reconcile_stale_mappings(limit=max(0, args.reconcile_limit))
        remaining_limit = max(0, args.reconcile_limit - len(stale_results))
        reconciled = [asdict(item) for item in stale_results]
        reconciled.extend(asdict(item) for item in sync.reconcile_pending(limit=remaining_limit))

    relationship_bridges = query_rows(client, relationship_bridge_sql(args, curated_ciks))
    pending_after = pending_rows(client, args, curated_ciks)
    embedding_coverage = query_rows(
        client,
        embedding_coverage_sql(
            args,
            curated_ciks,
            token_table_exists=table_exists(client, args.context_database, args.token_table),
            embedding_table_exists=table_exists(client, args.context_database, args.embedding_table),
        ),
    )
    unmapped = query_rows(client, unmapped_cik_audit_sql(args))
    write_jsonl(run_root / "unmapped_sec_ciks.jsonl", unmapped)

    missing_curated = sorted(set(curated_ciks) - {str(row["cik"]) for row in relationship_bridges})
    remaining_curated_pending = [row for row in pending_after if str(row.get("status")) == "pending_mapping"]
    status = "ok"
    if args.execute and (missing_curated or remaining_curated_pending):
        status = "failed"
    report = {
        "run_id": run_id,
        "status": status if args.execute else "dry_run",
        "execute": bool(args.execute),
        "curated_child_ciks": curated_ciks,
        "relationship_bridge_rows": relationship_bridges,
        "missing_curated_bridge_ciks": missing_curated,
        "pending_before": pending_before,
        "pending_after": pending_after,
        "reconciled": reconciled,
        "embedding_coverage": embedding_coverage,
        "remaining_unmapped_cik_count": len(unmapped),
        "subprocesses": subprocess_results,
        "embedding_command": embedding_command(args, curated_ciks, embedding_coverage),
    }
    (run_root / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_root / "REPORT.md").write_text(markdown_report(report), encoding="utf-8")
    print("summary=" + json.dumps({**report, "run_root": str(run_root)}, ensure_ascii=True, sort_keys=True), flush=True)
    return 1 if status == "failed" else 0


def run_required(command: list[str], stage: str) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    result = {
        "stage": stage,
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-4000:],
        "stderr_tail": completed.stderr[-4000:],
    }
    if completed.returncode != 0:
        raise RuntimeError(f"{stage} failed: {json.dumps(result, ensure_ascii=True, sort_keys=True)}")
    return result


def query_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql.strip().rstrip(";") + "\nFORMAT JSONEachRow")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    value = client.execute(
        "SELECT count() FROM system.tables "
        f"WHERE database = {sql_string(database)} AND name = {sql_string(table)} FORMAT TSV"
    ).strip()
    return int(value or 0) == 1


def ciks_sql(ciks: tuple[str, ...]) -> str:
    return ", ".join(sql_string(cik) for cik in ciks)


def relationship_bridge_sql(args: argparse.Namespace, ciks: tuple[str, ...]) -> str:
    db = quote_ident(args.source_database)
    return f"""
SELECT cik, ticker, issuer_id, security_id, listing_id, symbol_id, mapping_method,
       confidence_score, valid_from_date, valid_to_date_exclusive, bridge_id
FROM {db}.{quote_ident(args.bridge_table)} FINAL
WHERE cik IN ({ciks_sql(ciks)})
  AND mapping_status = 'active'
ORDER BY cik, ticker, listing_id
"""


def pending_rows(client: ClickHouseHttpClient, args: argparse.Namespace, ciks: tuple[str, ...]) -> list[dict[str, Any]]:
    context = quote_ident(args.context_database)
    table = quote_ident(args.context_manifest_table)
    exists = client.execute(
        "SELECT count() FROM system.tables "
        f"WHERE database = {sql_string(args.context_database)} AND name = {sql_string(args.context_manifest_table)} FORMAT TSV"
    ).strip()
    if int(exists or 0) == 0:
        return []
    return query_rows(
        client,
        f"""
SELECT cik, accession_number, status, source_company_fact_rows, source_frame_observation_rows,
       candidate_rows, inserted_rows, missing_rows, error, updated_at_utc
FROM {context}.{table} FINAL
WHERE cik IN ({ciks_sql(ciks)})
  AND status IN ('pending', 'pending_source', 'pending_mapping', 'failed')
ORDER BY cik, accession_number
""",
    )


def embedding_coverage_sql(
    args: argparse.Namespace,
    ciks: tuple[str, ...],
    *,
    token_table_exists: bool,
    embedding_table_exists: bool,
) -> str:
    source = quote_ident(args.source_database)
    context = quote_ident(args.context_database)
    token_source = (
        f"SELECT cik, accession_number, uniqExact(source_id) AS tokenized_sources "
        f"FROM {context}.{quote_ident(args.token_table)} FINAL "
        f"WHERE cik IN ({ciks_sql(ciks)}) GROUP BY cik, accession_number"
        if token_table_exists
        else "SELECT CAST('', 'String') AS cik, CAST('', 'String') AS accession_number, toUInt64(0) AS tokenized_sources WHERE 0"
    )
    embedding_source = (
        f"SELECT cik, accession_number, uniqExact(source_id) AS embedded_sources "
        f"FROM {context}.{quote_ident(args.embedding_table)} FINAL "
        f"WHERE cik IN ({ciks_sql(ciks)}) GROUP BY cik, accession_number"
        if embedding_table_exists
        else "SELECT CAST('', 'String') AS cik, CAST('', 'String') AS accession_number, toUInt64(0) AS embedded_sources WHERE 0"
    )
    return f"""
WITH rendered AS
(
    SELECT cik, accession_number, document_id, min(accepted_at_utc) AS accepted_at_utc
    FROM {source}.sec_filing_text_rendered_v3 AS r FINAL
    INNER JOIN {source}.sec_filing_v3 AS f FINAL USING (cik, accession_number)
    WHERE cik IN ({ciks_sql(ciks)})
      AND notEmpty(r.text)
      AND f.accepted_at_utc IS NOT NULL
      AND f.accepted_at_source NOT IN ('archive_filing_date_midnight', 'archive_date_midnight', 'filing_date_midnight_fallback')
    GROUP BY cik, accession_number, document_id
), tokenized AS
(
    {token_source}
), embedded AS
(
    {embedding_source}
), rendered_summary AS
(
    SELECT cik, uniqExact(accession_number) AS filing_count, count() AS rendered_document_count,
           min(accepted_at_utc) AS first_accepted_at_utc, max(accepted_at_utc) AS last_accepted_at_utc
    FROM rendered
    GROUP BY cik
), token_summary AS
(
    SELECT cik, sum(tokenized_sources) AS tokenized_source_count FROM tokenized GROUP BY cik
), embedding_summary AS
(
    SELECT cik, sum(embedded_sources) AS embedded_source_count FROM embedded GROUP BY cik
)
SELECT r.cik AS cik, r.filing_count, r.rendered_document_count, r.first_accepted_at_utc, r.last_accepted_at_utc,
       ifNull(t.tokenized_source_count, 0) AS tokenized_source_count,
       ifNull(e.embedded_source_count, 0) AS embedded_source_count
FROM rendered_summary AS r
LEFT JOIN token_summary AS t USING (cik)
LEFT JOIN embedding_summary AS e USING (cik)
ORDER BY r.cik
"""


def unmapped_cik_audit_sql(args: argparse.Namespace) -> str:
    db = quote_ident(args.source_database)
    return f"""
WITH mapped AS
(
    SELECT DISTINCT cik
    FROM {db}.{quote_ident(args.bridge_table)} FINAL
    WHERE mapping_status = 'active' AND ifNull(ticker, '') != ''
), filing_counts AS
(
    SELECT cik, count() AS filing_count, min(filing_date) AS first_filing_date,
           max(filing_date) AS last_filing_date, groupUniqArray(20)(form_type) AS form_types
    FROM {db}.sec_filing_v3 FINAL
    GROUP BY cik
), issuer_lookup AS
(
    SELECT iii.identifier_value_normalized AS cik, any(iii.issuer_id) AS issuer_id,
           any(issuer.issuer_name) AS issuer_name, uniqExact(iii.issuer_id) AS issuer_matches
    FROM {db}.id_issuer_identifier_v1 AS iii FINAL
    LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = iii.issuer_id
    WHERE iii.identifier_kind = 'cik'
    GROUP BY iii.identifier_value_normalized
), market_counts AS
(
    SELECT sec.issuer_id, uniqExact(sec.security_id) AS security_count,
           uniqExactIf(listing.listing_id, listing.listing_status = 'active') AS active_listing_count
    FROM {db}.id_security_v1 AS sec FINAL
    LEFT JOIN {db}.id_listing_v1 AS listing FINAL ON listing.security_id = sec.security_id
    GROUP BY sec.issuer_id
)
SELECT f.cik, ifNull(i.issuer_id, '') AS issuer_id, ifNull(i.issuer_name, '') AS issuer_name,
       f.filing_count, f.first_filing_date, f.last_filing_date, f.form_types,
       ifNull(i.issuer_matches, 0) AS issuer_matches,
       ifNull(m.security_count, 0) AS security_count,
       ifNull(m.active_listing_count, 0) AS active_listing_count
FROM filing_counts AS f
LEFT JOIN mapped ON mapped.cik = f.cik
LEFT JOIN issuer_lookup AS i ON i.cik = f.cik
LEFT JOIN market_counts AS m ON m.issuer_id = i.issuer_id
WHERE mapped.cik = ''
ORDER BY f.filing_count DESC, f.cik
"""


def embedding_command(args: argparse.Namespace, ciks: tuple[str, ...], coverage: list[dict[str, Any]]) -> list[str]:
    dates = [str(row.get(key) or "")[:10] for row in coverage for key in ("first_accepted_at_utc", "last_accepted_at_utc")]
    dates = sorted(value for value in dates if value)
    start_date = dates[0] if dates else "2019-01-01"
    end_date = dates[-1] if dates else datetime.now(UTC).date().isoformat()
    return [
        sys.executable,
        str(EMBEDDING_BUILD_SCRIPT),
        "--sources",
        "sec",
        "--sec-ciks",
        ",".join(ciks),
        "--start-date",
        start_date,
        "--end-date",
        end_date,
        "--build-embeddings",
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# SEC Issuer Relationship Repair",
        "",
        f"- Status: `{report['status']}`",
        f"- Curated child CIKs: `{len(report['curated_child_ciks'])}`",
        f"- Relationship bridge rows: `{len(report['relationship_bridge_rows'])}`",
        f"- Remaining curated pending mappings: `{len(report['pending_after'])}`",
        f"- Remaining unmapped SEC CIKs: `{report['remaining_unmapped_cik_count']}`",
        "",
        "## Relationship Bridges",
        "",
        "| Filing CIK | Ticker | Method | Confidence |",
        "|---|---|---|---:|",
    ]
    for row in report["relationship_bridge_rows"]:
        lines.append(f"| {row['cik']} | {row.get('ticker') or ''} | {row['mapping_method']} | {row['confidence_score']} |")
    lines.extend(["", "## Targeted Embedding Command", "", "```powershell", subprocess.list2cmdline(report["embedding_command"]), "```", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
