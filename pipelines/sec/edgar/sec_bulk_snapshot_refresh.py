from __future__ import annotations

import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from research.mlops.clickhouse import parse_size_bytes, quote_ident, sql_string, windows_path_to_clickhouse_path


TICKER_SOURCES = {"company_tickers", "company_tickers_exchange", "company_tickers_mf"}
SNAPSHOT_MANIFEST_TABLE = "sec_bulk_mirror_snapshot_manifest_v3"
LEGACY_MEMBER_MANIFEST_TABLE = "sec_bulk_mirror_member_manifest_v3"


def refresh_selected_snapshots(
    client: Any,
    args: Any,
    artifacts: list[Any],
    run_id: str,
    report_path: Path,
    retry: Any,
) -> dict[str, int]:
    del retry
    by_name = {artifact.source_name: artifact for artifact in artifacts}
    totals: dict[str, int] = {}
    if "submissions" in by_name:
        totals["submissions"] = refresh_archive_snapshot(
            client, args, by_name["submissions"], run_id, report_path, "submissions", build_submissions_tables
        )
    if "companyfacts" in by_name:
        totals["companyfacts"] = refresh_archive_snapshot(
            client, args, by_name["companyfacts"], run_id, report_path, "companyfacts", build_companyfacts_tables
        )
    selected_tickers = TICKER_SOURCES.intersection(by_name)
    if selected_tickers:
        if selected_tickers != TICKER_SOURCES:
            missing = sorted(TICKER_SOURCES - selected_tickers)
            raise RuntimeError(f"Ticker mirror replacement requires all ticker snapshots; missing={missing}")
        totals.update(refresh_ticker_snapshot(client, args, [by_name[name] for name in sorted(TICKER_SOURCES)], run_id, report_path))
    if not int(args.limit_ciks) and ({"submissions", "companyfacts"}.intersection(by_name)):
        drop_table(client, args.database, LEGACY_MEMBER_MANIFEST_TABLE)
    return totals


def refresh_archive_snapshot(
    client: Any,
    args: Any,
    artifact: Any,
    run_id: str,
    report_path: Path,
    source_name: str,
    builder: Callable[[Any, Any, Any, str, str], dict[str, int]],
) -> int:
    started = time.perf_counter()
    raw_table = stage_name(f"sec_bulk_mirror_{source_name}_raw", run_id)
    expected_bases = archive_bases(source_name)
    expected_members = count_json_members(artifact.path)
    limit = max(0, int(args.limit_ciks))
    if limit:
        expected_members = min(expected_members, limit)
    record_manifest(client, args.database, artifact, run_id, "loading", expected_members=expected_members)
    create_raw_stage(client, args.database, raw_table, args.storage_policy)
    stage_tables: list[str] = []
    succeeded = False
    try:
        archive_path = archive_clickhouse_path(args, artifact)
        limit_sql = f"LIMIT {limit}" if limit else ""
        execute_stage(
            client,
            source_name,
            "load raw JSON members",
            f"""
            INSERT INTO {table(args.database, raw_table)} (member_name, raw_json)
            SELECT _file, json
            FROM file({sql_string(archive_path)}, 'JSONAsString')
            {limit_sql}
            SETTINGS {query_settings(args)}
            """,
        )
        raw_rows = scalar_int(client, f"SELECT count() FROM {table(args.database, raw_table)}")
        invalid_json = scalar_int(client, f"SELECT countIf(NOT isValidJSON(raw_json)) FROM {table(args.database, raw_table)}")
        if raw_rows != expected_members or invalid_json:
            raise RuntimeError(
                f"{source_name} raw staging validation failed: rows={raw_rows:,} expected={expected_members:,} invalid_json={invalid_json:,}"
            )
        counts = builder(client, args, artifact, run_id, raw_table)
        stage_tables = list(counts)
        if limit:
            print(f"snapshot_debug_no_cutover source={source_name} limit_members={limit:,} staged_rows={sum(counts.values()):,}", flush=True)
            record_manifest(
                client,
                args.database,
                artifact,
                run_id,
                "validated_debug",
                expected_members=expected_members,
                staged_rows=sum(counts.values()),
            )
        else:
            validate_and_cut_over(
                client,
                args,
                stage_tables,
                counts,
                run_id=run_id,
                on_active=lambda: record_manifest(
                    client,
                    args.database,
                    artifact,
                    run_id,
                    "active",
                    expected_members=expected_members,
                    staged_rows=sum(counts.values()),
                    active_rows=sum(counts.values()),
                ),
            )
        row = {
            "run_id": run_id,
            "source": source_name,
            "source_file_id": artifact.source_file_id,
            "inserted_rows": sum(counts.values()),
            "member_rows": raw_rows,
            "cutover": not bool(limit),
            "wall_seconds": round(time.perf_counter() - started, 3),
            "status": "ok",
        }
        append_report(report_path, row)
        print(json.dumps(row, sort_keys=True), flush=True)
        succeeded = True
        return sum(counts.values())
    except Exception as exc:
        try:
            record_manifest(client, args.database, artifact, run_id, "failed", expected_members=expected_members, error=summarize_error(exc))
        except Exception as manifest_exc:  # noqa: BLE001
            print(f"snapshot_failure_manifest_error source={source_name} error={summarize_error(manifest_exc)}", flush=True)
        append_report(report_path, {"run_id": run_id, "source": source_name, "status": "failed", "error": repr(exc)})
        if not args.keep_failed_staging:
            for base in expected_bases:
                safe_drop_table(client, args.database, stage_name(base, run_id))
        raise
    finally:
        if succeeded or not args.keep_failed_staging:
            safe_drop_table(client, args.database, raw_table)
            if limit:
                for base in stage_tables:
                    safe_drop_table(client, args.database, stage_name(base, run_id))


