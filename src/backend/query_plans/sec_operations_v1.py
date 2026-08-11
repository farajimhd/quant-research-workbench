from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable

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


def today_summary(window_start_utc: datetime, window_end_utc: datetime) -> str:
    return f"""
        WITH
            {_datetime64(window_start_utc)} AS window_start,
            {_datetime64(window_end_utc)} AS window_end
        SELECT
            toUInt64(count()) AS total_filings,
            formatDateTime(max(accepted_at_utc), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS latest_accepted_at_utc
        FROM `q_live`.`sec_filing_v3`
        WHERE accepted_at_utc >= window_start
          AND accepted_at_utc < window_end
        FORMAT JSONEachRow
    """


def today_filings(
    window_start_utc: datetime,
    window_end_utc: datetime,
    *,
    limit: int,
    ascending: bool,
) -> str:
    safe_limit = max(1, min(int(limit), 1_000))
    direction = "ASC" if ascending else "DESC"
    return f"""
        WITH
            {_datetime64(window_start_utc)} AS window_start,
            {_datetime64(window_end_utc)} AS window_end
        SELECT
            f.filing_id,
            f.accession_number,
            f.accession_number_compact,
            toString(f.cik) AS cik,
            f.issuer_id,
            f.company_name,
            f.form_type,
            toString(f.filing_date) AS filing_date,
            toString(f.report_date) AS report_date,
            formatDateTime(f.accepted_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS accepted_at_utc,
            f.acceptance_datetime_raw,
            f.accepted_at_source,
            f.primary_document,
            f.primary_document_url,
            f.filing_detail_url,
            f.source_file_name,
            f.filing_size,
            f.items,
            f.text_status,
            'parent' AS activity_status
        FROM `q_live`.`sec_filing_v3` AS f
        WHERE f.accepted_at_utc >= window_start
          AND f.accepted_at_utc < window_end
        ORDER BY f.accepted_at_utc {direction}, f.accession_number {direction}
        LIMIT {safe_limit}
        FORMAT JSONEachRow
    """


def related_filing_counts(
    keys: Iterable[tuple[str, str]],
) -> dict[str, str]:
    normalized = sorted(
        {
            (str(cik).strip(), str(accession).strip())
            for cik, accession in keys
            if str(cik).strip() and str(accession).strip()
        }
    )
    if not normalized:
        return {}
    key_clause = ", ".join(
        f"({sql_string(cik)}, {sql_string(accession)})"
        for cik, accession in normalized
    )
    return {
        "documents": f"""
            SELECT
                toString(cik) AS cik,
                accession_number,
                toUInt64(count()) AS document_rows,
                toUInt64(countIf(document_role = 'primary')) AS primary_document_rows,
                toUInt64(countIf(has_normalized_text)) AS document_text_ready_rows,
                toUInt64(countIf(extraction_status NOT IN ('', 'ok', 'complete', 'completed', 'extracted'))) AS document_issue_rows,
                arraySort(arraySlice(groupUniqArray(nullIf(document_type, '')), 1, 8)) AS document_type_sample,
                arraySort(arraySlice(groupUniqArray(nullIf(file_extension, '')), 1, 8)) AS file_extension_sample
            FROM `q_live`.`sec_filing_document_v3` FINAL
            WHERE (toString(cik), accession_number) IN ({key_clause})
            GROUP BY cik, accession_number
            FORMAT JSONEachRow
        """,
        "texts": f"""
            SELECT
                toString(cik) AS cik,
                accession_number,
                toUInt64(count()) AS text_rows,
                toUInt64(sum(text_char_count)) AS text_chars,
                arraySort(arraySlice(groupUniqArray(nullIf(text_kind, '')), 1, 8)) AS text_kind_sample,
                arraySort(arraySlice(arrayDistinct(arrayFlatten(groupArray(quality_flags))), 1, 10)) AS quality_flag_sample
            FROM `q_live`.`sec_filing_text_rendered_v3` FINAL
            WHERE (toString(cik), accession_number) IN ({key_clause})
            GROUP BY cik, accession_number
            FORMAT JSONEachRow
        """,
        "company_facts": f"""
            SELECT
                toString(cik) AS cik,
                accession_number,
                toUInt64(count()) AS xbrl_fact_rows,
                toUInt64(uniqExact(tag)) AS xbrl_fact_tags,
                arraySort(arraySlice(groupUniqArray(nullIf(tag, '')), 1, 12)) AS xbrl_fact_tag_sample
            FROM `q_live`.`sec_xbrl_company_fact_v3`
            WHERE (toString(cik), accession_number) IN ({key_clause})
            GROUP BY cik, accession_number
            FORMAT JSONEachRow
        """,
        "frames": f"""
            SELECT
                toString(cik) AS cik,
                accession_number,
                toUInt64(count()) AS xbrl_frame_rows,
                toUInt64(uniqExact(tag)) AS xbrl_frame_tags,
                arraySort(arraySlice(groupUniqArray(nullIf(tag, '')), 1, 12)) AS xbrl_frame_tag_sample
            FROM `q_live`.`sec_xbrl_frame_observation_v3`
            WHERE (toString(cik), accession_number) IN ({key_clause})
            GROUP BY cik, accession_number
            FORMAT JSONEachRow
        """,
    }


