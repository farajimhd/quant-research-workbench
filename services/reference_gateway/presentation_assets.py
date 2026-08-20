from __future__ import annotations

import hashlib
import json
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from pipelines.sec.edgar.sec_pipeline.config import sec_user_agent
from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, quote_ident, sql_string
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.market_publications import ensure_market_publication_schema, table_exists


POLICY_VERSION = "presentation_asset_policy_v1"
ProgressCallback = Callable[[str, str, str, int | None], None]
PREFERRED_SEC_FORMS = (
    "10-K",
    "10-K/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
    "DEF 14A",
    "S-1",
    "S-1/A",
    "F-1",
    "F-1/A",
    "424B1",
    "424B2",
    "424B3",
    "424B4",
    "424B5",
    "424B7",
    "424B8",
    "8-K",
    "6-K",
)
GENERIC_ISSUER_TOKENS = frozenset(
    {
        "class",
        "company",
        "corporation",
        "corp",
        "group",
        "holding",
        "holdings",
        "inc",
        "incorporated",
        "limited",
        "ltd",
        "ordinary",
        "shares",
        "stock",
    }
)
THIRD_PARTY_LOGO_TOKENS = (
    "asyousow",
    "broadridge",
    "glasslewis",
    "investoradvocate",
    "investor-advocate",
    "proxyimpact",
    "shareholderproposal",
)


@dataclass(frozen=True, slots=True)
class PresentationRefreshResult:
    attempted: bool
    status: str
    requested_tickers: int = 0
    massive_tickers_refreshed: int = 0
    massive_candidates_written: int = 0
    sec_candidates_seen: int = 0
    sec_assets_written: int = 0
    sec_candidates_written: int = 0
    sec_candidates_accepted: int = 0
    sec_candidates_rejected: int = 0
    sec_candidates_ambiguous: int = 0
    sec_candidates_retryable: int = 0
    selections_written: int = 0
    failed: int = 0
    wall_seconds: float = 0.0
    details: dict[str, object] = field(default_factory=dict)


