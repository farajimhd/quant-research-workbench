from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.core.url_policy import (  # noqa: E402
    POLICY_TABLE_COLUMNS,
    create_policy_table_sql,
    load_policy,
    policy_counts,
    policy_to_entries,
)
from research.mlops.clickhouse import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_PASSWORD_ENV,
    CLICKHOUSE_PASSWORD_SIMPLE_ENV,
    CLICKHOUSE_USER_ENV,
    CLICKHOUSE_USER_SIMPLE_ENV,
    CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
    CLICKHOUSE_WORKSTATION_USER_ENV,
    DEFAULT_CLICKHOUSE_URL,
    ClickHouseHttpClient,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_DATABASE = "q_live"
DEFAULT_TABLE = "news_url_policy_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create, seed, and audit the compact news URL policy table.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("NEWS_URL_POLICY_DATABASE") or DEFAULT_DATABASE)
    parser.add_argument("--table", default=os.environ.get("NEWS_URL_POLICY_TABLE") or DEFAULT_TABLE)
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or os.environ.get("CLICKHOUSE_STORAGE_POLICY") or "")
    parser.add_argument("--policy-json", default=os.environ.get("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON") or "")
    parser.add_argument("--execute", action="store_true", help="Create/seed table. Without this, only print the plan.")
    parser.add_argument("--seed-default", action="store_true", help="Insert default policy rows.")
    parser.add_argument("--rebuild", action="store_true", help="Truncate the policy table before seeding.")
    parser.add_argument("--audit-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    policy = load_policy(args.policy_json)
    entries = policy_to_entries(policy, source="file" if args.policy_json else "default")
    print("=" * 96, flush=True)
    print("Benzinga news URL policy table", flush=True)
    print(f"clickhouse_url={args.clickhouse_url}", flush=True)
    print(f"target={args.database}.{args.table}", flush=True)
    print(f"policy_version={policy.get('version')}", flush=True)
    print(f"execute={args.execute} seed_default={args.seed_default} rebuild={args.rebuild} audit_only={args.audit_only}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(secret_keys()), sort_keys=True), flush=True)
    print("default_policy_counts=" + json.dumps(policy_counts(entries), sort_keys=True), flush=True)
    print("=" * 96, flush=True)

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    if args.audit_only:
        audit(client, args)
        return
    if not args.execute:
        print(create_policy_table_sql(args.database, args.table, storage_policy=args.storage_policy), flush=True)
        print("dry_run=1; pass --execute --seed-default to create and seed", flush=True)
        return

    client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(args.database)}")
    client.execute(create_policy_table_sql(args.database, args.table, storage_policy=args.storage_policy))
    if args.rebuild:
        client.execute(f"TRUNCATE TABLE {quote_ident(args.database)}.{quote_ident(args.table)}")
    if args.seed_default:
        insert_entries(client, args, entries)
    audit(client, args)


def insert_entries(client: ClickHouseHttpClient, args: argparse.Namespace, entries: list[Any]) -> None:
    rows = "\n".join(json.dumps(asdict(entry), ensure_ascii=False, separators=(",", ":")) for entry in entries)
    if not rows:
        return
    client.execute(f"INSERT INTO {quote_ident(args.database)}.{quote_ident(args.table)} ({', '.join(quote_ident(c) for c in POLICY_TABLE_COLUMNS)}) FORMAT JSONEachRow\n{rows}")
    print(f"seeded_rows={len(entries):,}", flush=True)


def audit(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    try:
        text = client.execute(
            f"""
SELECT
    policy_version,
    match_type,
    action,
    enabled,
    count() AS rows
FROM {quote_ident(args.database)}.{quote_ident(args.table)} FINAL
GROUP BY policy_version, match_type, action, enabled
ORDER BY policy_version, match_type, action, enabled
FORMAT JSONEachRow
"""
        )
    except Exception as exc:  # noqa: BLE001
        print(f"audit_status=missing_or_failed exception={exc!r}", flush=True)
        return
    print("audit_rows:", flush=True)
    for line in text.splitlines():
        if line.strip():
            print(line, flush=True)


def default_clickhouse_url() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL")
        or os.environ.get("QMD_CLICKHOUSE_URL")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or DEFAULT_CLICKHOUSE_URL
    )


def default_clickhouse_user() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER")
        or os.environ.get("QMD_CLICKHOUSE_USER")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER")
        or os.environ.get(CLICKHOUSE_WORKSTATION_USER_ENV)
        or os.environ.get(CLICKHOUSE_USER_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_USER_ENV)
        or "default"
    )


def default_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or os.environ.get(CLICKHOUSE_WORKSTATION_PASSWORD_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_SIMPLE_ENV)
        or os.environ.get(CLICKHOUSE_PASSWORD_ENV)
        or ""
    )


def secret_keys() -> list[str]:
    return [
        "QLIVE_MIGRATION_CLICKHOUSE_URL",
        "QLIVE_MIGRATION_CLICKHOUSE_USER",
        "QLIVE_MIGRATION_CLICKHOUSE_PASSWORD",
        "QMD_CLICKHOUSE_URL",
        "QMD_CLICKHOUSE_USER",
        "QMD_CLICKHOUSE_PASSWORD",
        "REAL_LIVE_CLICKHOUSE_WRITE_URL",
        "REAL_LIVE_CLICKHOUSE_WRITE_USER",
        "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
        "CLICKHOUSE_LIVE_STORAGE_POLICY",
    ]


if __name__ == "__main__":
    main()