def identity_rows_by_cik(ciks: Iterable[str]) -> str:
    normalized = sorted({str(cik).strip() for cik in ciks if str(cik).strip()})
    if not normalized:
        raise ValueError("SEC identity query requires at least one CIK")
    cik_clause = ", ".join(sql_string(cik) for cik in normalized)
    return f"""
        SELECT
            b.bridge_id,
            b.cik,
            b.issuer_id AS bridge_issuer_id,
            ifNull(b.security_id, '') AS bridge_security_id,
            ifNull(b.listing_id, '') AS bridge_listing_id,
            ifNull(b.symbol_id, '') AS bridge_symbol_id,
            ifNull(b.ticker, '') AS ticker,
            ifNull(b.accession_number, '') AS bridge_accession_number,
            toString(b.valid_from_date) AS bridge_valid_from_date,
            toString(b.valid_to_date_exclusive) AS bridge_valid_to_date_exclusive,
            b.mapping_method,
            b.mapping_status,
            b.confidence_score AS mapping_confidence_score,
            b.ambiguity_status,
            issuer.issuer_id,
            issuer.issuer_name,
            issuer.issuer_name_normalized,
            ifNull(issuer.legal_name, '') AS issuer_legal_name,
            ifNull(issuer.branding_name, '') AS issuer_branding_name,
            ifNull(issuer.entity_type, '') AS issuer_entity_type,
            ifNull(issuer.domicile_country_code, '') AS issuer_domicile_country_code,
            ifNull(issuer.state_of_incorporation, '') AS issuer_state_of_incorporation,
            ifNull(issuer.sic_code, '') AS issuer_sic_code,
            ifNull(issuer.sic_description, '') AS issuer_sic_description,
            ifNull(issuer.sector, '') AS issuer_sector,
            ifNull(issuer.industry, '') AS issuer_industry,
            ifNull(issuer.industry_group, '') AS issuer_industry_group,
            ifNull(issuer.website_url, '') AS issuer_website_url,
            ifNull(issuer.investor_website_url, '') AS issuer_investor_website_url,
            issuer.status AS issuer_status,
            sec.security_id,
            sec.security_name,
            sec.product_type AS security_product_type,
            ifNull(sec.asset_class, '') AS security_asset_class,
            ifNull(sec.instrument_type, '') AS security_instrument_type,
            ifNull(sec.security_type, '') AS security_type,
            ifNull(toString(sec.has_options), '') AS security_has_options,
            sec.status AS security_status,
            listing.listing_id,
            listing.exchange_code,
            listing.currency_code,
            ifNull(listing.ibkr_conid, '') AS ibkr_conid,
            ifNull(listing.board_code, '') AS listing_board_code,
            ifNull(listing.segment_name, '') AS listing_segment_name,
            listing.listing_status,
            listing.is_primary_listing,
            toString(listing.list_date) AS listing_list_date,
            toString(listing.delisted_date) AS listing_delisted_date,
            sym.symbol_id,
            sym.source_system AS symbol_source_system,
            sym.ticker_normalized,
            sym.display_name AS symbol_display_name,
            ifNull(sym.ticker_root, '') AS ticker_root,
            ifNull(sym.ticker_suffix, '') AS ticker_suffix,
            ifNull(sym.ticker_type_id, '') AS ticker_type_id,
            sym.asset_type AS symbol_asset_type,
            sym.instrument_type AS symbol_instrument_type,
            ifNull(sym.security_type, '') AS symbol_security_type,
            sym.status AS symbol_status,
            sym.primary_symbol_flag
        FROM `q_live`.id_sec_market_bridge_v3 AS b FINAL
        LEFT JOIN `q_live`.id_issuer_v1 AS issuer FINAL
            ON issuer.issuer_id = b.issuer_id
        LEFT JOIN `q_live`.id_security_v1 AS sec FINAL
            ON sec.security_id = ifNull(b.security_id, '')
        LEFT JOIN `q_live`.id_listing_v1 AS listing FINAL
            ON listing.listing_id = ifNull(b.listing_id, '')
        LEFT JOIN `q_live`.id_symbol_v1 AS sym FINAL
            ON sym.symbol_id = ifNull(b.symbol_id, '')
        WHERE b.cik IN ({cik_clause})
        ORDER BY
            b.cik ASC,
            sym.primary_symbol_flag DESC,
            listing.is_primary_listing DESC,
            b.confidence_score DESC,
            ifNull(b.ticker, '') ASC
        FORMAT JSONEachRow
    """


def _datetime64(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    formatted = aware.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    return f"toDateTime64({sql_string(formatted)}, 6, 'UTC')"
