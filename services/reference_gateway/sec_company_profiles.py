from __future__ import annotations

import hashlib
import html
import re
import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable

from research.mlops.clickhouse import ClickHouseHttpClient, insert_json_each_row, quote_ident, sql_string


PARSER_VERSION = "sec_company_profile_ixbrl_v2"

US_SUBDIVISION_CODES = frozenset(
    {
        "AK", "AL", "AR", "AS", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "GU", "HI", "IA", "ID",
        "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MP", "MS", "MT", "NC", "ND",
        "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "PR", "RI", "SC", "SD", "TN", "TX",
        "UT", "VA", "VI", "VT", "WA", "WI", "WV", "WY",
    }
)

US_SUBDIVISION_NAMES = frozenset(
    {
        "alabama", "alaska", "american samoa", "arizona", "arkansas", "california", "colorado", "connecticut",
        "delaware", "district of columbia", "florida", "georgia", "guam", "hawaii", "idaho", "illinois",
        "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
        "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
        "new mexico", "new york", "north carolina", "north dakota", "northern mariana islands", "ohio", "oklahoma",
        "oregon", "pennsylvania", "puerto rico", "rhode island", "south carolina", "south dakota", "tennessee",
        "texas", "united states virgin islands", "utah", "vermont", "virginia", "washington", "west virginia",
        "wisconsin", "wyoming",
    }
)

# EDGAR state/country codes are not ISO codes. This crosswalk covers every
# foreign jurisdiction currently present in the US-listed canonical universe.
# Unknown future codes remain NULL until a reviewed mapping is added.
SEC_JURISDICTION_TO_ISO = {
    "1P": "KZ", "1T": "MH", "2M": "DE",
    "A0": "CA", "A1": "CA", "A2": "CA", "A3": "CA", "A4": "CA", "A5": "CA", "A6": "CA", "A7": "CA", "A8": "CA", "A9": "CA",
    "B0": "CA", "B9": "AG", "C0": "AE", "C1": "AR", "C3": "AU", "C5": "BS", "C9": "BE",
    "D0": "BM", "D5": "BR", "D6": "IO", "D8": "VG", "E9": "KY", "F3": "CL", "F4": "CN", "F5": "TW", "F8": "CO",
    "G7": "DK", "H9": "FI", "I0": "FR", "J1": "GI", "J3": "GR", "K3": "HK", "K7": "IN", "L2": "IE", "L3": "IL", "L6": "IT",
    "M0": "JP", "M5": "KR", "N4": "LU", "N8": "MY", "O4": "MU", "O5": "MX", "P7": "NL", "R1": "PA",
    "T3": "ZA", "U0": "SG", "U3": "ES", "V7": "SE", "V8": "CH", "W8": "TR", "X0": "GB", "X1": "US",
    "Y7": "GG", "Y8": "IM", "Y9": "JE", "Z4": "CA",
}

COUNTRY_NAME_TO_ISO = {
    "antigua and barbuda": "AG", "argentina": "AR", "australia": "AU", "bahamas": "BS", "belgium": "BE",
    "bermuda": "BM", "brazil": "BR", "british indian ocean territory": "IO", "canada": "CA", "cayman islands": "KY",
    "chile": "CL", "china": "CN", "colombia": "CO", "denmark": "DK", "finland": "FI", "france": "FR", "germany": "DE",
    "gibraltar": "GI", "greece": "GR", "guernsey": "GG", "hong kong": "HK", "india": "IN", "ireland": "IE",
    "isle of man": "IM", "israel": "IL", "italy": "IT", "japan": "JP", "jersey": "JE", "kazakhstan": "KZ",
    "korea, republic of": "KR", "south korea": "KR", "luxembourg": "LU", "malaysia": "MY", "marshall islands": "MH",
    "mauritius": "MU", "mexico": "MX", "netherlands": "NL", "panama": "PA", "singapore": "SG", "south africa": "ZA",
    "spain": "ES", "sweden": "SE", "switzerland": "CH", "taiwan": "TW", "turkey": "TR", "united arab emirates": "AE",
    "united kingdom": "GB", "united states": "US", "united states of america": "US", "virgin islands, british": "VG",
}

ISO_ALPHA2_CODES = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split()
)

