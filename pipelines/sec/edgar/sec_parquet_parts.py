from __future__ import annotations

import gzip
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq


DEFAULT_ROW_GROUP_BYTES = 256 * 1024**2
DEFAULT_FILE_BYTES = 1024 * 1024**2
MAX_FILES_PER_ARCHIVE_DATASET = 99
WIDE_TEXT_COLUMNS = {"source_text", "text"}
DICTIONARY_COLUMNS = {
    "accepted_at_source",
    "content_format",
    "document_role",
    "document_type",
    "extraction_method",
    "extraction_status",
    "file_extension",
    "form_type",
    "mime_type",
    "normalizer_version",
    "skip_reason",
    "text_kind",
    "text_status",
}
UINT16_COLUMNS = {"source_section_ordinal"}
UINT32_COLUMNS = {"sequence_number", "document_count", "public_document_count"}
UINT64_COLUMNS = {
    "byte_size",
    "correction_order_key",
    "filing_size",
    "payload_char_count",
    "source_revision_rank",
    "source_text_byte_count",
    "source_text_char_count",
    "text_byte_count",
    "text_char_count",
}
UINT8_COLUMNS = {"document_deleted", "filing_deleted", "has_normalized_text", "private_to_public"}
DATE_COLUMNS = {"date_as_of_change", "filing_date", "report_date", "source_archive_date"}
TIMESTAMP_NS_COLUMNS = {"accepted_at_utc"}
TIMESTAMP_MS_COLUMNS = {"extracted_at_utc", "inserted_at", "source_revision_at"}
LIST_STRING_COLUMNS = {"entity_ciks", "quality_flags"}