def run_presentation_asset_refresh(
    config: ReferenceGatewayConfig,
    *,
    tickers: Iterable[str] | None = None,
    refresh_massive: bool,
    reason: str,
    on_progress: ProgressCallback | None = None,
) -> PresentationRefreshResult:
    started = time.perf_counter()
    requested = sorted({str(ticker or "").strip().upper() for ticker in (tickers or []) if str(ticker or "").strip()})

    def progress(status: str, message: str, rows: int | None = None) -> None:
        if on_progress is not None:
            on_progress("presentation_asset_resolution", status, message, rows)

    if not config.execute:
        return PresentationRefreshResult(False, "skipped", requested_tickers=len(requested), details={"reason": "diagnostic_mode"})

    client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
    ensure_market_publication_schema(
        client,
        database=config.clickhouse_write_database,
        read_database=config.clickhouse_read_database,
        storage_policy="",
    )
    massive_refreshed = 0
    source_errors: list[dict[str, str]] = []
    candidate_tickers = list(requested)
    if refresh_massive:
        candidate_tickers = stale_massive_tickers(
            client,
            database=config.clickhouse_write_database,
            identity_database=config.clickhouse_read_database,
            stale_after_days=config.presentation_massive_refresh_days,
            limit=config.presentation_massive_refresh_batch_size,
        )
        if candidate_tickers:
            progress("running", f"Refreshing Massive presentation sources for {len(candidate_tickers):,} stale ticker(s).", len(candidate_tickers))
            try:
                from services.reference_gateway.current_ticker_detail_sync import run_current_ticker_detail_sync

                detail_result = run_current_ticker_detail_sync(config, tickers=candidate_tickers)
                massive_refreshed = detail_result.matched
                if detail_result.status not in {"completed", "covered_empty", "skipped"}:
                    source_errors.append({"source": "massive", "reason": str(detail_result.details.get("reason") or detail_result.status)})
            except Exception as exc:  # source isolation is intentional; SEC and the old selection remain usable
                source_errors.append({"source": "massive", "reason": f"{type(exc).__name__}: {exc}"})

    link_tickers = sorted(set(requested) | set(candidate_tickers))
    progress("running", "Linking downloaded Massive icon and logo assets to canonical issuers.", None)
    try:
        massive_rows = build_massive_candidate_rows(
            client,
            database=config.clickhouse_write_database,
            identity_database=config.clickhouse_read_database,
            tickers=link_tickers or None,
            limit=config.presentation_candidate_batch_size,
            run_id=_run_id(reason),
        )
        massive_written = insert_rows(
            client,
            config.clickhouse_write_database,
            "market_issuer_presentation_candidate_v1",
            massive_rows,
            config.presentation_insert_batch_size,
        )
    except Exception as exc:
        massive_written = 0
        source_errors.append({"source": "massive_candidate_link", "reason": f"{type(exc).__name__}: {exc}"})

    sec_seen = sec_assets_written = sec_candidates_written = 0
    candidate_rows: list[dict[str, Any]] = []
    user_agent = sec_user_agent().strip()
    if not user_agent:
        source_errors.append({"source": "sec", "reason": "SEC_USER_AGENT is not configured"})
    elif all(
        table_exists(client, config.clickhouse_read_database, name)
        for name in ("sec_filing_document_v3", "sec_filing_v3", "id_sec_market_bridge_v3", "id_issuer_v1")
    ):
        try:
            progress("running", "Classifying new SEC filing-image candidates.", None)
            sec_documents = load_sec_candidate_documents(
                client,
                database=config.clickhouse_read_database,
                candidate_database=config.clickhouse_write_database,
                tickers=requested or None,
                lookback_days=config.presentation_sec_lookback_days if requested else 0,
                limit=config.presentation_sec_candidate_batch_size,
            )
            sec_seen = len(sec_documents)
            asset_rows: list[dict[str, Any]] = []
            run_id = _run_id(reason)
            for index, document in enumerate(sec_documents, start=1):
                try:
                    asset_row, candidate_row = classify_sec_candidate(
                        document,
                        asset_root=config.presentation_asset_root_win,
                        user_agent=user_agent,
                        timeout_seconds=config.presentation_sec_request_timeout_seconds,
                        max_bytes=config.presentation_sec_max_asset_bytes,
                        run_id=run_id,
                    )
                    if asset_row is not None:
                        asset_rows.append(asset_row)
                    candidate_rows.append(candidate_row)
                except (ValueError, OSError, TimeoutError, urllib.error.URLError) as exc:
                    candidate_rows.append(
                        failed_sec_candidate(
                            document,
                            run_id=run_id,
                            reason=f"{type(exc).__name__}: {exc}",
                            retryable=is_retryable_fetch_error(exc),
                        )
                    )
                if index < len(sec_documents):
                    time.sleep(max(0.0, config.presentation_sec_request_min_interval_seconds))
            sec_assets_written = insert_rows(
                client,
                config.clickhouse_write_database,
                "market_presentation_asset_v1",
                asset_rows,
                config.presentation_insert_batch_size,
            )
            sec_candidates_written = insert_rows(
                client,
                config.clickhouse_write_database,
                "market_issuer_presentation_candidate_v1",
                candidate_rows,
                config.presentation_insert_batch_size,
            )
        except Exception as exc:
            source_errors.append({"source": "sec_candidate_sync", "reason": f"{type(exc).__name__}: {exc}"})
    else:
        source_errors.append({"source": "sec", "reason": "required SEC or bridge table is missing"})

    issuer_ids = issuer_ids_for_tickers(client, config.clickhouse_read_database, requested) if requested else None
    progress("running", "Resolving the best accepted presentation asset per issuer.", None)
    selections = resolve_presentations(
        client,
        database=config.clickhouse_write_database,
        issuer_ids=issuer_ids,
        run_id=_run_id(reason),
    )
    selections_written = insert_rows(
        client,
        config.clickhouse_write_database,
        "market_issuer_presentation_selection_v1",
        selections,
        config.presentation_insert_batch_size,
    )
    fetch_failed = sum(1 for row in candidate_rows if row.get("candidate_status") == "fetch_failed")
    accepted = sum(1 for row in candidate_rows if row.get("candidate_status") == "accepted")
    rejected = sum(1 for row in candidate_rows if row.get("candidate_status") == "rejected")
    ambiguous = sum(1 for row in candidate_rows if row.get("candidate_status") == "ambiguous")
    failed = len(source_errors) + fetch_failed
    status = "completed" if not source_errors and fetch_failed == 0 else "partial"
    result = PresentationRefreshResult(
        True,
        status,
        requested_tickers=len(requested),
        massive_tickers_refreshed=massive_refreshed,
        massive_candidates_written=massive_written,
        sec_candidates_seen=sec_seen,
        sec_assets_written=sec_assets_written,
        sec_candidates_written=sec_candidates_written,
        sec_candidates_accepted=accepted,
        sec_candidates_rejected=rejected,
        sec_candidates_ambiguous=ambiguous,
        sec_candidates_retryable=fetch_failed,
        selections_written=selections_written,
        failed=failed,
        wall_seconds=time.perf_counter() - started,
        details={"reason": reason, "policy_version": POLICY_VERSION, "source_errors": source_errors},
    )
    progress(status, f"Presentation refresh {status}: candidates={massive_written + sec_candidates_written:,}, selections={selections_written:,}.", selections_written)
    return result