DEI_FIELDS = {
    "EntityRegistrantName": "issuer_name",
    "EntityIncorporationStateCountryCode": "incorporation_jurisdiction",
    "EntityAddressAddressLine1": "business_address_line1",
    "EntityAddressAddressLine2": "business_address_line2",
    "EntityAddressAddressLine3": "business_address_line3",
    "EntityAddressCityOrTown": "business_address_city",
    "EntityAddressStateOrProvince": "business_address_state_or_province",
    "EntityAddressPostalZipCode": "business_address_postal_code",
    "EntityAddressCountry": "business_address_country_raw",
}


@dataclass(frozen=True, slots=True)
class MaterializeResult:
    submissions_rows: int = 0
    filing_rows_read: int = 0
    filing_rows_written: int = 0
    filing_rows_rejected: int = 0
    filing_rows_skipped: int = 0


def normalize_country(value: Any) -> str | None:
    text = re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()
    if not text:
        return None
    code = text.upper()
    if code in US_SUBDIVISION_CODES:
        return "US"
    if code in SEC_JURISDICTION_TO_ISO:
        return SEC_JURISDICTION_TO_ISO[code]
    if code in ISO_ALPHA2_CODES:
        return code
    normalized = text.casefold().replace("u.s.a.", "united states").strip(" .")
    if normalized in US_SUBDIVISION_NAMES:
        return "US"
    if normalized.endswith(", canada") or normalized == "canada (federal level)":
        return "CA"
    return COUNTRY_NAME_TO_ISO.get(normalized)


def normalize_address_country(value: Any) -> str | None:
    """Normalize an explicit country field where ISO codes outrank US state codes."""
    text = re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()
    if not text:
        return None
    code = text.upper()
    if code in ISO_ALPHA2_CODES:
        return code
    if code in SEC_JURISDICTION_TO_ISO:
        return SEC_JURISDICTION_TO_ISO[code]
    normalized = text.casefold().replace("u.s.a.", "united states").strip(" .")
    if normalized.endswith(", canada") or normalized == "canada (federal level)":
        return "CA"
    return COUNTRY_NAME_TO_ISO.get(normalized)


_IX_ELEMENT_RE = re.compile(
    r"<ix:(?P<kind>nonnumeric|continuation)\b(?P<attrs>[^>]*)>(?P<body>.*?)</ix:(?P=kind)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_IX_ATTRIBUTE_RE = re.compile(r"(?P<name>[A-Za-z_:][\w:.-]*)\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)", re.DOTALL)
_IX_EXCLUDE_RE = re.compile(r"<ix:exclude\b[^>]*>.*?</ix:exclude\s*>", re.IGNORECASE | re.DOTALL)
_MARKUP_RE = re.compile(r"<[^>]+>", re.DOTALL)


def parse_company_profile_ixbrl(source_text: str) -> dict[str, str | None]:
    facts: list[dict[str, str]] = []
    continuations: dict[str, dict[str, str]] = {}
    for match in _IX_ELEMENT_RE.finditer(source_text or ""):
        attributes = {
            attribute.group("name").casefold(): html.unescape(attribute.group("value"))
            for attribute in _IX_ATTRIBUTE_RE.finditer(match.group("attrs"))
        }
        kind = match.group("kind").casefold()
        if kind == "nonnumeric":
            local_name = attributes.get("name", "").split(":", 1)[-1]
            if local_name not in DEI_FIELDS:
                continue
        value = clean_text(_MARKUP_RE.sub(" ", _IX_EXCLUDE_RE.sub(" ", match.group("body"))))
        row = {"value": value, "continued_at": attributes.get("continuedat", "")}
        if kind == "continuation":
            continuation_id = attributes.get("id", "")
            if continuation_id:
                continuations[continuation_id] = row
        else:
            row["name"] = attributes.get("name", "")
            facts.append(row)
    result: dict[str, str | None] = {target: None for target in DEI_FIELDS.values()}
    for fact in facts:
        raw_name = fact.get("name", "")
        local_name = raw_name.split(":", 1)[-1]
        target = DEI_FIELDS.get(local_name)
        if not target or result[target]:
            continue
        parts = [fact.get("value", "")]
        continuation_id = fact.get("continued_at", "")
        seen: set[str] = set()
        while continuation_id and continuation_id not in seen:
            seen.add(continuation_id)
            continuation = continuations.get(continuation_id)
            if not continuation:
                break
            parts.append(continuation.get("value", ""))
            continuation_id = continuation.get("continued_at", "")
        result[target] = clean_text(" ".join(parts)) or None
    result["issuer_legal_country_code"] = normalize_country(result.get("incorporation_jurisdiction"))
    result["issuer_business_country_code"] = normalize_address_country(result.get("business_address_country_raw")) or normalize_country(
        result.get("business_address_state_or_province")
    )
    return result


