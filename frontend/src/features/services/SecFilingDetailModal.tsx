import { useEffect, useRef } from "react";

import { DataTable } from "../../app/components/DataTable";
import { displayName, formatCompactNumber } from "../../app/format";
import type { SecDetailPayload, SecTodayRow } from "./secContracts";
import {
  secIdentityTickers,
  secMappingConfidenceLabel,
  secReadableDocumentRows,
  secReadableTextRows,
  secTextCharCount,
  secTextMetadataRow,
} from "./secFilingPresentation";
import { numericMetric, stringMetric, uniqueStringSample } from "./metrics";
import { ServiceMetadataTable } from "./ServiceMetadataTable";
import { ServiceTimeCard } from "./ServiceTimeCard";
import { EXCHANGE_TIME_ZONE, VANCOUVER_TIME_ZONE } from "./time";
import { formatValue, isRecord, normalizeRow } from "./workPresentation";

export function SecFilingDetailModal({ detail, error, loading, row }: { detail: SecDetailPayload | null; error: string; loading: boolean; row: SecTodayRow }) {
  const detailScrollRef = useRef<HTMLDivElement | null>(null);
  const filingRow = isRecord(detail?.filing_row) ? detail.filing_row : {};
  const documentRows = Array.isArray(detail?.document_rows) ? detail.document_rows.filter(isRecord) : [];
  const textRows = Array.isArray(detail?.text_rows) ? detail.text_rows.filter(isRecord) : [];
  const companyFactRows = Array.isArray(detail?.company_fact_rows) ? detail.company_fact_rows.filter(isRecord) : [];
  const frameRows = Array.isArray(detail?.frame_rows) ? detail.frame_rows.filter(isRecord) : [];
  const identityRows = Array.isArray(detail?.identity_rows) ? detail.identity_rows.filter(isRecord) : [];
  const identitySummary = isRecord(detail?.identity_summary) ? detail.identity_summary : {};
  const companyName = stringMetric(filingRow, ["company_name"]) || row.companyName || "Unknown SEC filer";
  const formType = stringMetric(filingRow, ["form_type"]) || row.formType || "-";
  const accession = stringMetric(filingRow, ["accession_number"]) || row.accessionNumber;
  const acceptedAt = row.acceptedAtUtc || stringMetric(filingRow, ["accepted_at_utc"]);
  const primaryDocumentUrl = stringMetric(filingRow, ["primary_document_url"]) || row.primaryDocumentUrl;
  const filingDetailUrl = stringMetric(filingRow, ["filing_detail_url"]) || row.filingDetailUrl;
  const primaryDocument = stringMetric(filingRow, ["primary_document"]) || row.primaryDocument;
  const identityTickers = secIdentityTickers(identitySummary, row, identityRows);
  const primaryTicker = stringMetric(identitySummary, ["primary_ticker"]) || row.primaryTicker || identityTickers[0] || "";
  const readableTextRows = secReadableTextRows(textRows);
  const readableDocumentRows = secReadableDocumentRows(documentRows, textRows);
  const primaryTextKind = readableTextRows.length === 1
    ? readableTextRows[0].label
    : readableTextRows.length
      ? `${formatCompactNumber(readableTextRows.length)} extracted text parts`
      : "No extracted text";
  const primaryTextChars = readableTextRows.reduce((total, item) => total + item.charCount, 0);
  const documentTypeSample = uniqueStringSample([
    ...row.documentTypeSample,
    ...documentRows.map((item) => stringMetric(item, ["document_type", "type", "description"])),
  ], 8);
  const xbrlFactTags = uniqueStringSample([
    ...row.xbrlFactTagSample,
    ...companyFactRows.map((item) => stringMetric(item, ["tag", "concept", "name"])),
  ], 10);
  const xbrlFrameTags = uniqueStringSample([
    ...row.xbrlFrameTagSample,
    ...frameRows.map((item) => stringMetric(item, ["tag", "concept", "name"])),
  ], 10);
  const relationStats = [
    { label: "Documents", value: documentRows.length || row.documentRows },
    { label: "Text rows", value: textRows.length || row.textRows },
    { label: "XBRL facts", value: companyFactRows.length || row.xbrlFactRows },
    { label: "Frame rows", value: frameRows.length || row.xbrlFrameRows },
  ];
  const marketIdentityFacts = [
    { label: "Ticker", value: primaryTicker || "-" },
    { label: "All linked tickers", value: identityTickers.length ? identityTickers.join(", ") : "-" },
    { label: "Exchange", value: stringMetric(identitySummary, ["primary_exchange_code"]) || row.primaryExchangeCode || row.exchangeCodeSample.join(", ") || "-" },
    { label: "Currency", value: stringMetric(identitySummary, ["primary_currency_code"]) || row.primaryCurrencyCode || "-" },
    { label: "IBKR conid", value: stringMetric(identitySummary, ["primary_ibkr_conid"]) || row.primaryIbkrConid || "-" },
    { label: "Issuer", value: stringMetric(identitySummary, ["issuer_name"]) || row.issuerName || companyName },
    { label: "Legal name", value: stringMetric(identitySummary, ["issuer_legal_name"]) || row.issuerLegalName || "-" },
    { label: "Domicile", value: stringMetric(identitySummary, ["issuer_domicile_country_code"]) || row.issuerDomicileCountryCode || "-" },
    { label: "State", value: stringMetric(identitySummary, ["issuer_state_of_incorporation"]) || row.issuerStateOfIncorporation || "-" },
    { label: "Sector", value: stringMetric(identitySummary, ["issuer_sector"]) || row.issuerSector || "-" },
    { label: "Industry", value: stringMetric(identitySummary, ["issuer_industry"]) || row.issuerIndustry || "-" },
    { label: "SIC", value: [stringMetric(identitySummary, ["issuer_sic_code"]) || row.issuerSicCode, stringMetric(identitySummary, ["issuer_sic_description"]) || row.issuerSicDescription].filter(Boolean).join(" - ") || "-" },
    { label: "Security", value: stringMetric(identitySummary, ["security_name"]) || row.securityName || "-" },
    { label: "Security type", value: stringMetric(identitySummary, ["security_type"]) || row.securityType || row.securityProductType || "-" },
    { label: "Bridge rows", value: formatCompactNumber(numericMetric(identitySummary, ["identity_bridge_count"]) || row.identityBridgeCount || identityRows.length) },
    { label: "Mapping", value: row.mappingStatusSample.length ? row.mappingStatusSample.join(", ") : stringMetric(identitySummary, ["mapping_status_sample"]) || "-" },
    { label: "Ambiguity", value: row.ambiguityStatusSample.length ? row.ambiguityStatusSample.join(", ") : stringMetric(identitySummary, ["ambiguity_status_sample"]) || "-" },
    { label: "Confidence", value: secMappingConfidenceLabel(numericMetric(identitySummary, ["max_mapping_confidence"]) || row.maxMappingConfidence) },
  ];
  const marketLabel = [stringMetric(identitySummary, ["primary_exchange_code"]) || row.primaryExchangeCode, stringMetric(identitySummary, ["primary_currency_code"]) || row.primaryCurrencyCode].filter(Boolean).join(" / ") || "No listed market";
  const heroIdentityFacts = marketIdentityFacts.filter((item) => ["All linked tickers", "IBKR conid", "SIC", "Security type", "Confidence"].includes(item.label));
  const filingFacts = [
    { label: "Ticker", value: primaryTicker || "-" },
    { label: "CIK", value: stringMetric(filingRow, ["cik"]) || row.cik },
    { label: "Accession", value: accession },
    { label: "Form", value: formType },
    { label: "Filing date", value: stringMetric(filingRow, ["filing_date"]) || row.filingDate || "-" },
    { label: "Report date", value: stringMetric(filingRow, ["report_date"]) || row.reportDate || "-" },
    { label: "Accepted source", value: stringMetric(filingRow, ["accepted_at_source"]) || "-" },
    { label: "Raw acceptance", value: stringMetric(filingRow, ["acceptance_datetime_raw"]) || row.acceptanceDatetimeRaw || "-" },
    { label: "Text status", value: stringMetric(filingRow, ["text_status"]) || row.textStatus || "-" },
    { label: "Primary document", value: primaryDocument || "-" },
    { label: "Source file", value: stringMetric(filingRow, ["source_file_name"]) || row.sourceFileName || "-" },
  ];
  const filingSnapshotFacts = filingFacts.filter((item) => ["CIK", "Accession", "Filing date", "Report date", "Accepted source", "Raw acceptance", "Text status"].includes(item.label));
  const detailErrors = Array.isArray(detail?.detail_errors) ? detail.detail_errors.filter(isRecord) : [];
  const partialDetailMessage = detailErrors.length
    ? `Loaded filing parent, but ${detailErrors.length} related detail ${detailErrors.length === 1 ? "query" : "queries"} failed: ${detailErrors.map((item) => stringMetric(item, ["part"]) || "related data").slice(0, 4).join(", ")}${detailErrors.length > 4 ? ", ..." : ""}.`
    : "";

  useEffect(() => {
    detailScrollRef.current?.scrollTo({ left: 0, top: 0 });
  }, [accession, primaryDocument]);

  return (
    <div className="sec-filing-detail">
      <article className="sec-filing-hero-card">
        <div className="sec-filing-hero-main">
          <div className="sec-filing-meta-line">
            <span className="sec-provider-pill">SEC</span>
            {primaryTicker ? <span>{primaryTicker}</span> : null}
            <span>{formType}</span>
            <span>{row.xbrlFactRows + row.xbrlFrameRows > 0 ? "XBRL linked" : "Filing parent"}</span>
            <span>{row.textRows > 0 ? "Text extracted" : "No text rows"}</span>
          </div>
          <h3>{companyName}</h3>
          <p>{accession} / {primaryDocument || row.sourceFileName || "filing parent"}</p>
          <section className="sec-filing-hero-identity" aria-label="SEC filing market identity">
            <div className="sec-filing-hero-ticker-card"><span>Market identity</span><strong>{primaryTicker || "No ticker"}</strong><small>{marketLabel}</small></div>
            <dl className="sec-filing-hero-identity-list">
              {heroIdentityFacts.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}
            </dl>
          </section>
        </div>
        <div className="sec-filing-hero-actions">
          {primaryDocumentUrl ? <a href={primaryDocumentUrl} rel="noreferrer" target="_blank">Primary document</a> : null}
          {filingDetailUrl ? <a href={filingDetailUrl} rel="noreferrer" target="_blank">SEC filing page</a> : null}
        </div>
        <div className="news-full-time-grid sec-filing-time-grid">
          <ServiceTimeCard label="Market time" timeZone={EXCHANGE_TIME_ZONE} value={acceptedAt} />
          <ServiceTimeCard label="Vancouver" timeZone={VANCOUVER_TIME_ZONE} value={acceptedAt} />
          <ServiceTimeCard label="UTC" timeZone="UTC" value={acceptedAt} />
        </div>
        <div className="sec-filing-stat-grid">
          {relationStats.map((item) => <div key={item.label}><span>{item.label}</span><strong>{formatCompactNumber(item.value)}</strong></div>)}
        </div>
      </article>
      <div className="sec-filing-detail-scroll" ref={detailScrollRef}>
        {loading ? <div className="news-full-detail-notice inline-loading-message"><span className="loading-spinner" aria-hidden="true" />Loading complete SEC filing row from ClickHouse...</div> : null}
        {error ? <div className="news-full-detail-notice error">{error}</div> : null}
        {partialDetailMessage ? <div className="news-full-detail-notice warning">{partialDetailMessage}</div> : null}
        <section className="sec-filing-reader-layout">
          <article className="sec-filing-reader-card">
            <header><div><span>Readable filing text</span><h4>{primaryTextKind}</h4></div><strong>{primaryTextChars ? `${formatCompactNumber(primaryTextChars)} chars` : "No text"}</strong></header>
            <div className="sec-filing-readable-body">
              {readableTextRows.length ? readableTextRows.map((textPart, partIndex) => (
                <section className="sec-filing-readable-part" key={`${textPart.documentId || textPart.sha256 || textPart.label}-${partIndex}`}>
                  <div className="sec-filing-readable-part-header"><strong>{textPart.label}</strong><span>{formatCompactNumber(textPart.charCount)} chars</span><small>{textPart.documentId || textPart.archiveMember || textPart.sha256 || "-"}</small></div>
                  {textPart.blocks.map((block, blockIndex) => <p key={`${partIndex}-${blockIndex}-${block.slice(0, 24)}`}>{block}</p>)}
                </section>
              )) : <p className="sec-filing-empty-note">No filing text was returned for this filing yet.</p>}
            </div>
          </article>
          <aside className="sec-filing-context-panel">
            <section className="sec-filing-side-card"><h4>Filing Snapshot</h4><dl className="sec-filing-context-list">{filingSnapshotFacts.map((item) => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></section>
            <section className="sec-filing-side-card"><h4>Document Signals</h4><div className="sec-filing-chip-cloud">{documentTypeSample.length ? documentTypeSample.map((item) => <span key={item}>{item}</span>) : <em>No document type sample.</em>}</div></section>
            <section className="sec-filing-side-card"><h4>XBRL Tags</h4><div className="sec-filing-chip-cloud">{[...xbrlFactTags, ...xbrlFrameTags].length ? [...xbrlFactTags, ...xbrlFrameTags].map((item) => <span key={item}>{item}</span>) : <em>No XBRL tags linked.</em>}</div></section>
            <section className="sec-filing-side-card"><h4>Text Rows</h4><div className="sec-filing-text-row-list">
              {textRows.length ? textRows.map((textRow, index) => <div key={`${stringMetric(textRow, ["document_id", "filing_text_id"])}-${index}`}><strong>{displayName(stringMetric(textRow, ["text_kind", "kind"]) || `Text row ${index + 1}`)}</strong><span>{formatCompactNumber(secTextCharCount(textRow))} chars</span><small>{stringMetric(textRow, ["document_id", "filing_document_id", "source_file_name"]) || "-"}</small></div>) : <p>No text rows returned.</p>}
            </div></section>
          </aside>
        </section>
        <section className="sec-filing-document-inventory-card">
          <header><div><span>Document inventory</span><h4>Readable sec_filing_document_v2 rows</h4><p>Every saved filing document row is shown with extraction status, source artifact, content metadata, and linked text coverage.</p></div><strong>{formatCompactNumber(readableDocumentRows.length)} docs</strong></header>
          {readableDocumentRows.length ? <div className="sec-filing-document-list">{readableDocumentRows.map((doc) => (
            <article className="sec-filing-document-card" key={doc.key}>
              <div className="sec-filing-document-card-head"><div><span>#{doc.sequenceLabel}</span><h5>{doc.title}</h5>{doc.description ? <p>{doc.description}</p> : null}</div><div className="sec-filing-document-badges">{doc.badges.map((badge) => <span key={badge}>{badge}</span>)}<span className={doc.textStatusClass}>{doc.textStatusLabel}</span></div></div>
              <dl className="sec-filing-document-facts">{doc.facts.map((fact) => <div className={fact.wide ? "wide" : undefined} key={fact.label}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>
              <div className="sec-filing-document-actions">{doc.documentUrl ? <a href={doc.documentUrl} rel="noreferrer" target="_blank">Open SEC document</a> : <span>No document URL stored</span>}{doc.linkedTextRows ? <span>{formatCompactNumber(doc.linkedTextRows)} linked text row{doc.linkedTextRows === 1 ? "" : "s"}</span> : <span>No linked text row</span>}</div>
            </article>
          ))}</div> : <p className="sec-filing-empty-note">No document rows were returned for this filing.</p>}
        </section>
        <section className="sec-filing-data-sections">
          <header className="sec-filing-data-section-header"><div><span>Technical row data</span><strong>Documents, XBRL, market bridge, and raw filing parent</strong></div><p>Collapsed by default so the readable filing stays primary. Open a section when you need raw rows.</p></header>
          <TechnicalRowsDetails label="Filing Documents" rows={documentRows} empty="No document rows returned for this filing." />
          <details><summary><span>Filing Text Rows</span><strong>{formatCompactNumber(textRows.length)}</strong></summary><div className="sec-filing-data-table-wrap"><DataTable empty="No text rows returned for this filing." fitToContent rows={textRows.map(secTextMetadataRow).map(normalizeRow)} /></div></details>
          <TechnicalRowsDetails label="XBRL Company Facts" rows={companyFactRows} empty="No XBRL company fact rows returned for this filing." />
          <TechnicalRowsDetails label="XBRL Frame Observations" rows={frameRows} empty="No XBRL frame rows returned for this filing." />
          <TechnicalRowsDetails label="SEC Market Bridge And Listing Identity" rows={identityRows} empty="No SEC market bridge rows returned for this filing CIK." />
          <details><summary><span>Filing Parent Row</span><strong>{formatCompactNumber(filingFacts.length)}</strong></summary><ServiceMetadataTable rows={Object.entries(filingRow).map(([key, value]) => ({ key, value: formatValue(key, value) }))} /></details>
        </section>
      </div>
    </div>
  );
}

function TechnicalRowsDetails({ empty, label, rows }: { empty: string; label: string; rows: Record<string, unknown>[] }) {
  return (
    <details>
      <summary><span>{label}</span><strong>{formatCompactNumber(rows.length)}</strong></summary>
      <div className="sec-filing-data-table-wrap"><DataTable empty={empty} fitToContent rows={rows.map(normalizeRow)} /></div>
    </details>
  );
}