def build_submissions_tables(client: Any, args: Any, artifact: Any, run_id: str, raw_table: str) -> dict[str, int]:
    bases = [
        "sec_bulk_mirror_company_v3",
        "sec_bulk_mirror_submission_file_ref_v3",
        "sec_bulk_mirror_filing_v3",
    ]
    create_replacement_tables(client, args.database, bases, run_id, args.storage_policy)
    raw = table(args.database, raw_table)
    company_stage, file_stage, filing_stage = (table(args.database, stage_name(base, run_id)) for base in bases)
    now = now_sql()
    cik = cik_sql("raw_json", "member_name")
    is_fragment = "positionCaseInsensitive(member_name, '-submissions-') > 0"

    execute_stage(
        client,
        "submissions",
        "build companies",
        f"""
        INSERT INTO {company_stage}
        SELECT
            {cik} AS cik,
            JSONExtractString(raw_json, 'name') AS entity_name,
            nullIf(JSONExtractString(raw_json, 'sic'), '') AS sic,
            nullIf(JSONExtractString(raw_json, 'sicDescription'), '') AS sic_description,
            nullIf(JSONExtractString(raw_json, 'ein'), '') AS ein,
            nullIf(JSONExtractString(raw_json, 'category'), '') AS category,
            nullIf(JSONExtractString(raw_json, 'fiscalYearEnd'), '') AS fiscal_year_end,
            nullIf(JSONExtractString(raw_json, 'stateOfIncorporation'), '') AS state_of_incorporation,
            if(empty(JSONExtractRaw(raw_json, 'addresses')), '{{}}', JSONExtractRaw(raw_json, 'addresses')) AS addresses_json,
            if(empty(JSONExtractRaw(raw_json, 'formerNames')), '[]', JSONExtractRaw(raw_json, 'formerNames')) AS former_names_json,
            {sql_string(artifact.source_file_id)} AS source_file_id,
            {now} AS last_seen_at_utc
        FROM {raw}
        WHERE NOT ({is_fragment}) AND {cik} != ''
        SETTINGS {query_settings(args)}
        """,
    )

    execute_stage(
        client,
        "submissions",
        "build file references",
        f"""
        INSERT INTO {file_stage}
        WITH {cik} AS cik
        SELECT
            hex(SHA256(concat(cik, '|', file.1))) AS file_ref_id,
            cik,
            file.1 AS file_name,
            file.2 AS filing_count,
            toDateOrNull(nullIf(file.3, '')) AS filing_from,
            toDateOrNull(nullIf(file.4, '')) AS filing_to,
            {sql_string(artifact.source_file_id)} AS source_file_id,
            {now} AS last_seen_at_utc
        FROM {raw}
        ARRAY JOIN JSONExtract(
            raw_json,
            'filings',
            'files',
            'Array(Tuple(name String, filingCount UInt64, filingFrom String, filingTo String))'
        ) AS file
        WHERE NOT ({is_fragment}) AND file.1 != ''
        SETTINGS {query_settings(args)}
        """,
    )

    filing_payload_type = (
        "Tuple(accessionNumber Array(String), filingDate Array(String), reportDate Array(String), "
        "acceptanceDateTime Array(String), form Array(String), primaryDocument Array(String), "
        "size Array(String), items Array(String), act Array(String), fileNumber Array(String), filmNumber Array(String))"
    )
    filings_sql = f"""
        INSERT INTO {filing_stage}
        WITH
            {cik} AS cik,
            if(
                {is_fragment},
                JSONExtract(raw_json, {sql_string(filing_payload_type)}),
                JSONExtract(raw_json, 'filings', 'recent', {sql_string(filing_payload_type)})
            ) AS filing_payload,
            filing_payload.1 AS accessions,
            arrayElement(accessions, filing_index) AS accession_number,
            replaceAll(accession_number, '-', '') AS accession_number_compact,
            arrayElement(filing_payload.4, filing_index) AS acceptance_datetime_raw_value,
            arrayElement(filing_payload.6, filing_index) AS primary_document_value,
            arrayElement(filing_payload.2, filing_index) AS filing_date_value,
            arrayElement(filing_payload.3, filing_index) AS report_date_value,
            arrayElement(filing_payload.5, filing_index) AS form_value,
            arrayElement(filing_payload.7, filing_index) AS size_value,
            arrayElement(filing_payload.8, filing_index) AS items_value,
            arrayElement(filing_payload.9, filing_index) AS act_value,
            arrayElement(filing_payload.10, filing_index) AS file_number_value,
            arrayElement(filing_payload.11, filing_index) AS film_number_value
        SELECT
            accession_number,
            accession_number_compact,
            cik,
            JSONExtractString(raw_json, 'name') AS company_name,
            form_value AS form_type,
            toDateOrNull(nullIf(filing_date_value, '')) AS filing_date,
            toDateOrNull(nullIf(report_date_value, '')) AS report_date,
            if(
                endsWith(acceptance_datetime_raw_value, 'Z'),
                parseDateTime64BestEffortOrNull(acceptance_datetime_raw_value, 9, 'UTC'),
                CAST(NULL AS Nullable(DateTime64(9, 'UTC')))
            ) AS accepted_at_utc,
            nullIf(acceptance_datetime_raw_value, '') AS acceptance_datetime_raw,
            if(empty(acceptance_datetime_raw_value), 'missing', if({is_fragment}, 'submissions_bulk_fragment', 'submissions_bulk')) AS accepted_at_source,
            nullIf(primary_document_value, '') AS primary_document,
            if(
                empty(primary_document_value),
                CAST(NULL, 'Nullable(String)'),
                concat('https://www.sec.gov/Archives/edgar/data/', toString(toUInt64OrZero(cik)), '/', accession_number_compact, '/', primary_document_value)
            ) AS primary_document_url,
            concat('https://www.sec.gov/Archives/edgar/data/', toString(toUInt64OrZero(cik)), '/', accession_number_compact, '/') AS filing_detail_url,
            CAST(NULL, 'Nullable(UInt16)') AS document_count,
            toUInt64OrNull(nullIf(size_value, '')) AS filing_size,
            nullIf(items_value, '') AS items,
            nullIf(act_value, '') AS act,
            nullIf(file_number_value, '') AS file_number,
            nullIf(film_number_value, '') AS film_number,
            if({is_fragment}, 'submissions_bulk_fragment', 'submissions_bulk') AS source_kind,
            {sql_string(artifact.source_file_id)} AS source_file_id,
            toJSONString(map(
                'accessionNumber', accession_number,
                'filingDate', filing_date_value,
                'reportDate', report_date_value,
                'acceptanceDateTime', acceptance_datetime_raw_value,
                'form', form_value,
                'primaryDocument', primary_document_value,
                'size', size_value,
                'items', items_value,
                'act', act_value,
                'fileNumber', file_number_value,
                'filmNumber', film_number_value
            )) AS raw_submission_json,
            {now} AS last_seen_at_utc
        FROM {raw}
        ARRAY JOIN arrayEnumerate(accessions) AS filing_index
        WHERE accession_number != '' AND cik != ''
        SETTINGS max_partitions_per_insert_block = 1000, {query_settings(args)}
    """
    execute_stage(client, "submissions", "build filings", filings_sql)

    validate_submission_stage(client, args, bases, run_id)
    return {base: scalar_int(client, f"SELECT count() FROM {table(args.database, stage_name(base, run_id))} FINAL") for base in bases}