def stale_massive_tickers(
    client: ClickHouseHttpClient,
    *,
    database: str,
    identity_database: str,
    stale_after_days: int,
    limit: int,
) -> list[str]:
    db = quote_ident(database)
    identity_db = quote_ident(identity_database)
    rows = query_rows(
        client,
        f"""
        WITH (SELECT max(universe_date) FROM {identity_db}.feature_tradable_universe_v1 FINAL) AS latest_universe_date,
        last_icon AS
        (
            SELECT lowerUTF8(display_name) AS display_key, max(last_verified_at_utc) AS last_verified_at_utc
            FROM {db}.market_presentation_asset_v1 FINAL
            WHERE source_system = 'massive' AND asset_kind = 'icon' AND status = 'active'
            GROUP BY display_key
        )
        SELECT upperUTF8(u.ticker) AS ticker
        FROM {identity_db}.feature_tradable_universe_v1 AS u FINAL
        LEFT JOIN last_icon AS a ON a.display_key = lowerUTF8(concat(u.ticker, ' icon'))
        WHERE u.universe_date = latest_universe_date
          AND u.is_tradable = 1
          AND (a.last_verified_at_utc IS NULL OR a.last_verified_at_utc < now64(3, 'UTC') - INTERVAL {max(1, int(stale_after_days))} DAY)
        ORDER BY ifNull(a.last_verified_at_utc, toDateTime64('1970-01-01 00:00:00', 3, 'UTC')), ticker
        LIMIT {max(1, int(limit))}
        """,
    )
    return [str(row.get("ticker") or "").upper() for row in rows if str(row.get("ticker") or "").strip()]


