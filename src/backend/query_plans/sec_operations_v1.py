from __future__ import annotations

from datetime import UTC, datetime

from research.mlops.clickhouse import sql_string


PLAN_VERSION = 1


def intraday_histogram(
    window_start_utc: datetime,
    window_end_utc: datetime,
    *,
    bin_seconds: int,
) -> str:
    safe_bin = max(1, int(bin_seconds))
    bin_count = int(
        ((window_end_utc - window_start_utc).total_seconds() + safe_bin - 1)
        // safe_bin
    )
    return f"""
        WITH
            {_datetime64(window_start_utc)} AS window_start,
            {_datetime64(window_end_utc)} AS window_end,
            filing_buckets AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(intDiv(dateDiff('second', window_start, accepted_at_utc) + {safe_bin // 2}, {safe_bin})) AS bucket_index
                FROM `q_live`.`sec_filing_v3`
                WHERE accepted_at_utc >= window_start
                  AND accepted_at_utc < window_end
            ),
            document_counts AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(count()) AS document_rows
                FROM `q_live`.`sec_filing_document_v3` FINAL
                WHERE (toString(cik), accession_number) IN (SELECT cik, accession_number FROM filing_buckets)
                GROUP BY cik, accession_number
            ),
            text_counts AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(count()) AS text_rows
                FROM `q_live`.`sec_filing_text_rendered_v3` FINAL
                WHERE (toString(cik), accession_number) IN (SELECT cik, accession_number FROM filing_buckets)
                GROUP BY cik, accession_number
            ),
            fact_counts AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(count()) AS xbrl_fact_rows
                FROM `q_live`.`sec_xbrl_company_fact_v3`
                WHERE (toString(cik), accession_number) IN (SELECT cik, accession_number FROM filing_buckets)
                GROUP BY cik, accession_number
            ),
            frame_counts AS
            (
                SELECT
                    toString(cik) AS cik,
                    accession_number,
                    toUInt64(count()) AS xbrl_frame_rows
                FROM `q_live`.`sec_xbrl_frame_observation_v3`
                WHERE (toString(cik), accession_number) IN (SELECT cik, accession_number FROM filing_buckets)
                GROUP BY cik, accession_number
            ),
            classified_filings AS
            (
                SELECT
                    f.bucket_index AS bucket_index,
                    toUInt64(ifNull(d.document_rows, 0)) AS related_document_rows,
                    toUInt64(ifNull(t.text_rows, 0)) AS related_text_rows,
                    toUInt64(ifNull(cf.xbrl_fact_rows, 0) + ifNull(fr.xbrl_frame_rows, 0)) AS related_xbrl_rows
                FROM filing_buckets AS f
                LEFT JOIN document_counts AS d
                    ON d.cik = f.cik AND d.accession_number = f.accession_number
                LEFT JOIN text_counts AS t
                    ON t.cik = f.cik AND t.accession_number = f.accession_number
                LEFT JOIN fact_counts AS cf
                    ON cf.cik = f.cik AND cf.accession_number = f.accession_number
                LEFT JOIN frame_counts AS fr
                    ON fr.cik = f.cik AND fr.accession_number = f.accession_number
            ),
            bucket_counts AS
            (
                SELECT
                    bucket_index,
                    toUInt64(count()) AS total_rows,
                    toUInt64(countIf(related_xbrl_rows > 0)) AS xbrl_rows,
                    toUInt64(countIf(related_xbrl_rows = 0 AND related_text_rows > 0)) AS text_rows,
                    toUInt64(countIf(related_xbrl_rows = 0 AND related_text_rows = 0 AND related_document_rows > 0)) AS document_rows,
                    toUInt64(countIf(related_xbrl_rows = 0 AND related_text_rows = 0 AND related_document_rows = 0)) AS filing_only_rows
                FROM classified_filings
                GROUP BY bucket_index
            )
        SELECT
            formatDateTime(
                window_start + toIntervalSecond(toInt64(b.bucket_index) * {safe_bin}),
                '%Y-%m-%dT%H:%i:%S.000Z',
                'UTC'
            ) AS bucket_utc,
            toUInt64(ifNull(c.filing_only_rows, 0)) AS filing_only_rows,
            toUInt64(ifNull(c.document_rows, 0)) AS document_rows,
            toUInt64(ifNull(c.text_rows, 0)) AS text_rows,
            toUInt64(ifNull(c.xbrl_rows, 0)) AS xbrl_rows,
            toUInt64(ifNull(c.total_rows, 0)) AS total_rows
        FROM
        (
            SELECT toUInt64(number) AS bucket_index
            FROM numbers({bin_count + 1})
        ) AS b
        LEFT JOIN bucket_counts AS c
            ON c.bucket_index = b.bucket_index
        ORDER BY b.bucket_index
        FORMAT JSONEachRow
    """


def _datetime64(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    formatted = aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"toDateTime64({sql_string(formatted)}, 6, 'UTC')"
