from __future__ import annotations

import re


TEXT_SOURCE_PARTITION_KEY = "toYYYYMM(source_archive_date)"
TEXT_SOURCE_SORTING_KEY = "cik, accession_number, document_id, content_format"
TEXT_SOURCE_PARTITION_PLACEHOLDER = "{{SEC_TEXT_SOURCE_PARTITION_KEY}}"
TEXT_SOURCE_SORTING_PLACEHOLDER = "{{SEC_TEXT_SOURCE_SORTING_KEY}}"


def normalized_clickhouse_key(value: str) -> str:
    return re.sub(r"[\s`()]+", "", value).lower()


def replacing_merge_tree_version_matches(engine_full: str, version_column: str) -> bool:
    """Compare the version column in full ClickHouse system.tables metadata."""
    normalized = re.sub(r"\s+", "", str(engine_full or "")).lower()
    match = re.match(r"^replacingmergetree\(([^()]*)\)", normalized)
    if match is None:
        return False
    return normalized_clickhouse_key(match.group(1)) == normalized_clickhouse_key(version_column)


def text_source_layout_matches(partition_key: str, sorting_key: str) -> bool:
    return (
        normalized_clickhouse_key(partition_key) == normalized_clickhouse_key(TEXT_SOURCE_PARTITION_KEY)
        and normalized_clickhouse_key(sorting_key) == normalized_clickhouse_key(TEXT_SOURCE_SORTING_KEY)
    )
