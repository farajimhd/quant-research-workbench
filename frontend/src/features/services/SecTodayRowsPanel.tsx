import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";

import { api } from "../../api/client";
import { Modal } from "../../app/components/Modal";
import { displayName, formatCompactNumber } from "../../app/format";
import { openTickerChartsQuotes } from "../../app/tickerNavigation";
import type { SecDetailPayload, SecTodayRow, SecTodayRowsState, SecTodaySort } from "./secContracts";
import { SecFilingDetailModal } from "./SecFilingDetailModal";
import {
  secActivityStatus,
  secDisplayStatus,
  secDocumentTextLabel,
  secTickerSubLabel,
  secTickerTitle,
  secTodayFilteredRows,
  secTodayRowTone,
  secXbrlLabel,
} from "./secFilingPresentation";
import { ServicePanel as Panel } from "./ServicePanel";
import { ServiceTableTimeCell } from "./ServiceTableTimeCell";
import { formatLogTime, tableRowRecencyClass } from "./time";
import { workStatusClass } from "./workPresentation";

export function SecTodayRowsPanel({ onSortChange, state }: { onSortChange: (sort: SecTodaySort) => void; state: SecTodayRowsState }) {
  const [detail, setDetail] = useState<SecDetailPayload | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRow, setSelectedRow] = useState<SecTodayRow | null>(null);
  const { rows, summary } = state;
  const filteredRows = useMemo(() => secTodayFilteredRows(rows, searchQuery), [rows, searchQuery]);
  const showingLabel = summary.totalFilings > summary.loadedRows
    ? `Showing ${formatCompactNumber(summary.loadedRows)} of ${formatCompactNumber(summary.totalFilings)} filings`
    : summary.feedParticipantRows
      ? `${formatCompactNumber(summary.totalFilings)} filings + ${formatCompactNumber(summary.feedParticipantRows)} feed participants loaded`
      : `${formatCompactNumber(summary.totalFilings)} filings loaded`;
  const searchLabel = searchQuery.trim()
    ? `Filtered ${formatCompactNumber(filteredRows.length)} of ${formatCompactNumber(summary.loadedRows)} loaded filings`
    : showingLabel;

  async function openFiling(row: SecTodayRow) {
    setSelectedRow(row);
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    try {
      const detailCik = row.filingParentCik || row.cik;
      setDetail(await api<SecDetailPayload>(`/api/services/sec/detail/${encodeURIComponent(detailCik)}/${encodeURIComponent(row.accessionNumber)}`));
    } catch (exc) {
      setDetailError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setDetailLoading(false);
    }
  }

  function closeDetail() {
    setSelectedRow(null);
    setDetail(null);
    setDetailError("");
  }

  return (
    <Panel className="sec-today-panel" title="Today's SEC Filings And XBRL">
      <div className="news-today-searchbar sec-today-searchbar">
        <label className="news-today-search-field">
          <Search size={14} />
          <input onChange={(event) => setSearchQuery(event.target.value)} placeholder="Search ticker, CIK, issuer, company, SEC form, accession, document, text status, or XBRL tag" type="search" value={searchQuery} />
          {searchQuery ? <button aria-label="Clear SEC filing search" onClick={() => setSearchQuery("")} type="button"><X size={14} /></button> : null}
        </label>
        <div className="news-today-compact-stats">
          <span><small>Filings</small><strong>{formatCompactNumber(summary.totalFilings)}</strong></span>
          <span><small>Loaded</small><strong>{formatCompactNumber(summary.loadedRows)}</strong></span>
          {summary.feedParticipantRows ? <span><small>Feed participants</small><strong>{formatCompactNumber(summary.feedParticipantRows)}</strong></span> : null}
          <span><small>With Text</small><strong>{formatCompactNumber(summary.withText)}</strong></span>
          <span><small>With XBRL</small><strong>{formatCompactNumber(summary.withXbrl)}</strong></span>
          <span><small>Latest</small><strong>{summary.latest ? formatLogTime(summary.latest) : "-"}</strong></span>
        </div>
      </div>
      <div className="news-today-meta">
        <span>{state.windowStartUtc ? `Window ${formatLogTime(state.windowStartUtc)} -> ${formatLogTime(state.windowEndUtc)}` : "Today, market timezone"}</span>
        {state.error ? <strong>{state.error}</strong> : <strong className={state.loading ? "inline-loading-message" : undefined}>{state.loading ? <span className="loading-spinner" aria-hidden="true" /> : null}{state.loading ? "Loading SEC filing rows..." : searchLabel}</strong>}
      </div>
      <div className="news-today-table-wrap sec-today-table-wrap">
        <table className="news-today-table sec-today-table">
          <thead><tr>
            <th aria-sort={state.sort === "desc" ? "descending" : "ascending"}><button className="news-today-sort-button" onClick={() => onSortChange(state.sort === "desc" ? "asc" : "desc")} type="button"><span>Time</span><strong>{state.sort === "desc" ? "Newest" : "Oldest"}</strong></button></th>
            <th>Ticker</th><th>CIK</th><th title="SEC filing form type, such as 10-K, 8-K, 424B2, or FWP. This is not the ticker.">SEC Form</th><th>Filing</th><th>Docs / Text</th><th>XBRL</th><th>Status</th>
          </tr></thead>
          <tbody>
            {(filteredRows.length ? filteredRows : [null]).map((row, index) => row ? (
              <tr className={`${secTodayRowTone(row)} ${tableRowRecencyClass(row.feedUpdatedAtUtc || row.acceptedAtUtc)}`} key={`${row.rowOrigin}-${row.cik}-${row.accessionNumber}-${index}`}>
                <ServiceTableTimeCell className="news-today-time-cell" value={row.feedUpdatedAtUtc || row.acceptedAtUtc} />
                <td className="sec-filing-ticker-cell" title={secTickerTitle(row)}><div className="news-today-cell-stack">{row.primaryTicker ? <button aria-label={`Open ${row.primaryTicker} Charts & Quotes in a new tab`} className="ticker-charts-quotes-link" onClick={() => openTickerChartsQuotes(row.primaryTicker)} type="button"><strong>{row.primaryTicker}</strong></button> : <strong className="muted-value">-</strong>}<span>{secTickerSubLabel(row)}</span></div></td>
                <td title={row.cik}><div className="news-today-cell-stack"><strong>{row.cik || "-"}</strong><span>{row.rowOrigin === "sec_gateway_feed_participant" ? "feed participant" : row.issuerName || row.issuerId || row.accessionNumberCompact || "-"}</span></div></td>
                <td title={row.formType}><span className="sec-form-chip">{row.formType || "-"}</span></td>
                <td className="news-today-title-cell sec-filing-title-cell" title={`${row.companyName} ${row.accessionNumber}`}><button className="table-primary-link" onClick={() => void openFiling(row)} type="button"><span className="news-today-cell-stack"><strong>{row.companyName || "Unknown SEC filer"}</strong><span>{row.accessionNumber} / {row.primaryDocument || row.sourceFileName || "filing parent"}</span></span></button></td>
                <td title={secDocumentTextLabel(row)}>{secDocumentTextLabel(row)}</td>
                <td title={secXbrlLabel(row)}>{secXbrlLabel(row)}</td>
                <td><span className={`service-work-status ${workStatusClass(secActivityStatus(row))}`}>{displayName(secDisplayStatus(row))}</span></td>
              </tr>
            ) : <tr key={`empty-${index}`}><td colSpan={8}>{state.loading ? <span className="inline-loading-message"><span className="loading-spinner" aria-hidden="true" />Loading today's SEC filing rows...</span> : searchQuery.trim() ? "No loaded SEC filing rows match this search." : "No SEC filing rows found for today's market date."}</td></tr>)}
          </tbody>
        </table>
      </div>
      {selectedRow ? <Modal className="sec-filing-detail-modal-panel" onClose={closeDetail} title="SEC Filing Detail"><SecFilingDetailModal detail={detail} error={detailError} loading={detailLoading} row={selectedRow} /></Modal> : null}
    </Panel>
  );
}
