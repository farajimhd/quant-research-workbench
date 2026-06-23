from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.paths import machine_name  # noqa: E402


DEFAULT_TARGET_DATABASE = "q_live"
DEFAULT_SEC_CORE_DATABASE = "sec_core"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_02c_weak_reference_report")


@dataclass(frozen=True, slots=True)
class StepPaths:
    run_root: Path
    manifest_json: Path
    rows_jsonl: Path
    summary_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "StepPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "step_02c_manifest.json",
            rows_jsonl=run_root / "weak_reference_candidates.jsonl",
            summary_json=run_root / "weak_reference_summary.json",
            summary_md=run_root / "step_02c_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Step 2c of q_live migration: report remaining active US-stock candidates "
            "whose issuer identity is weak. This is read-only and uses SEC/Massive/q_live "
            "evidence only; IBKR-public issuer rows are treated as provisional."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--target-database", default=os.environ.get("QLIVE_MIGRATION_TARGET_DATABASE", DEFAULT_TARGET_DATABASE))
    parser.add_argument("--sec-core-database", default=os.environ.get("SEC_CORE_DATABASE", DEFAULT_SEC_CORE_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("QLIVE_MIGRATION_STEP_02C_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_database_name(args.target_database, "--target-database")
    validate_database_name(args.sec_core_database, "--sec-core-database")

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    paths = StepPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print("=" * 96, flush=True)
    print("q_live migration step 2c: weak reference candidate report", flush=True)
    print(f"target_database={args.target_database}", flush=True)
    print(f"sec_core_database={args.sec_core_database}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print("loaded_env_files=" + json.dumps([str(path) for path in loaded_env]), flush=True)
    print(
        "secret_status="
        + json.dumps(
            secret_status(
                [
                    "QLIVE_MIGRATION_CLICKHOUSE_URL",
                    "QLIVE_MIGRATION_CLICKHOUSE_USER",
                    "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                ]
            ),
            sort_keys=True,
        ),
        flush=True,
    )
    print("=" * 96, flush=True)

    rows = query_json_each_row(client, weak_candidate_report_sql(args.target_database, args.sec_core_database))
    write_jsonl(paths.rows_jsonl, rows)
    summary = build_summary(rows)
    paths.summary_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_summary_md(paths.summary_md, args, summary, paths)
    write_manifest(paths.manifest_json, args, paths, loaded_env, summary)

    print(f"rows={len(rows):,}", flush=True)
    for bucket, count in summary["weak_label_counts"].items():
        print(f"  {bucket}={count:,}", flush=True)
    print(f"rows_jsonl={paths.rows_jsonl}", flush=True)
    print(f"summary_md={paths.summary_md}", flush=True)


def weak_candidate_report_sql(target_database: str, sec_core_database: str) -> str:
    db = quote_ident(target_database)
    sec_db = quote_ident(sec_core_database)
    return f"""
WITH durable_issuers AS
(
    SELECT DISTINCT issuer_id
    FROM {db}.id_issuer_identifier_v1 FINAL
    WHERE lower(identifier_kind) IN ('cik', 'lei', 'ein')
      AND identifier_value_normalized != ''
),
weak_candidates AS
(
    SELECT
        sym.ticker AS ticker,
        sym.ticker_normalized AS ticker_normalized,
        sym.symbol_id AS symbol_id,
        l.listing_id AS listing_id,
        sec.security_id AS security_id,
        sec.issuer_id AS issuer_id,
        ifNull(issuer.issuer_name, '') AS issuer_name,
        sec.security_name AS security_name,
        l.exchange_code AS exchange_code,
        l.currency_code AS currency_code,
        l.ibkr_conid AS ibkr_conid,
        sec.product_type AS product_type,
        sec.asset_class AS asset_class,
        sym.source_system AS symbol_source,
        ifNull(ex.iso_country_code, '') AS exchange_country,
        upper(replaceRegexpAll(ifNull(issuer.issuer_name, ''), '[^A-Za-z0-9]', '')) AS normalized_issuer_name
    FROM {db}.id_symbol_v1 AS sym FINAL
    INNER JOIN {db}.id_listing_v1 AS l FINAL ON l.listing_id = sym.listing_id
    INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.security_id = l.security_id
    LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = sec.issuer_id
    LEFT JOIN {db}.ref_exchange_v1 AS ex FINAL ON ex.exchange_code = l.exchange_code
    WHERE sym.status = 'active'
      AND sym.primary_symbol_flag = 1
      AND l.listing_status = 'active'
      AND upper(l.currency_code) = 'USD'
      AND upper(ifNull(ex.iso_country_code, '')) = 'US'
      AND upper(sec.product_type) IN ('STK', 'STOCK', 'STOCKS')
      AND sec.issuer_id NOT IN (SELECT issuer_id FROM durable_issuers)
),
sec_ticker_groups AS
(
    SELECT
        upper(ticker) AS ticker_normalized,
        groupUniqArray(cik) AS ciks,
        groupUniqArray(exchange) AS exchanges,
        count() AS rows
    FROM {sec_db}.sec_bulk_mirror_company_ticker_v1 FINAL
    WHERE is_active = 1
      AND ifNull(ticker, '') != ''
      AND ifNull(cik, '') != ''
    GROUP BY upper(ticker)
),
sec_name_groups AS
(
    SELECT
        upper(replaceRegexpAll(entity_name, '[^A-Za-z0-9]', '')) AS normalized_issuer_name,
        groupUniqArray(cik) AS ciks,
        groupUniqArray(entity_name) AS entity_names,
        count() AS rows
    FROM {sec_db}.sec_bulk_mirror_company_v1 FINAL
    WHERE ifNull(entity_name, '') != ''
      AND ifNull(cik, '') != ''
    GROUP BY upper(replaceRegexpAll(entity_name, '[^A-Za-z0-9]', ''))
),
canonical_name_groups AS
(
    SELECT
        upper(replaceRegexpAll(issuer.issuer_name, '[^A-Za-z0-9]', '')) AS normalized_issuer_name,
        groupUniqArray(issuer.issuer_id) AS issuer_ids,
        count() AS rows
    FROM {db}.id_issuer_v1 AS issuer FINAL
    WHERE issuer.issuer_id IN (SELECT issuer_id FROM durable_issuers)
      AND NOT startsWith(issuer.issuer_id, 'issuer:ibkr_public:')
    GROUP BY upper(replaceRegexpAll(issuer.issuer_name, '[^A-Za-z0-9]', ''))
)
SELECT
    multiIf(
        upper(w.exchange_code) = 'OTCLNKECN' OR positionCaseInsensitive(w.exchange_code, 'OTC') > 0, 'weak_identity_otc',
        upper(w.exchange_code) NOT IN ('NYSE', 'NASDAQ', 'AMEX', 'BATS'), 'weak_identity_secondary_venue',
        startsWith(w.issuer_id, 'issuer:ibkr_public:'), 'weak_identity_ibkr_only',
        'weak_identity_needs_manual_review'
    ) AS weak_label,
    w.ticker,
    w.exchange_code,
    w.issuer_id,
    w.issuer_name,
    w.security_id,
    w.security_name,
    w.listing_id,
    w.symbol_id,
    w.ibkr_conid,
    w.product_type,
    w.asset_class,
    w.symbol_source,
    multiIf(
        length(ifNull(st.ciks, [])) = 0, 'none',
        length(st.ciks) = 1, 'unique',
        'ambiguous'
    ) AS sec_ticker_match_status,
    ifNull(st.ciks, []) AS sec_ticker_ciks,
    ifNull(st.exchanges, []) AS sec_ticker_exchanges,
    multiIf(
        length(ifNull(sn.ciks, [])) = 0, 'none',
        length(sn.ciks) = 1 AND sn.rows = 1, 'unique_exact_name',
        'ambiguous_exact_name'
    ) AS sec_name_match_status,
    ifNull(sn.ciks, []) AS sec_name_ciks,
    multiIf(
        length(ifNull(cn.issuer_ids, [])) = 0, 'none',
        length(cn.issuer_ids) = 1, 'unique_exact_name',
        'ambiguous_exact_name'
    ) AS qlive_canonical_name_match_status,
    ifNull(cn.issuer_ids, []) AS qlive_canonical_issuer_ids,
    'Issuer lacks SEC/LEI/EIN durable identity after deterministic SEC/Massive/q_live repair paths; keep non-tradable.' AS block_reason
FROM weak_candidates AS w
LEFT JOIN sec_ticker_groups AS st ON st.ticker_normalized = w.ticker_normalized
LEFT JOIN sec_name_groups AS sn ON sn.normalized_issuer_name = w.normalized_issuer_name
LEFT JOIN canonical_name_groups AS cn ON cn.normalized_issuer_name = w.normalized_issuer_name
ORDER BY weak_label, w.exchange_code, w.ticker, w.issuer_id
FORMAT JSONEachRow
"""


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "weak_label_counts": count_by(rows, "weak_label"),
        "exchange_counts": count_by(rows, "exchange_code"),
        "sec_ticker_match_status_counts": count_by(rows, "sec_ticker_match_status"),
        "sec_name_match_status_counts": count_by(rows, "sec_name_match_status"),
        "qlive_canonical_name_match_status_counts": count_by(rows, "qlive_canonical_name_match_status"),
    }
    return summary


def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def write_summary_md(path: Path, args: argparse.Namespace, summary: dict[str, Any], paths: StepPaths) -> None:
    lines = [
        "# q_live Step 02c Weak Reference Candidate Report",
        "",
        f"- target_database: `{args.target_database}`",
        f"- sec_core_database: `{args.sec_core_database}`",
        f"- row_count: `{summary['row_count']}`",
        f"- rows_jsonl: `{paths.rows_jsonl}`",
        "",
        "## Weak Labels",
        "",
    ]
    for label, count in summary["weak_label_counts"].items():
        lines.append(f"- {label}: `{count}`")
    lines.extend(["", "## SEC Match Status", ""])
    for label, count in summary["sec_ticker_match_status_counts"].items():
        lines.append(f"- ticker {label}: `{count}`")
    for label, count in summary["sec_name_match_status_counts"].items():
        lines.append(f"- name {label}: `{count}`")
    lines.extend(["", "## q_live Canonical Name Status", ""])
    for label, count in summary["qlive_canonical_name_match_status_counts"].items():
        lines.append(f"- {label}: `{count}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest(path: Path, args: argparse.Namespace, paths: StepPaths, loaded_env: list[Path], summary: dict[str, Any]) -> None:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "machine": machine_name(),
        "repo_root": str(REPO_ROOT),
        "git_commit": quiet_git_commit(REPO_ROOT),
        "job_type": "step_02c_report_weak_reference_candidates",
        "target_database": args.target_database,
        "sec_core_database": args.sec_core_database,
        "run_root": str(paths.run_root),
        "rows_jsonl": str(paths.rows_jsonl),
        "summary_json": str(paths.summary_json),
        "summary_md": str(paths.summary_md),
        "loaded_env_files": [str(path) for path in loaded_env],
        "summary": summary,
        "secret_status": secret_status(
            [
                "QLIVE_MIGRATION_CLICKHOUSE_URL",
                "QLIVE_MIGRATION_CLICKHOUSE_USER",
                "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
            ]
        ),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")


def query_json_each_row(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql)
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def validate_database_name(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def quiet_git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return ""


def default_migration_clickhouse_url() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL") or default_clickhouse_url()


def default_migration_clickhouse_user() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER") or default_clickhouse_user()


def default_migration_clickhouse_password() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD") or default_clickhouse_password()


if __name__ == "__main__":
    main()