class ParquetShardWriter:
    def __init__(
        self,
        *,
        dataset_name: str,
        target_table: str,
        output_directory: Path,
        filename_prefix: str,
        columns: list[str],
        archive_index: int,
        row_group_bytes: int = DEFAULT_ROW_GROUP_BYTES,
        file_bytes: int = DEFAULT_FILE_BYTES,
        compression_level: int = 1,
    ) -> None:
        if row_group_bytes < 1:
            raise ValueError("row_group_bytes must be positive")
        if file_bytes < row_group_bytes:
            raise ValueError("file_bytes must be at least row_group_bytes")
        self.dataset_name = dataset_name
        self.target_table = target_table
        self.output_directory = output_directory
        self.filename_prefix = filename_prefix
        self.columns = list(columns)
        self.archive_index = int(archive_index)
        self.row_group_bytes = int(row_group_bytes)
        self.file_bytes = int(file_bytes)
        self.compression_level = int(compression_level)
        self.schema = schema_for_columns(self.columns)
        self._rows: list[dict[str, Any]] = []
        self._row_bytes = 0
        self._writer: pq.ParquetWriter | None = None
        self._temporary_path: Path | None = None
        self._final_path: Path | None = None
        self._file_index = 0
        self._file_rows = 0
        self._file_logical_bytes = 0
        self._completed: list[dict[str, Any]] = []
        self.output_directory.mkdir(parents=True, exist_ok=True)

    def append(self, row: dict[str, Any]) -> None:
        estimated_bytes = estimate_row_bytes(row, self.columns)
        if self._rows and self._row_bytes + estimated_bytes > self.row_group_bytes:
            self._flush_row_group()
        self._rows.append(row)
        self._row_bytes += estimated_bytes
        if self._row_bytes >= self.row_group_bytes:
            self._flush_row_group()

    def close(self) -> list[dict[str, Any]]:
        self._flush_row_group()
        self._close_file()
        return list(self._completed)

    def abort(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        if self._temporary_path is not None:
            self._temporary_path.unlink(missing_ok=True)
        for item in self._completed:
            Path(item["path"]).unlink(missing_ok=True)
        self._completed.clear()

    def _flush_row_group(self) -> None:
        if not self._rows:
            return
        logical_bytes = self._row_bytes
        if self._writer is not None and self._file_rows and self._file_logical_bytes + logical_bytes > self.file_bytes:
            self._close_file()
        if self._writer is None:
            self._open_file()
        rows = [coerce_row(row, self.columns) for row in self._rows]
        table = pa.Table.from_pylist(rows, schema=self.schema)
        assert self._writer is not None
        self._writer.write_table(table, row_group_size=max(1, table.num_rows))
        self._file_rows += table.num_rows
        self._file_logical_bytes += logical_bytes
        self._rows.clear()
        self._row_bytes = 0

    def _open_file(self) -> None:
        self._file_index += 1
        if self._file_index > MAX_FILES_PER_ARCHIVE_DATASET:
            raise RuntimeError(
                f"too many Parquet shards for archive dataset={self.dataset_name}; "
                f"limit={MAX_FILES_PER_ARCHIVE_DATASET}"
            )
        name = f"{self.filename_prefix}_{self._file_index:02d}.parquet"
        self._final_path = self.output_directory / name
        self._temporary_path = self._final_path.with_suffix(".parquet.tmp")
        self._temporary_path.unlink(missing_ok=True)
        dictionary_columns = sorted(DICTIONARY_COLUMNS.intersection(self.columns))
        statistics_columns = [column for column in self.columns if column not in WIDE_TEXT_COLUMNS]
        self._writer = pq.ParquetWriter(
            self._temporary_path,
            self.schema,
            version="2.6",
            compression="zstd",
            compression_level=self.compression_level,
            use_dictionary=dictionary_columns,
            write_statistics=statistics_columns,
            data_page_size=1024**2,
            write_page_index=True,
            write_page_checksum=True,
        )
        self._file_rows = 0
        self._file_logical_bytes = 0

    def _close_file(self) -> None:
        if self._writer is None:
            return
        assert self._temporary_path is not None
        assert self._final_path is not None
        self._writer.close()
        self._writer = None
        self._temporary_path.replace(self._final_path)
        metadata = pq.ParquetFile(self._final_path).metadata
        if metadata.num_rows != self._file_rows:
            raise RuntimeError(
                f"Parquet footer row mismatch path={self._final_path} "
                f"expected={self._file_rows} actual={metadata.num_rows}"
            )
        part_index = self.archive_index * 100 + self._file_index
        if part_index > 0xFFFFFFFF:
            raise RuntimeError(f"Parquet part index exceeds UInt32: {part_index}")
        self._completed.append(
            {
                "dataset_name": self.dataset_name,
                "target_table": self.target_table,
                "part_index": part_index,
                "path": str(self._final_path),
                "rows": int(metadata.num_rows),
                "bytes": self._final_path.stat().st_size,
                "logical_bytes": self._file_logical_bytes,
                "row_groups": metadata.num_row_groups,
                "format": "Parquet",
                "columns": self.columns,
            }
        )
        self._temporary_path = None
        self._final_path = None
        self._file_rows = 0
        self._file_logical_bytes = 0


def schema_for_columns(columns: Iterable[str]) -> pa.Schema:
    return pa.schema([pa.field(column, arrow_type_for_column(column), nullable=True) for column in columns])


def arrow_type_for_column(column: str) -> pa.DataType:
    if column in UINT16_COLUMNS:
        return pa.uint16()
    if column in UINT32_COLUMNS:
        return pa.uint32()
    if column in UINT64_COLUMNS:
        return pa.uint64()
    if column in UINT8_COLUMNS:
        return pa.uint8()
    if column in DATE_COLUMNS:
        return pa.date32()
    if column in TIMESTAMP_NS_COLUMNS:
        return pa.timestamp("ns", tz="UTC")
    if column in TIMESTAMP_MS_COLUMNS:
        return pa.timestamp("ms", tz="UTC")
    if column in LIST_STRING_COLUMNS:
        return pa.list_(pa.string())
    if column in WIDE_TEXT_COLUMNS:
        return pa.large_string()
    return pa.string()


def coerce_row(row: dict[str, Any], columns: Iterable[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in columns:
        value = row.get(column)
        if value in ("", None) and column in DATE_COLUMNS.union(TIMESTAMP_NS_COLUMNS, TIMESTAMP_MS_COLUMNS):
            output[column] = None
        elif column in DATE_COLUMNS:
            output[column] = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        elif column in TIMESTAMP_NS_COLUMNS or column in TIMESTAMP_MS_COLUMNS:
            output[column] = parse_utc_datetime(value)
        elif column in LIST_STRING_COLUMNS:
            output[column] = [str(item) for item in (value or [])]
        elif column in UINT16_COLUMNS or column in UINT32_COLUMNS or column in UINT64_COLUMNS or column in UINT8_COLUMNS:
            output[column] = None if value in ("", None) else int(value)
        else:
            output[column] = None if value is None else str(value)
    return output


def parse_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def estimate_row_bytes(row: dict[str, Any], columns: Iterable[str]) -> int:
    total = 16
    for column in columns:
        value = row.get(column)
        if value is None:
            total += 1
        elif isinstance(value, str):
            total += len(value.encode("utf-8", errors="replace")) + 8
        elif isinstance(value, (list, tuple)):
            total += 8 + sum(len(str(item).encode("utf-8", errors="replace")) + 4 for item in value)
        else:
            total += 8
    return total


def validate_parquet_part(path: Path, expected_rows: int, expected_columns: Iterable[str]) -> dict[str, int]:
    parquet = pq.ParquetFile(path)
    metadata = parquet.metadata
    actual_schema = parquet.schema_arrow
    actual_columns = actual_schema.names
    expected = list(expected_columns)
    if actual_columns != expected:
        raise RuntimeError(f"Parquet schema mismatch path={path} expected={expected} actual={actual_columns}")
    expected_schema = schema_for_columns(expected)
    type_mismatches = [
        f"{column}: expected={expected_schema.field(column).type} actual={actual_schema.field(column).type}"
        for column in expected
        if actual_schema.field(column).type != expected_schema.field(column).type
    ]
    if type_mismatches:
        raise RuntimeError(f"Parquet type mismatch path={path} " + "; ".join(type_mismatches))
    if expected_rows >= 0 and metadata.num_rows != expected_rows:
        raise RuntimeError(
            f"Parquet row mismatch path={path} expected={expected_rows} actual={metadata.num_rows}"
        )
    return {"rows": int(metadata.num_rows), "row_groups": int(metadata.num_row_groups)}


def convert_json_part(
    *,
    source_path: Path,
    writer: ParquetShardWriter,
) -> list[dict[str, Any]]:
    opener = gzip.open if source_path.name.lower().endswith(".gz") else open
    try:
        with opener(source_path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    writer.append(json.loads(line))
                except Exception as exc:
                    raise RuntimeError(f"legacy JSON conversion failed path={source_path} line={line_number}") from exc
        return writer.close()
    except Exception:
        writer.abort()
        raise
