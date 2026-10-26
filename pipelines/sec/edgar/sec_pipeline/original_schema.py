"""Lightweight additive SEC source schema, also usable by historical tools."""
TABLE = 'sec_xbrl_original_fact_v1'
FACT_COLUMNS = {
    'period_start_date': 'Nullable(Date)', 'frame': 'Nullable(String)',
    'dimensions_json': "String DEFAULT '{}'", 'value_exact': 'Nullable(String)',
    'source_kind': "LowCardinality(String) DEFAULT 'companyfacts'",
    'document_name': "String DEFAULT ''", 'source_positions': 'Array(UInt32)',
    'public_available_at': "Nullable(DateTime64(9, 'UTC'))",
    'availability_basis': "LowCardinality(String) DEFAULT ''",
    'extraction_version': "String DEFAULT ''",
    'source_version_key': "String DEFAULT ''",
    'source_accession_sha256': "String DEFAULT ''",
    'time_evidence_json': "String DEFAULT ''",
}