def build_companyfacts_tables(client: Any, args: Any, artifact: Any, run_id: str, raw_table: str) -> dict[str, int]:
    base = "sec_bulk_mirror_xbrl_fact_v3"
    create_replacement_tables(client, args.database, [base], run_id, args.storage_policy)
    stage = table(args.database, stage_name(base, run_id))
    raw = table(args.database, raw_table)
    now = now_sql()
    cik = cik_sql("raw_json", "member_name")
    fact_type = (
        "Tuple(val Nullable(Float64), accn Nullable(String), fy Nullable(UInt16), fp Nullable(String), "
        "form Nullable(String), filed Nullable(String), frame Nullable(String), start Nullable(String), end Nullable(String))"
    )
    execute_stage(
        client,
        "companyfacts",
        "build XBRL facts",
        f"""
        INSERT INTO {stage}
        WITH
            {cik} AS cik,
            taxonomy_pair.1 AS taxonomy,
            taxonomy_pair.2 AS taxonomy_json,
            tag_pair.1 AS tag,
            tag_pair.2 AS tag_json,
            unit_pair.1 AS unit,
            nullIf(ifNull(fact.2, ''), '') AS accession_number,
            toDateOrNull(nullIf(ifNull(fact.8, ''), '')) AS start_date,
            toDateOrNull(nullIf(ifNull(fact.9, ''), '')) AS end_date,
            nullIf(ifNull(fact.7, ''), '') AS frame,
            '{{}}' AS dimensions_json
        SELECT
            hex(SHA256(toJSONString(tuple(cik, taxonomy, tag, unit, start_date, end_date, accession_number, frame, map())))) AS fact_id,
            cik,
            JSONExtractString(raw_json, 'entityName') AS entity_name,
            taxonomy,
            tag,
            JSONExtractString(tag_json, 'label') AS label,
            JSONExtractString(tag_json, 'description') AS description,
            unit,
            fact.1 AS value,
            start_date,
            end_date,
            toDateOrNull(nullIf(ifNull(fact.6, ''), '')) AS filed_date,
            fact.3 AS fy,
            nullIf(ifNull(fact.4, ''), '') AS fp,
            nullIf(ifNull(fact.5, ''), '') AS form_type,
            frame,
            accession_number,
            dimensions_json,
            {sql_string(artifact.source_file_id)} AS source_file_id,
            {now} AS last_seen_at_utc
        FROM {raw}
        ARRAY JOIN JSONExtractKeysAndValuesRaw(JSONExtractRaw(raw_json, 'facts')) AS taxonomy_pair
        ARRAY JOIN JSONExtractKeysAndValuesRaw(taxonomy_json) AS tag_pair
        ARRAY JOIN JSONExtractKeysAndValuesRaw(JSONExtractRaw(tag_json, 'units')) AS unit_pair
        ARRAY JOIN JSONExtract(unit_pair.2, {sql_string(f'Array({fact_type})')}) AS fact
        WHERE cik != '' AND tag != '' AND unit != ''
        SETTINGS max_partitions_per_insert_block = 1000, {query_settings(args)}
        """,
    )
    missing = scalar_int(client, f"SELECT countIf(cik = '' OR tag = '' OR unit = '') FROM {stage}")
    if missing:
        raise RuntimeError(f"companyfacts normalized staging contains {missing:,} rows missing required identity")
    rows, unique_ids = tsv_ints(client.execute(f"SELECT count(), uniqExact(fact_id) FROM {stage} FORMAT TSV"))
    if rows != unique_ids:
        raise RuntimeError(f"companyfacts fact identity collision: rows={rows:,} unique_fact_ids={unique_ids:,}")
    return {base: rows}