def materialize_sec_company_profiles(
    client: ClickHouseHttpClient,
    *,
    read_database: str,
    write_database: str,
    sec_database: str,
    start_date: date,
    end_date: date,
    run_id: str,
    include_current_submissions: bool,
    batch_size: int = 250,
) -> MaterializeResult:
    submissions_rows = 0
    if include_current_submissions:
        client.execute(current_submissions_insert_sql(read_database, write_database, sec_database, run_id))
        submissions_rows = scalar_int(
            client,
            f"SELECT count() FROM {table(write_database, 'market_issuer_company_profile_v1')} FINAL "
            f"WHERE source_run_id = {sql_string(run_id)} AND source_kind = 'sec_submissions_current'",
        )
    read = written = rejected = skipped = 0
    batch: list[dict[str, Any]] = []
    bridge = load_sec_bridge_map(client, read_database)
    windows = list(monthly_windows(start_date, end_date))
    for window_index, (window_start, window_end) in enumerate(windows, start=1):
        inventory_rows = list(client.iter_json_each_row(filing_profile_inventory_sql(read_database, window_start, window_end)))
        existing = load_existing_filing_profiles(client, write_database, window_start, window_end)
        inventory = {}
        for row in inventory_rows:
            if str(row.get("cik") or "") not in bridge:
                continue
            key = (str(row.get("document_id") or ""), str(row.get("content_sha256") or ""))
            if key in existing:
                skipped += 1
                continue
            inventory[str(row["filing_id"])] = row
        filing_ids = sorted(inventory)
        print(
            "sec_company_profiles "
            f"window={window_start.isoformat()}->{window_end.isoformat()} "
            f"active={window_index}/{len(windows)} queued={len(windows) - window_index} filing_candidates={len(filing_ids):,}",
            flush=True,
        )
        for offset in range(0, len(filing_ids), max(1, batch_size)):
            selected_ids = filing_ids[offset : offset + max(1, batch_size)]
            selected_rows = [inventory[filing_id] for filing_id in selected_ids]
            filing_metadata = {
                str(row["filing_id"]): row
                for row in client.iter_json_each_row(filing_metadata_by_keys_sql(read_database, selected_rows))
            }
            eligible_rows = [
                row
                for row in selected_rows
                if start_date.isoformat()
                <= str(filing_metadata.get(str(row["filing_id"]), {}).get("accepted_at_utc") or "")[:10]
                < end_date.isoformat()
            ]
            if not eligible_rows:
                continue
            documents_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
            for document in client.iter_json_each_row(
                filing_profile_documents_sql(read_database, window_start, window_end, eligible_rows)
            ):
                document_key = (
                    str(document.get("filing_id") or ""),
                    str(document.get("document_id") or ""),
                    str(document.get("content_sha256") or ""),
                )
                documents_by_key.setdefault(document_key, document)
            documents = list(documents_by_key.values())
            returned_filing_ids = {str(document.get("filing_id") or "") for document in documents}
            rejected += sum(1 for row in eligible_rows if str(row.get("filing_id") or "") not in returned_filing_ids)
            for document in documents:
                filing_id = str(document["filing_id"])
                filing = filing_metadata.get(filing_id)
                if not filing:
                    rejected += 1
                    continue
                accepted_at = str(filing.get("accepted_at_utc") or "")
                cik = str(inventory[filing_id].get("cik") or "")
                source_row = {**inventory[filing_id], **filing, **document, "issuer_id": bridge[cik]}
                read += 1
                parsed = parse_company_profile_ixbrl(str(source_row.get("source_text") or ""))
                if not any(parsed.get(field) for field in ("issuer_name", "issuer_legal_country_code", "business_address_city", "issuer_business_country_code")):
                    rejected += 1
                    continue
                batch.append(filing_profile_row(source_row, parsed, run_id))
                if len(batch) >= max(1, batch_size):
                    insert_profile_rows(client, write_database, batch)
                    written += len(batch)
                    batch.clear()
        if batch:
            insert_profile_rows(client, write_database, batch)
            written += len(batch)
            batch.clear()
        print(
            "sec_company_profiles "
            f"window={window_start.isoformat()}->{window_end.isoformat()} status=completed "
            f"read={read:,} written={written:,} skipped={skipped:,} rejected={rejected:,}",
            flush=True,
        )
    return MaterializeResult(submissions_rows, read, written, rejected, skipped)