def build_massive_candidate_rows(
    client: ClickHouseHttpClient,
    *,
    database: str,
    identity_database: str,
    tickers: list[str] | None,
    limit: int,
    run_id: str,
) -> list[dict[str, Any]]:
    db = quote_ident(database)
    identity_db = quote_ident(identity_database)
    ticker_filter = ""
    if tickers:
        ticker_filter = "AND upperUTF8(u.ticker) IN (" + ", ".join(sql_string(value) for value in tickers) + ")"
    rows = query_rows(
        client,
        f"""
        WITH (SELECT max(universe_date) FROM {identity_db}.feature_tradable_universe_v1 FINAL) AS latest_universe_date
        SELECT
            u.issuer_id,
            any(u.listing_id) AS listing_id,
            upperUTF8(u.ticker) AS ticker,
            a.asset_id,
            a.asset_kind,
            a.content_hash_sha256,
            ifNull(a.first_seen_at_utc, a.inserted_at) AS first_seen_at_utc,
            a.relative_path,
            a.mime_type
        FROM {identity_db}.feature_tradable_universe_v1 AS u FINAL
        INNER JOIN {db}.market_presentation_asset_v1 AS a FINAL
            ON lowerUTF8(a.display_name) = lowerUTF8(concat(u.ticker, ' ', a.asset_kind))
           AND a.source_system = 'massive'
           AND a.asset_kind IN ('icon', 'logo')
           AND a.status = 'active'
        LEFT JOIN {db}.market_issuer_presentation_candidate_v1 AS c FINAL
            ON c.issuer_id = u.issuer_id AND c.asset_id = a.asset_id AND c.source_system = 'massive'
        WHERE u.universe_date = latest_universe_date
          AND u.is_tradable = 1
          AND (c.candidate_id IS NULL OR c.candidate_id = '')
          {ticker_filter}
        GROUP BY u.issuer_id, ticker, a.asset_id, a.asset_kind, a.content_hash_sha256,
                 a.first_seen_at_utc, a.inserted_at, a.relative_path, a.mime_type
        ORDER BY first_seen_at_utc DESC, ticker, a.asset_kind
        LIMIT {max(1, int(limit))}
        """,
    )
    inserted_at = now64()
    output = []
    for row in rows:
        source_kind = "massive_icon" if row["asset_kind"] == "icon" else "massive_logo"
        quality_class = "compact_mark" if source_kind == "massive_icon" else "wordmark"
        quality_score = 800.0 if source_kind == "massive_icon" else 400.0
        candidate_id = stable_id("presentation_candidate", f"{row['issuer_id']}:{row['asset_id']}:{source_kind}")
        output.append(
            {
                "candidate_id": candidate_id,
                "issuer_id": row["issuer_id"],
                "listing_id": row.get("listing_id"),
                "provider_ticker": row.get("ticker"),
                "asset_id": row["asset_id"],
                "source_system": "massive",
                "source_kind": source_kind,
                "source_cik": None,
                "source_accession_number": None,
                "source_document_id": None,
                "source_revision_rank": 0,
                "source_version_key": "",
                "observed_at_utc": dt64(row.get("first_seen_at_utc")),
                "valid_from_date": str(row.get("first_seen_at_utc") or "")[:10] or None,
                "quality_class": quality_class,
                "width_px": None,
                "height_px": None,
                "aspect_ratio": None,
                "identity_confidence": 0.95,
                "quality_score": quality_score,
                "candidate_status": "accepted",
                "status_reason": "canonical_ticker_asset_link",
                "evidence_json": json.dumps({"relative_path": row.get("relative_path"), "mime_type": row.get("mime_type")}, sort_keys=True, separators=(",", ":")),
                "source_run_id": run_id,
                "source_content_sha256": row.get("content_hash_sha256") or "",
                "inserted_at": inserted_at,
            }
        )
    return output