def refresh_ticker_snapshot(client: Any, args: Any, artifacts: list[Any], run_id: str, report_path: Path) -> dict[str, int]:
    base = "sec_bulk_mirror_company_ticker_v3"
    create_replacement_tables(client, args.database, [base], run_id, args.storage_policy)
    stage = table(args.database, stage_name(base, run_id))
    now = now_sql()
    try:
        for artifact in artifacts:
            record_manifest(client, args.database, artifact, run_id, "loading")
            source_path = clickhouse_path(args, artifact.path)
            if artifact.source_name == "company_tickers":
                source_sql = ticker_object_select_sql(stage, artifact, source_path, now, args)
            elif artifact.source_name == "company_tickers_exchange":
                source_sql = ticker_array_select_sql(stage, artifact, source_path, now, args, is_fund=False)
            else:
                source_sql = ticker_array_select_sql(stage, artifact, source_path, now, args, is_fund=True)
            execute_stage(client, "tickers", f"load {artifact.source_name}", source_sql)
        count = scalar_int(client, f"SELECT count() FROM {stage} FINAL")
        if not count:
            raise RuntimeError("ticker snapshot produced zero rows")
        source_counts = {
            artifact.source_name: scalar_int(
                client,
                f"SELECT count() FROM {stage} FINAL WHERE mapping_source = {sql_string(artifact.source_kind)}",
            )
            for artifact in artifacts
        }
        if any(value == 0 for value in source_counts.values()):
            raise RuntimeError(f"ticker snapshot has an empty source: {source_counts}")
        validate_and_cut_over(
            client,
            args,
            [base],
            {base: count},
            run_id=run_id,
            on_active=lambda: [
                record_manifest(
                    client,
                    args.database,
                    artifact,
                    run_id,
                    "active",
                    staged_rows=source_counts[artifact.source_name],
                    active_rows=source_counts[artifact.source_name],
                )
                for artifact in artifacts
            ],
        )
        row = {"run_id": run_id, "source": "ticker_mappings", "inserted_rows": count, "status": "ok"}
        append_report(report_path, row)
        print(json.dumps(row, sort_keys=True), flush=True)
        return source_counts
    except Exception as exc:
        for artifact in artifacts:
            try:
                record_manifest(client, args.database, artifact, run_id, "failed", error=summarize_error(exc))
            except Exception as manifest_exc:  # noqa: BLE001
                print(
                    f"snapshot_failure_manifest_error source={artifact.source_name} error={summarize_error(manifest_exc)}",
                    flush=True,
                )
        if not args.keep_failed_staging:
            safe_drop_table(client, args.database, stage_name(base, run_id))
        raise