def current_submissions_insert_sql(read_database: str, write_database: str, sec_database: str, run_id: str) -> str:
    legal_country = sec_country_sql("company.state_of_incorporation", "''")
    business_country = sec_country_sql(
        "JSONExtractString(company.addresses_json, 'business', 'stateOrCountry')",
        "JSONExtractString(company.addresses_json, 'business', 'stateOrCountryDescription')",
    )
    mailing_country = sec_country_sql(
        "JSONExtractString(company.addresses_json, 'mailing', 'stateOrCountry')",
        "JSONExtractString(company.addresses_json, 'mailing', 'stateOrCountryDescription')",
    )
    return f"""
INSERT INTO {table(write_database, 'market_issuer_company_profile_v1')}
(profile_id, issuer_id, cik, available_at_utc, profile_date, issuer_name, incorporation_jurisdiction,
 issuer_legal_country_code, business_address_line1, business_address_line2, business_address_line3,
 business_address_city, business_address_state_or_province, business_address_postal_code, issuer_business_country_code,
 mailing_address_line1, mailing_address_line2, mailing_address_city, mailing_address_state_or_province,
 mailing_address_postal_code, issuer_mailing_country_code, source_kind, source_accession_number, source_document_id,
 source_evidence_ref, parser_version, source_run_id, source_content_sha256, inserted_at)
WITH
    bridge AS
    (
        SELECT cik, argMax(issuer_id, tuple(confidence_score, last_seen_at_utc)) AS market_issuer_id
        FROM {table(read_database, 'id_sec_market_bridge_v3')} FINAL
        WHERE mapping_status = 'active' AND issuer_id != '' AND cik != ''
        GROUP BY cik
    ),
    now64(3, 'UTC') AS write_inserted_at
SELECT
    concat('sec-profile:submissions:', bridge.market_issuer_id, ':', company.source_file_id) AS profile_id,
    bridge.market_issuer_id AS issuer_id,
    company.cik,
    company.last_seen_at_utc AS available_at_utc,
    toDate(company.last_seen_at_utc) AS profile_date,
    nullIf(company.entity_name, '') AS issuer_name,
    nullIf(upper(ifNull(company.state_of_incorporation, '')), '') AS incorporation_jurisdiction,
    {legal_country} AS issuer_legal_country_code,
    nullIf(JSONExtractString(company.addresses_json, 'business', 'street1'), '') AS business_address_line1,
    nullIf(JSONExtractString(company.addresses_json, 'business', 'street2'), '') AS business_address_line2,
    CAST(NULL, 'Nullable(String)') AS business_address_line3,
    nullIf(JSONExtractString(company.addresses_json, 'business', 'city'), '') AS business_address_city,
    nullIf(JSONExtractString(company.addresses_json, 'business', 'stateOrCountry'), '') AS business_address_state_or_province,
    nullIf(JSONExtractString(company.addresses_json, 'business', 'zipCode'), '') AS business_address_postal_code,
    {business_country} AS issuer_business_country_code,
    nullIf(JSONExtractString(company.addresses_json, 'mailing', 'street1'), '') AS mailing_address_line1,
    nullIf(JSONExtractString(company.addresses_json, 'mailing', 'street2'), '') AS mailing_address_line2,
    nullIf(JSONExtractString(company.addresses_json, 'mailing', 'city'), '') AS mailing_address_city,
    nullIf(JSONExtractString(company.addresses_json, 'mailing', 'stateOrCountry'), '') AS mailing_address_state_or_province,
    nullIf(JSONExtractString(company.addresses_json, 'mailing', 'zipCode'), '') AS mailing_address_postal_code,
    {mailing_country} AS issuer_mailing_country_code,
    'sec_submissions_current' AS source_kind,
    CAST(NULL, 'Nullable(String)') AS source_accession_number,
    CAST(NULL, 'Nullable(String)') AS source_document_id,
    concat('sec_bulk_mirror_company_v3:', company.source_file_id, ':', company.cik) AS source_evidence_ref,
    'sec_submissions_v1' AS parser_version,
    {sql_string(run_id)} AS source_run_id,
    lower(hex(SHA256(concat(company.cik, ':', company.source_file_id, ':', company.entity_name, ':', company.addresses_json)))) AS source_content_sha256,
    write_inserted_at AS inserted_at
FROM {table(sec_database, 'sec_bulk_mirror_company_v3')} AS company FINAL
INNER JOIN bridge USING (cik)
""".strip()