def load_sec_candidate_documents(
    client: ClickHouseHttpClient,
    *,
    database: str,
    candidate_database: str,
    tickers: list[str] | None,
    lookback_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    db = quote_ident(database)
    candidate_db = quote_ident(candidate_database)
    ticker_filter = ""
    if tickers:
        ticker_filter = "AND upperUTF8(ifNull(bridge.ticker, '')) IN (" + ", ".join(sql_string(value) for value in tickers) + ")"
    lookback_filter = ""
    if lookback_days > 0:
        lookback_filter = f"AND d.source_archive_date >= today() - INTERVAL {int(lookback_days)} DAY"
    forms = ", ".join(sql_string(value) for value in PREFERRED_SEC_FORMS)
    return query_rows(
        client,
        f"""
        WITH bridge AS
        (
            SELECT
                cik,
                any(issuer_id) AS market_issuer_id,
                any(ticker) AS ticker,
                any(listing_id) AS listing_id
            FROM {db}.id_sec_market_bridge_v3 FINAL
            WHERE mapping_status = 'active'
            GROUP BY cik
            HAVING uniqExact(issuer_id) = 1
        ),
        prior AS
        (
            SELECT source_document_id, source_revision_rank, source_version_key, source_content_sha256, issuer_id,
                   argMax(candidate_status, inserted_at) AS candidate_status
            FROM {candidate_db}.market_issuer_presentation_candidate_v1 FINAL
            WHERE source_system = 'sec_edgar'
            GROUP BY source_document_id, source_revision_rank, source_version_key, source_content_sha256, issuer_id
        )
        SELECT
            d.document_id,
            d.accession_number,
            d.cik,
            d.sequence_number,
            d.document_name,
            d.description,
            d.document_url,
            d.mime_type,
            d.byte_size,
            d.content_sha256,
            d.source_revision_at,
            d.source_revision_rank,
            d.source_version_key,
            d.source_archive_date,
            f.form_type,
            f.accepted_at_utc,
                bridge.market_issuer_id AS issuer_id,
            bridge.listing_id,
            bridge.ticker,
            issuer.issuer_name,
            issuer.branding_name,
            arrayFirst(
                token -> lengthUTF8(token) >= 4
                    AND token NOT IN ('class','company','corporation','corp','group','holding','holdings','inc','incorporated','limited','ltd','ordinary','shares','stock'),
                splitByRegexp('[^a-z0-9]+', lowerUTF8(ifNull(issuer.branding_name, issuer.issuer_name)))
            ) AS issuer_filename_token
        FROM {db}.sec_filing_document_v3 AS d FINAL
        INNER JOIN bridge ON bridge.cik = d.cik
        INNER JOIN
        (
            SELECT accession_number,
                   argMax(form_type, tuple(inserted_at, filing_id)) AS form_type,
                   argMax(accepted_at_utc, tuple(inserted_at, filing_id)) AS accepted_at_utc
            FROM {db}.sec_filing_v3 FINAL
            GROUP BY accession_number
        ) AS f ON f.accession_number = d.accession_number
        LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL ON issuer.issuer_id = bridge.market_issuer_id
        LEFT JOIN prior
            ON prior.source_document_id = d.document_id
           AND ifNull(prior.source_revision_rank, 0) = ifNull(d.source_revision_rank, 0)
           AND ifNull(prior.source_version_key, '') = ifNull(d.source_version_key, '')
           AND prior.source_content_sha256 = d.content_sha256
           AND prior.issuer_id = bridge.market_issuer_id
        WHERE d.document_role = 'image'
          AND d.document_url IS NOT NULL
          AND
          (
              positionCaseInsensitiveUTF8(concat(d.document_name, ' ', ifNull(d.description, '')), 'logo') > 0
              OR (issuer_filename_token != '' AND positionCaseInsensitiveUTF8(d.document_name, issuer_filename_token) > 0)
          )
          AND f.form_type IN ({forms})
          AND (prior.source_document_id IS NULL OR prior.source_document_id = '' OR prior.candidate_status = 'fetch_failed')
          {ticker_filter}
          {lookback_filter}
        ORDER BY d.source_revision_at DESC, d.source_revision_rank DESC, d.document_id
        LIMIT {max(1, int(limit))}
        """,
    )


def classify_sec_candidate(
    document: dict[str, Any],
    *,
    asset_root: Path,
    user_agent: str,
    timeout_seconds: int,
    max_bytes: int,
    run_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    name = str(document.get("document_name") or "")
    lowered_name = name.lower()
    suspicious = next((token for token in THIRD_PARTY_LOGO_TOKENS if token in lowered_name), "")
    if suspicious:
        return None, sec_candidate_row(
            document,
            asset_id="",
            status="rejected",
            reason="third_party_filename_token",
            quality_class="unsuitable",
            identity_confidence=0.0,
            quality_score=0.0,
            run_id=run_id,
            evidence={"matched_token": suspicious},
        )

    identity_match, identity_evidence = sec_identity_match(document)
    content = fetch_sec_asset(
        str(document.get("document_url") or ""),
        user_agent=user_agent,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    digest = hashlib.sha256(content).hexdigest()
    expected_digest = str(document.get("content_sha256") or "").lower()
    width, height, detected_mime = image_dimensions_and_mime(content, str(document.get("mime_type") or ""))
    if width is None or height is None or min(width, height) < 32:
        quality_class = "unsuitable"
        candidate_status = "rejected"
        reason = "invalid_or_too_small_image"
        score = 0.0
    else:
        aspect_ratio = max(width / height, height / width)
        quality_class = "compact_mark" if aspect_ratio <= 2.5 else "wordmark"
        if not identity_match:
            candidate_status = "ambiguous"
            reason = "issuer_identity_not_proven_by_filename"
            score = 0.0
        else:
            candidate_status = "accepted"
            reason = "issuer_filename_and_filing_identity_match"
            score = 1000.0 if quality_class == "compact_mark" else 600.0
            if "new" in lowered_name:
                score += 10.0
            if int(document.get("sequence_number") or 0) > 10:
                score -= 25.0
    suffix = suffix_for_mime(detected_mime)
    relative_path = Path("sec") / str(document.get("cik") or "unknown") / digest[:2] / f"{digest}{suffix}"
    target = asset_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != len(content):
        target.write_bytes(content)
    observed = dt64(document.get("accepted_at_utc") or document.get("source_revision_at") or datetime.now(UTC))
    asset_id = stable_id("presentation_asset", f"sec_edgar:{digest}")
    asset_row = {
        "asset_id": asset_id,
        "asset_kind": "logo",
        "display_name": f"{str(document.get('ticker') or document.get('cik') or '').upper()} SEC logo",
        "relative_path": str(relative_path).replace("\\", "/"),
        "mime_type": detected_mime,
        "byte_size": len(content),
        "content_hash_sha256": digest,
        "source_system": "sec_edgar",
        "source_reference": str(document.get("document_url") or ""),
        "source_file_name": name,
        "status": "active",
        "first_seen_at_utc": observed,
        "last_seen_at_utc": observed,
        "last_verified_at_utc": now64(),
        "source_run_id": run_id,
        "source_content_sha256": expected_digest or digest,
        "inserted_at": now64(),
    }
    aspect = max(width / height, height / width) if width and height else None
    candidate = sec_candidate_row(
        document,
        asset_id=asset_id,
        status=candidate_status,
        reason=reason,
        quality_class=quality_class,
        identity_confidence=0.98 if identity_match else 0.35,
        quality_score=score,
        run_id=run_id,
        width=width,
        height=height,
        aspect_ratio=aspect,
        evidence={
            "identity": identity_evidence,
            "sequence_number": document.get("sequence_number"),
            "form_type": document.get("form_type"),
            "sec_archive_payload_sha256": expected_digest,
            "downloaded_asset_sha256": digest,
        },
    )
    return asset_row, candidate


def sec_identity_match(document: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    filename = normalize_token_text(str(document.get("document_name") or ""))
    ticker = normalize_token_text(str(document.get("ticker") or ""))
    issuer_name = str(document.get("branding_name") or document.get("issuer_name") or "")
    issuer_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", issuer_name.lower())
        if len(token) >= 4 and token not in GENERIC_ISSUER_TOKENS
    ]
    ticker_match = len(ticker) >= 3 and ticker in filename
    name_matches = sorted({token for token in issuer_tokens if token in filename})
    return ticker_match or bool(name_matches), {"ticker_match": ticker_match, "issuer_token_matches": name_matches}


def sec_candidate_row(
    document: dict[str, Any],
    *,
    asset_id: str,
    status: str,
    reason: str,
    quality_class: str,
    identity_confidence: float,
    quality_score: float,
    run_id: str,
    width: int | None = None,
    height: int | None = None,
    aspect_ratio: float | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    revision_rank = int(document.get("source_revision_rank") or 0)
    source_version_key = str(document.get("source_version_key") or "")
    source_content_sha256 = str(document.get("content_sha256") or "")
    candidate_id = stable_id(
        "presentation_candidate",
        f"{document.get('issuer_id')}:{document.get('document_id')}:{revision_rank}:{source_version_key}:{source_content_sha256}",
    )
    observed = document.get("accepted_at_utc") or document.get("source_revision_at") or datetime.now(UTC)
    return {
        "candidate_id": candidate_id,
        "issuer_id": str(document.get("issuer_id") or ""),
        "listing_id": document.get("listing_id"),
        "provider_ticker": document.get("ticker"),
        "asset_id": asset_id,
        "source_system": "sec_edgar",
        "source_kind": "sec_filing_logo",
        "source_cik": document.get("cik"),
        "source_accession_number": document.get("accession_number"),
        "source_document_id": document.get("document_id"),
        "source_revision_rank": revision_rank,
        "source_version_key": source_version_key,
        "observed_at_utc": dt64(observed),
        "valid_from_date": str(document.get("accepted_at_utc") or document.get("source_archive_date") or "")[:10] or None,
        "quality_class": quality_class,
        "width_px": width,
        "height_px": height,
        "aspect_ratio": aspect_ratio,
        "identity_confidence": identity_confidence,
        "quality_score": quality_score,
        "candidate_status": status,
        "status_reason": reason,
        "evidence_json": json.dumps(evidence or {}, sort_keys=True, separators=(",", ":"), default=str),
        "source_run_id": run_id,
        "source_content_sha256": source_content_sha256,
        "inserted_at": now64(),
    }


def failed_sec_candidate(document: dict[str, Any], *, run_id: str, reason: str, retryable: bool = True) -> dict[str, Any]:
    return sec_candidate_row(
        document,
        asset_id="",
        status="fetch_failed" if retryable else "rejected",
        reason="asset_fetch_or_validation_failed" if retryable else "source_asset_permanently_unavailable",
        quality_class="unknown",
        identity_confidence=0.0,
        quality_score=0.0,
        run_id=run_id,
        evidence={"error": reason[:500]},
    )


def is_retryable_fetch_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 425, 429} or exc.code >= 500
    return not isinstance(exc, ValueError)


def resolve_presentations(
    client: ClickHouseHttpClient,
    *,
    database: str,
    issuer_ids: list[str] | None,
    run_id: str,
) -> list[dict[str, Any]]:
    db = quote_ident(database)
    issuer_filter = ""
    if issuer_ids:
        issuer_filter = "AND c.issuer_id IN (" + ", ".join(sql_string(value) for value in issuer_ids) + ")"
    candidates = query_rows(
        client,
        f"""
        SELECT
            c.issuer_id, c.asset_id, c.source_system, c.source_kind, c.quality_class,
            c.quality_score, c.observed_at_utc, c.candidate_id
        FROM {db}.market_issuer_presentation_candidate_v1 AS c FINAL
        INNER JOIN {db}.market_presentation_asset_v1 AS a FINAL ON a.asset_id = c.asset_id
        WHERE c.candidate_status = 'accepted'
          AND a.status = 'active'
          {issuer_filter}
        ORDER BY
            c.issuer_id,
            multiIf(
                c.source_kind = 'sec_filing_logo' AND c.quality_class = 'compact_mark', 4,
                c.source_kind = 'massive_icon', 3,
                c.source_kind = 'sec_filing_logo' AND c.quality_class = 'wordmark', 2,
                c.source_kind = 'massive_logo', 1,
                0
            ) DESC,
            c.observed_at_utc DESC,
            c.quality_score DESC,
            c.asset_id
        """,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(str(candidate["issuer_id"]), []).append(candidate)
    if not grouped:
        return []
    current = current_selections(client, database=database, issuer_ids=sorted(grouped))
    now = now64()
    output: list[dict[str, Any]] = []
    for issuer_id, issuer_candidates in grouped.items():
        selected = issuer_candidates[0]
        candidate_set_hash = hashlib.sha256(
            "\n".join(sorted(str(row["candidate_id"]) for row in issuer_candidates)).encode("utf-8")
        ).hexdigest()
        prior = current.get(issuer_id)
        if (
            prior
            and prior.get("asset_id") == selected.get("asset_id")
            and prior.get("policy_version") == POLICY_VERSION
            and prior.get("candidate_set_sha256") == candidate_set_hash
        ):
            continue
        selection_id = stable_id("presentation_selection", f"{issuer_id}:{selected['asset_id']}:{POLICY_VERSION}:{candidate_set_hash}")
        output.append(
            {
                "selection_id": selection_id,
                "issuer_id": issuer_id,
                "asset_id": selected["asset_id"],
                "source_system": selected["source_system"],
                "source_kind": selected["source_kind"],
                "quality_class": selected["quality_class"],
                "quality_score": selected["quality_score"],
                "policy_version": POLICY_VERSION,
                "selection_reason": selection_reason(str(selected["source_kind"]), str(selected["quality_class"])),
                "candidate_set_sha256": candidate_set_hash,
                "selected_at_utc": now,
                "source_run_id": run_id,
                "inserted_at": now,
            }
        )
    return output


def current_selections(client: ClickHouseHttpClient, *, database: str, issuer_ids: list[str]) -> dict[str, dict[str, Any]]:
    db = quote_ident(database)
    rows = query_rows(
        client,
        f"""
        SELECT issuer_id,
               argMax(asset_id, tuple(selected_at_utc, inserted_at, selection_id)) AS asset_id,
               argMax(policy_version, tuple(selected_at_utc, inserted_at, selection_id)) AS policy_version,
               argMax(candidate_set_sha256, tuple(selected_at_utc, inserted_at, selection_id)) AS candidate_set_sha256
        FROM {db}.market_issuer_presentation_selection_v1
        WHERE issuer_id IN ({', '.join(sql_string(value) for value in issuer_ids)})
        GROUP BY issuer_id
        """,
    )
    return {str(row["issuer_id"]): row for row in rows}


def issuer_ids_for_tickers(client: ClickHouseHttpClient, database: str, tickers: list[str]) -> list[str]:
    if not tickers:
        return []
    db = quote_ident(database)
    rows = query_rows(
        client,
        f"""
        SELECT DISTINCT issuer_id
        FROM {db}.feature_tradable_universe_v1 FINAL
        WHERE universe_date = (SELECT max(universe_date) FROM {db}.feature_tradable_universe_v1 FINAL)
          AND upperUTF8(ticker) IN ({', '.join(sql_string(value) for value in tickers)})
        """,
    )
    return [str(row["issuer_id"]) for row in rows if row.get("issuer_id")]


def selection_reason(source_kind: str, quality_class: str) -> str:
    if source_kind == "sec_filing_logo" and quality_class == "compact_mark":
        return "verified_sec_compact_mark"
    if source_kind == "massive_icon":
        return "massive_compact_icon_fallback"
    if source_kind == "sec_filing_logo":
        return "verified_sec_wordmark_fallback"
    return "massive_logo_fallback"


def fetch_sec_asset(url: str, *, user_agent: str, timeout_seconds: int, max_bytes: int) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not (parsed.hostname or "").lower().endswith("sec.gov"):
        raise ValueError("SEC presentation asset URL must be an https://*.sec.gov URL")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=max(1, int(timeout_seconds))) as response:
                length = int(response.headers.get("Content-Length") or 0)
                if length > max_bytes:
                    raise ValueError("SEC presentation asset exceeds configured byte limit")
                content = response.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise ValueError("SEC presentation asset exceeds configured byte limit")
                if not content:
                    raise ValueError("SEC presentation asset is empty")
                return content
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(0.5 * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def image_dimensions_and_mime(content: bytes, declared_mime: str) -> tuple[int | None, int | None, str]:
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        width, height = struct.unpack(">II", content[16:24])
        return width, height, "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")) and len(content) >= 10:
        width, height = struct.unpack("<HH", content[6:10])
        return width, height, "image/gif"
    if content.startswith(b"\xff\xd8"):
        width, height = jpeg_dimensions(content)
        return width, height, "image/jpeg"
    prefix = content[:2048].decode("utf-8", errors="ignore")
    if "<svg" in prefix.lower():
        width = svg_dimension(prefix, "width")
        height = svg_dimension(prefix, "height")
        if (width is None or height is None) and (match := re.search(r"viewBox\s*=\s*['\"]\s*[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)", prefix, flags=re.I)):
            width, height = int(float(match.group(1))), int(float(match.group(2)))
        return width, height, "image/svg+xml"
    return None, None, declared_mime or "application/octet-stream"


def jpeg_dimensions(content: bytes) -> tuple[int | None, int | None]:
    offset = 2
    while offset + 9 < len(content):
        if content[offset] != 0xFF:
            offset += 1
            continue
        marker = content[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(content):
            break
        segment_length = int.from_bytes(content[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(content):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(content[offset + 3 : offset + 5], "big")
            width = int.from_bytes(content[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    return None, None


def svg_dimension(text: str, attribute: str) -> int | None:
    match = re.search(rf"\b{attribute}\s*=\s*['\"]\s*([\d.]+)", text, flags=re.I)
    return int(float(match.group(1))) if match else None


def suffix_for_mime(mime: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/svg+xml": ".svg"}.get(mime, ".bin")


def normalize_token_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def insert_rows(client: ClickHouseHttpClient, database: str, table_name: str, rows: list[dict[str, Any]], batch_size: int) -> int:
    if not rows:
        return 0
    written = 0
    for start in range(0, len(rows), max(1, int(batch_size))):
        batch = rows[start : start + max(1, int(batch_size))]
        body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in batch)
        client.execute(f"INSERT INTO {quote_ident(database)}.{quote_ident(table_name)} FORMAT JSONEachRow\n{body}")
        written += len(batch)
    return written


def query_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    payload = client.execute(sql.strip().rstrip(";") + "\nFORMAT JSONEachRow").strip()
    return [json.loads(line) for line in payload.splitlines() if line.strip()]


def stable_id(prefix: str, key: str) -> str:
    return f"{prefix}:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


def now64() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def dt64(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip().replace("Z", "+00:00").replace(" ", "T")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _run_id(reason: str) -> str:
    safe_reason = re.sub(r"[^a-z0-9]+", "_", reason.lower()).strip("_") or "refresh"
    return f"reference_gateway_presentation_{safe_reason}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