def ticker_object_select_sql(stage: str, artifact: Any, source_path: str, now: str, args: Any) -> str:
    return f"""
        INSERT INTO {stage}
        WITH item.2 AS row_json, leftPad(toString(JSONExtractUInt(row_json, 'cik_str')), 10, '0') AS cik,
             upper(JSONExtractString(row_json, 'ticker')) AS ticker,
             '' AS exchange, '' AS series_id, '' AS class_id
        SELECT
            hex(SHA256(concat({sql_string(artifact.source_name)}, '|', cik, '|', ticker, '|', exchange, '|', series_id, '|', class_id))),
            cik, ticker, nullIf(exchange, ''), JSONExtractString(row_json, 'title'), {sql_string(artifact.source_kind)},
            nullIf(series_id, ''), nullIf(class_id, ''), {now}, {now}, 1, {sql_string(artifact.source_file_id)}
        FROM file({sql_string(source_path)}, 'JSONAsString')
        ARRAY JOIN JSONExtractKeysAndValuesRaw(json) AS item
        WHERE cik != '' AND ticker != ''
        SETTINGS {query_settings(args)}
    """


def ticker_array_select_sql(stage: str, artifact: Any, source_path: str, now: str, args: Any, *, is_fund: bool) -> str:
    if is_fund:
        name = "''"
        ticker = "upper(JSONExtractString(row_json, 4))"
        exchange = "''"
        series_id = "JSONExtractString(row_json, 2)"
        class_id = "JSONExtractString(row_json, 3)"
    else:
        name = "JSONExtractString(row_json, 2)"
        ticker = "upper(JSONExtractString(row_json, 3))"
        exchange = "JSONExtractString(row_json, 4)"
        series_id = "''"
        class_id = "''"
    return f"""
        INSERT INTO {stage}
        WITH
            leftPad(toString(JSONExtractUInt(row_json, 1)), 10, '0') AS cik,
            {ticker} AS ticker,
            {exchange} AS exchange,
            {series_id} AS series_id,
            {class_id} AS class_id
        SELECT
            hex(SHA256(concat({sql_string(artifact.source_name)}, '|', cik, '|', ticker, '|', exchange, '|', series_id, '|', class_id))),
            cik, ticker, nullIf(exchange, ''), {name}, {sql_string(artifact.source_kind)},
            nullIf(series_id, ''), nullIf(class_id, ''), {now}, {now}, 1, {sql_string(artifact.source_file_id)}
        FROM file({sql_string(source_path)}, 'JSONAsString')
        ARRAY JOIN JSONExtractArrayRaw(json, 'data') AS row_json
        WHERE cik != '' AND ticker != ''
        SETTINGS {query_settings(args)}
    """