def filing_profile_inventory_sql(database: str, start_date: date, end_date: date) -> str:
    return f"""
SELECT filing_id, cik, accession_number, document_id, content_format, content_sha256, source_revision_rank
FROM {table(database, 'sec_filing_text_v3')} FINAL
WHERE document_role = 'primary_document'
  AND document_type IN ('10-K', '10-K/A', '10-Q', '10-Q/A', '8-K', '8-K/A', '20-F', '20-F/A', '40-F', '40-F/A', '6-K')
  AND source_archive_date >= toDate({sql_string((start_date - timedelta(days=1)).isoformat())})
  AND source_archive_date < toDate({sql_string((end_date + timedelta(days=1)).isoformat())})
SETTINGS max_threads = 4, max_memory_usage = 8589934592, output_format_parallel_formatting = 0
FORMAT JSONEachRow
""".strip()


def filing_metadata_by_keys_sql(database: str, filings: list[dict[str, Any]]) -> str:
    if not filings:
        raise ValueError("filing metadata query requires at least one filing key")
    values = ", ".join(
        f"({sql_string(str(row.get('cik') or ''))}, {sql_string(str(row.get('accession_number') or ''))})"
        for row in filings
    )
    return f"""
SELECT filing_id, accession_number, cik, accepted_at_utc, form_type
FROM {table(database, 'sec_filing_v3')}
PREWHERE (cik, accession_number) IN ({values})
ORDER BY inserted_at DESC
LIMIT 1 BY cik, accession_number
SETTINGS max_threads = 4, max_memory_usage = 8589934592
FORMAT JSONEachRow
""".strip()


def filing_profile_documents_sql(database: str, start_date: date, end_date: date, filings: list[dict[str, Any]]) -> str:
    if not filings:
        raise ValueError("filing profile document query requires at least one filing key")
    values = ", ".join(
        "(" + ", ".join(
            (
                sql_string(str(row.get("cik") or "")),
                sql_string(str(row.get("accession_number") or "")),
                sql_string(str(row.get("document_id") or "")),
                sql_string(str(row.get("content_format") or "")),
                str(int(row.get("source_revision_rank") or 0)),
            )
        ) + ")"
        for row in filings
    )
    return f"""
SELECT
    filing_id,
    document_id,
    source_archive_path,
    source_archive_member,
    source_text,
    content_sha256,
    source_revision_rank
FROM {table(database, 'sec_filing_text_v3')}
PREWHERE source_archive_date >= toDate({sql_string((start_date - timedelta(days=1)).isoformat())})
  AND source_archive_date < toDate({sql_string((end_date + timedelta(days=1)).isoformat())})
  AND (cik, accession_number, document_id, content_format, source_revision_rank) IN ({values})
WHERE document_role = 'primary_document'
  AND positionCaseInsensitive(source_text, '<ix:') > 0
SETTINGS max_threads = 4, max_memory_usage = 8589934592, output_format_parallel_formatting = 0
FORMAT JSONEachRow
""".strip()


