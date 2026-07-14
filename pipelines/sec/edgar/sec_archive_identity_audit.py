from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import tarfile
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_filing_text_extract_parts import parse_filing  # noqa: E402
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify unresolved q_live filing identities directly against daily-archive SGML headers."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", "q_live"))
    parser.add_argument("--submissions-database", default=os.environ.get("SEC_BULK_MIRROR_DATABASE", "sec_core"))
    parser.add_argument("--submissions-table", default="sec_bulk_mirror_filing_v3")
    parser.add_argument("--submissions-overlay-table", default="sec_submissions_filing_overlay_v3")
    parser.add_argument("--output-root-win", default="D:/market-data/prepared/sec_archive_identity_audit")
    parser.add_argument("--workers", type=int, default=32)
    return parser.parse_args()


def main() -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    for name in ("database", "submissions_database", "submissions_table", "submissions_overlay_table"):
        validate_identifier(str(getattr(args, name)), "--" + name.replace("_", "-"))
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    rows = load_unconfirmed_document_identities(client, args)
    grouped: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        grouped[row["archive_path"]][row["member"].lstrip("./")] = row

    run_id = "sec_archive_identity_audit_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    results_path = run_root / "archive_results.jsonl"
    started = time.perf_counter()
    totals = {"archives": len(grouped), "relationships": len(rows), "matched": 0, "mismatched": 0, "missing": 0, "archive_errors": 0}
    print(f"archives={len(grouped):,} relationships={len(rows):,} workers={args.workers}", flush=True)
    with results_path.open("w", encoding="utf-8") as output:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(audit_archive, archive_path, wanted) for archive_path, wanted in sorted(grouped.items())]
            for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
                result = future.result()
                output.write(json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n")
                output.flush()
                for key in ("matched", "mismatched", "missing", "archive_errors"):
                    totals[key] += int(result[key])
                if completed % 25 == 0 or completed == len(futures):
                    print(
                        f"archives={completed:,}/{len(futures):,} matched={totals['matched']:,} "
                        f"mismatched={totals['mismatched']:,} missing={totals['missing']:,} "
                        f"errors={totals['archive_errors']:,} elapsed={time.perf_counter() - started:.1f}s",
                        flush=True,
                    )
    totals["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    totals["run_id"] = run_id
    totals["results_path"] = str(results_path)
    (run_root / "summary.json").write_text(json.dumps(totals, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("summary=" + json.dumps(totals, sort_keys=True), flush=True)
    return 1 if totals["mismatched"] or totals["missing"] or totals["archive_errors"] else 0


def load_unconfirmed_document_identities(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[dict[str, str]]:
    overlay_union = ""
    if table_exists(client, args.submissions_database, args.submissions_overlay_table):
        overlay_union = f"""
        UNION ALL
        SELECT cik, accession_number
        FROM {qi(args.submissions_database)}.{qi(args.submissions_overlay_table)} FINAL
        """
    text = client.execute(
        f"""
WITH authoritative AS
(
    SELECT cik, accession_number
    FROM {qi(args.submissions_database)}.{qi(args.submissions_table)} FINAL
    {overlay_union}
),
exact AS
(
    SELECT cik, accession_number FROM authoritative GROUP BY cik, accession_number
)
SELECT
    d.cik,
    d.accession_number,
    any(ifNull(d.source_archive_path, '')) AS archive_path,
    any(d.source_archive_member) AS member
FROM {qi(args.database)}.sec_filing_document_v3 AS d FINAL
LEFT ANTI JOIN exact AS e
    ON d.cik = e.cik AND d.accession_number = e.accession_number
GROUP BY d.cik, d.accession_number
FORMAT JSONEachRow
"""
    )
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def audit_archive(archive_path: str, wanted: dict[str, dict[str, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "archive_path": archive_path,
        "wanted": len(wanted),
        "matched": 0,
        "mismatched": 0,
        "missing": 0,
        "archive_errors": 0,
        "mismatches": [],
        "error": "",
    }
    found: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                key = member.name.lstrip("./")
                expected = wanted.get(key)
                if expected is None or not member.isfile():
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                parsed = parse_filing(handle.read(), member.name)
                found.add(key)
                if parsed["cik"] == expected["cik"] and parsed["accession_number"] == expected["accession_number"]:
                    result["matched"] += 1
                else:
                    result["mismatched"] += 1
                    result["mismatches"].append(
                        {
                            "member": member.name,
                            "stored_cik": expected["cik"],
                            "sgml_cik": parsed["cik"],
                            "stored_accession": expected["accession_number"],
                            "sgml_accession": parsed["accession_number"],
                        }
                    )
        result["missing"] = len(set(wanted) - found)
    except Exception as exc:  # noqa: BLE001
        result["archive_errors"] = 1
        result["missing"] = len(wanted)
        result["error"] = repr(exc)
    return result


def table_exists(client: ClickHouseHttpClient, database: str, name: str) -> bool:
    return bool(
        int(
            client.execute(
                f"SELECT count() FROM system.tables WHERE database={json.dumps(database)} AND name={json.dumps(name)} FORMAT TSV"
            ).strip()
            or "0"
        )
    )


def qi(value: str) -> str:
    return quote_ident(value)


def validate_identifier(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


if __name__ == "__main__":
    raise SystemExit(main())