def validate_submission_stage(client: Any, args: Any, bases: list[str], run_id: str) -> None:
    filing = table(args.database, stage_name("sec_bulk_mirror_filing_v3", run_id))
    metrics = tsv_ints(
        client.execute(
            f"""
            SELECT
                count(),
                uniqExact((cik, accession_number)),
                countIf(cik = '' OR accession_number = ''),
                countIf(notEmpty(ifNull(acceptance_datetime_raw, '')) AND NOT endsWith(acceptance_datetime_raw, 'Z')),
                countIf(endsWith(ifNull(acceptance_datetime_raw, ''), 'Z') AND accepted_at_utc IS NULL)
            FROM {filing}
            FORMAT TSV
            """
        )
    )
    rows, unique_keys, missing_keys, non_utc_raw, unparsed_utc = metrics
    if rows != unique_keys or missing_keys or non_utc_raw or unparsed_utc:
        raise RuntimeError(
            "submission filing staging validation failed: "
            f"rows={rows:,} unique_keys={unique_keys:,} missing_keys={missing_keys:,} "
            f"non_utc_raw={non_utc_raw:,} unparsed_utc={unparsed_utc:,}"
        )
    required_bases = ["sec_bulk_mirror_filing_v3"] if int(args.limit_ciks) else bases
    for base in required_bases:
        if scalar_int(client, f"SELECT count() FROM {table(args.database, stage_name(base, run_id))} FINAL") == 0:
            raise RuntimeError(f"submission staging table is empty: {base}")


def validate_and_cut_over(
    client: Any,
    args: Any,
    bases: list[str],
    staged_counts: dict[str, int],
    *,
    run_id: str,
    on_active: Callable[[], Any] | None = None,
) -> None:
    for base in bases:
        active = table(args.database, base)
        active_count = scalar_int(client, f"SELECT count() FROM {active} FINAL")
        staged = staged_counts[base]
        if active_count and staged < int(active_count * float(args.minimum_row_ratio)):
            raise RuntimeError(
                f"snapshot regression blocked for {base}: staged={staged:,} active={active_count:,} minimum_ratio={args.minimum_row_ratio}"
            )
        if base == "sec_bulk_mirror_filing_v3" and active_count:
            active_max = client.execute(f"SELECT toString(max(filing_date)) FROM {active}").strip()
            staged_max = client.execute(f"SELECT toString(max(filing_date)) FROM {table(args.database, stage_name(base, run_id))}").strip()
            if staged_max < active_max:
                raise RuntimeError(f"filing snapshot date regression blocked: staged_max={staged_max} active_max={active_max}")

    exchanged: list[str] = []
    try:
        for base in bases:
            client.execute(f"EXCHANGE TABLES {table(args.database, base)} AND {table(args.database, stage_name(base, run_id))}")
            exchanged.append(base)
        for base in bases:
            actual = scalar_int(client, f"SELECT count() FROM {table(args.database, base)} FINAL")
            if actual != staged_counts[base]:
                raise RuntimeError(f"post-cutover count mismatch for {base}: actual={actual:,} expected={staged_counts[base]:,}")
        if on_active is not None:
            on_active()
    except Exception:
        for base in reversed(exchanged):
            client.execute(f"EXCHANGE TABLES {table(args.database, base)} AND {table(args.database, stage_name(base, run_id))}")
        raise
    for base in bases:
        drop_table(client, args.database, stage_name(base, run_id))


def create_replacement_tables(client: Any, database: str, bases: list[str], run_id: str, storage_policy: str) -> None:
    for base in bases:
        stage = stage_name(base, run_id)
        drop_table(client, database, stage)
        if base == "sec_bulk_mirror_xbrl_fact_v3":
            settings = "index_granularity = 8192"
            if storage_policy:
                settings += f", storage_policy = {sql_string(storage_policy)}"
            client.execute(
                f"CREATE TABLE {table(database, stage)} AS {table(database, base)} "
                "ENGINE = ReplacingMergeTree(last_seen_at_utc) "
                "PARTITION BY toYYYYMM(ifNull(end_date, toDate('1970-01-01'))) "
                "ORDER BY (cik, taxonomy, tag, unit, ifNull(end_date, toDate('1970-01-01')), "
                f"ifNull(accession_number, ''), fact_id) SETTINGS {settings}"
            )
        else:
            client.execute(f"CREATE TABLE {table(database, stage)} AS {table(database, base)}")