def filing_profile_row(source: dict[str, Any], parsed: dict[str, str | None], run_id: str) -> dict[str, Any]:
    accepted_at = str(source.get("accepted_at_utc") or "")
    source_hash = str(source.get("content_sha256") or "")
    issuer_id = str(source.get("issuer_id") or "")
    accession = str(source.get("accession_number") or "")
    document_id = str(source.get("document_id") or "")
    identity = f"{issuer_id}:{accession}:{document_id}:{source_hash}:{PARSER_VERSION}"
    return {
        "profile_id": "sec-profile:filing:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "issuer_id": issuer_id,
        "cik": str(source.get("cik") or ""),
        "available_at_utc": accepted_at,
        "profile_date": accepted_at[:10],
        "issuer_name": parsed.get("issuer_name"),
        "incorporation_jurisdiction": parsed.get("incorporation_jurisdiction"),
        "issuer_legal_country_code": parsed.get("issuer_legal_country_code"),
        "business_address_line1": parsed.get("business_address_line1"),
        "business_address_line2": parsed.get("business_address_line2"),
        "business_address_line3": parsed.get("business_address_line3"),
        "business_address_city": parsed.get("business_address_city"),
        "business_address_state_or_province": parsed.get("business_address_state_or_province"),
        "business_address_postal_code": parsed.get("business_address_postal_code"),
        "issuer_business_country_code": parsed.get("issuer_business_country_code"),
        "mailing_address_line1": None,
        "mailing_address_line2": None,
        "mailing_address_city": None,
        "mailing_address_state_or_province": None,
        "mailing_address_postal_code": None,
        "issuer_mailing_country_code": None,
        "source_kind": "sec_filing_dei",
        "source_accession_number": accession,
        "source_document_id": document_id,
        "source_evidence_ref": f"{source.get('source_archive_path') or ''}#{source.get('source_archive_member') or ''}",
        "parser_version": PARSER_VERSION,
        "source_run_id": run_id,
        "source_content_sha256": source_hash,
        "inserted_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    }


def insert_profile_rows(client: ClickHouseHttpClient, database: str, rows: list[dict[str, Any]]) -> None:
    insert_json_each_row(client, database, "market_issuer_company_profile_v1", list(rows[0]), rows)


def load_sec_bridge_map(client: ClickHouseHttpClient, database: str) -> dict[str, str]:
    query = f"""
SELECT cik, argMax(issuer_id, tuple(confidence_score, last_seen_at_utc)) AS market_issuer_id
FROM {table(database, 'id_sec_market_bridge_v3')} FINAL
WHERE mapping_status = 'active' AND issuer_id != '' AND cik != ''
GROUP BY cik
FORMAT JSONEachRow
""".strip()
    return {
        str(row["cik"]): str(row["market_issuer_id"])
        for row in client.iter_json_each_row(query)
    }


def load_existing_filing_profiles(
    client: ClickHouseHttpClient,
    database: str,
    start_date: date,
    end_date: date,
) -> set[tuple[str, str]]:
    query = f"""
SELECT source_document_id, source_content_sha256
FROM {table(database, 'market_issuer_company_profile_v1')} FINAL
WHERE source_kind = 'sec_filing_dei'
  AND parser_version = {sql_string(PARSER_VERSION)}
  AND profile_date >= toDate({sql_string(start_date.isoformat())})
  AND profile_date < toDate({sql_string(end_date.isoformat())})
  AND source_document_id IS NOT NULL
FORMAT JSONEachRow
""".strip()
    return {
        (str(row.get("source_document_id") or ""), str(row.get("source_content_sha256") or ""))
        for row in client.iter_json_each_row(query)
    }


def sec_country_sql(code_expression: str, description_expression: str) -> str:
    foreign_codes = sorted(SEC_JURISDICTION_TO_ISO)
    foreign_values = [SEC_JURISDICTION_TO_ISO[code] for code in foreign_codes]
    state_values = ", ".join(sql_string(code) for code in sorted(US_SUBDIVISION_CODES))
    code_values = ", ".join(sql_string(code) for code in foreign_codes)
    iso_values = ", ".join(sql_string(code) for code in foreign_values)
    name_conditions = []
    for name, iso in sorted(COUNTRY_NAME_TO_ISO.items()):
        name_conditions.extend((f"lowerUTF8(trim(BOTH ' ' FROM {description_expression})) = {sql_string(name)}", sql_string(iso)))
    name_conditions.extend(
        (
            f"endsWith(lowerUTF8(trim(BOTH ' ' FROM {description_expression})), ', canada')", "'CA'",
            f"lowerUTF8(trim(BOTH ' ' FROM {description_expression})) = 'canada (federal level)'", "'CA'",
        )
    )
    return (
        "multiIf("
        f"upper(trim(BOTH ' ' FROM ifNull({code_expression}, ''))) IN ({state_values}), 'US', "
        f"upper(trim(BOTH ' ' FROM ifNull({code_expression}, ''))) IN ({code_values}), "
        f"transform(upper(trim(BOTH ' ' FROM ifNull({code_expression}, ''))), [{code_values}], [{iso_values}], ''), "
        + ", ".join(name_conditions)
        + ", CAST(NULL, 'Nullable(String)'))"
    )


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    return int(client.query_tsv(sql).strip() or "0")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def monthly_windows(start_date: date, end_date: date) -> Iterable[tuple[date, date]]:
    cursor = start_date
    while cursor < end_date:
        month_end = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1]) + timedelta(days=1)
        next_cursor = min(month_end, end_date)
        yield cursor, next_cursor
        cursor = next_cursor


def table(database: str, name: str) -> str:
    return f"{quote_ident(database)}.{quote_ident(name)}"