def create_raw_stage(client: Any, database: str, raw_table: str, storage_policy: str) -> None:
    settings = "index_granularity = 8192"
    if storage_policy:
        settings += f", storage_policy = {sql_string(storage_policy)}"
    client.execute(
        f"""
        CREATE TABLE {table(database, raw_table)}
        (
            member_name String,
            raw_json String
        )
        ENGINE = MergeTree
        ORDER BY member_name
        SETTINGS {settings}
        """
    )


def record_manifest(
    client: Any,
    database: str,
    artifact: Any,
    run_id: str,
    status: str,
    *,
    expected_members: int = 0,
    staged_rows: int = 0,
    active_rows: int = 0,
    error: str = "",
) -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    row = {
        "source_name": artifact.source_name,
        "source_kind": artifact.source_kind,
        "source_file_id": artifact.source_file_id,
        "run_id": run_id,
        "sha256": artifact.sha256,
        "byte_size": artifact.byte_size,
        "expected_members": expected_members,
        "staged_rows": staged_rows,
        "active_rows": active_rows,
        "status": status,
        "processed_at_utc": now,
        "error": error,
    }
    client.execute(
        f"INSERT INTO {table(database, SNAPSHOT_MANIFEST_TABLE)} FORMAT JSONEachRow\n"
        + json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    )


def execute_stage(client: Any, source: str, stage: str, sql: str) -> None:
    started = time.perf_counter()
    print(f"snapshot source={source} stage={stage} status=active", flush=True)
    client.execute(sql)
    print(f"snapshot source={source} stage={stage} status=completed wall_seconds={time.perf_counter() - started:.1f}", flush=True)


def count_json_members(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        return sum(1 for info in archive.infolist() if not info.is_dir() and info.filename.lower().endswith(".json"))


def archive_bases(source_name: str) -> list[str]:
    if source_name == "submissions":
        return [
            "sec_bulk_mirror_company_v3",
            "sec_bulk_mirror_submission_file_ref_v3",
            "sec_bulk_mirror_filing_v3",
        ]
    if source_name == "companyfacts":
        return ["sec_bulk_mirror_xbrl_fact_v3"]
    raise ValueError(f"Unsupported archive snapshot: {source_name}")


def archive_clickhouse_path(args: Any, artifact: Any) -> str:
    return f"{clickhouse_path(args, artifact.path)} :: *.json"


def clickhouse_path(args: Any, path: Path) -> str:
    windows_root = Path(args.artifact_root_win).parent
    return windows_path_to_clickhouse_path(path, windows_root, args.clickhouse_file_root)


def query_settings(args: Any) -> str:
    return f"max_threads = {max(1, int(args.max_threads))}, max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}"


def cik_sql(json_column: str, member_column: str) -> str:
    return (
        f"if(JSONExtractUInt({json_column}, 'cik') > 0, "
        f"leftPad(toString(JSONExtractUInt({json_column}, 'cik')), 10, '0'), "
        f"extract({member_column}, 'CIK([0-9]{{10}})'))"
    )


def now_sql() -> str:
    return "now64(9, 'UTC')"


def stage_name(base: str, run_id: str) -> str:
    return f"{base}__stage_{run_id}"


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"


def scalar_int(client: Any, sql: str) -> int:
    return int(client.execute(sql + "\nFORMAT TSV").strip() or "0")


def tsv_ints(text: str) -> list[int]:
    return [int(value) for value in text.strip().split("\t")]


def drop_table(client: Any, database: str, name: str) -> None:
    client.execute(f"DROP TABLE IF EXISTS {table(database, name)} SYNC")


def safe_drop_table(client: Any, database: str, name: str) -> None:
    try:
        drop_table(client, database, name)
    except Exception as exc:  # noqa: BLE001
        print(f"snapshot_cleanup_error table={database}.{name} error={summarize_error(exc)}", flush=True)


def append_report(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def summarize_error(exc: BaseException) -> str:
    return repr(exc).replace("\n", " ")[:2000]
