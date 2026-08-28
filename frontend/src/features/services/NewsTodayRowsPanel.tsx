import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";

import { api } from "../../api/client";
import { Modal } from "../../app/components/Modal";
import { formatCompactNumber } from "../../app/format";
import { openTickerChartsQuotes } from "../../app/tickerNavigation";
import type { NewsDetailPayload, NewsTodayRow, NewsTodayRowsState, NewsTodaySort } from "./newsContracts";
import { NewsTodayDetailModal } from "./NewsTodayDetailModal";
import { newsTodayFilteredRows, newsTodayFlagChips, newsTodayRowTone, newsTodayTextLabel, newsTodayTickerChips, newsTodayTickerLabel } from "./newsTodayPresentation";
import { ServicePanel as Panel } from "./ServicePanel";
import { ServiceTableTimeCell } from "./ServiceTableTimeCell";
import { formatLogTime, tableRowRecencyClass } from "./time";

export function NewsTodayRowsPanel({ onSortChange, state }: { onSortChange: (sort: NewsTodaySort) => void; state: NewsTodayRowsState }) {
  const [detail, setDetail] = useState<NewsDetailPayload | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedRow, setSelectedRow] = useState<NewsTodayRow | null>(null);
  const { rows, summary } = state;
  const filteredRows = useMemo(() => newsTodayFilteredRows(rows, searchQuery), [rows, searchQuery]);
  const showingLabel = summary.totalRows > summary.loadedRows ? `Showing ${formatCompactNumber(summary.loadedRows)} of ${formatCompactNumber(summary.totalRows)} rows` : `${formatCompactNumber(summary.totalRows)} rows loaded`;
  const searchLabel = searchQuery.trim() ? `Filtered ${formatCompactNumber(filteredRows.length)} of ${formatCompactNumber(summary.loadedRows)} loaded rows` : showingLabel;

  async function openNews(row: NewsTodayRow) {
    setSelectedRow(row);
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    try {
      setDetail(await api<NewsDetailPayload>(`/api/services/news/detail/${encodeURIComponent(row.canonicalNewsId)}`));
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
    <Panel className="news-today-panel" title="Today's Inserted News">
      <div className="news-today-searchbar">
        <label className="news-today-search-field"><Search size={14} /><input onChange={(event) => setSearchQuery(event.target.value)} placeholder="Search ticker, title, source, flag, author, URL, or article id" type="search" value={searchQuery} />{searchQuery ? <button aria-label="Clear inserted news search" onClick={() => setSearchQuery("")} type="button"><X size={14} /></button> : null}</label>
        <div className="news-today-compact-stats"><span><small>Today</small><strong>{formatCompactNumber(summary.totalRows)}</strong></span><span><small>Loaded</small><strong>{formatCompactNumber(summary.loadedRows)}</strong></span><span><small>1 ticker</small><strong>{formatCompactNumber(summary.oneTickerRows)}</strong></span><span><small>Latest</small><strong>{summary.latest ? formatLogTime(summary.latest) : "-"}</strong></span></div>
      </div>
      <div className="news-today-meta"><span>{state.windowStartUtc ? `Window ${formatLogTime(state.windowStartUtc)} -> ${formatLogTime(state.windowEndUtc)}` : "Today, market timezone"}</span>{state.error ? <strong>{state.error}</strong> : <strong className={state.loading ? "inline-loading-message" : undefined}>{state.loading ? <span className="loading-spinner" aria-hidden="true" /> : null}{state.loading ? "Loading rows..." : searchLabel}</strong>}</div>
      <div className="news-today-table-wrap"><table className="news-today-table">
        <thead><tr><th aria-sort={state.sort === "desc" ? "descending" : "ascending"}><button className="news-today-sort-button" onClick={() => onSortChange(state.sort === "desc" ? "asc" : "desc")} type="button"><span>Time</span><strong>{state.sort === "desc" ? "Newest" : "Oldest"}</strong></button></th><th>Tickers</th><th>Title</th><th>Text</th><th>Flags</th><th>Source</th></tr></thead>
        <tbody>{(filteredRows.length ? filteredRows : [null]).map((row, index) => row ? (
          <tr className={`${newsTodayRowTone(row)} ${tableRowRecencyClass(row.publishedAtUtc)}`} key={`${row.canonicalNewsId}-${index}`}>
            <ServiceTableTimeCell className="news-today-time-cell" value={row.publishedAtUtc} />
            <td className="news-today-ticker-cell" title={newsTodayTickerLabel(row)}><div className="news-today-chip-row">{newsTodayTickerChips(row).map((ticker) => <button aria-label={`Open ${ticker} Charts & Quotes in a new tab`} className="ticker-charts-quotes-link" key={ticker} onClick={() => openTickerChartsQuotes(ticker)} type="button">{ticker}</button>)}</div></td>
            <td className="news-today-title-cell" title={row.title}><button className="table-primary-link" onClick={() => void openNews(row)} type="button"><span className="news-today-cell-stack"><strong>{row.title || row.normalizedTitle || "-"}</strong><span>{row.textPreview || row.normalizedTitle || "No text preview reported."}</span></span></button></td>
            <td className="news-today-text-cell" title={newsTodayTextLabel(row)}>{newsTodayTextLabel(row)}</td>
            <td className="news-today-flag-cell" title={row.contentQualityFlags.join(", ")}><div className="news-today-chip-row muted">{newsTodayFlagChips(row).map((flag) => <span key={flag}>{flag}</span>)}</div></td>
            <td className="news-today-source-cell" title={row.articleUrl || row.urlDomain}><div className="news-today-cell-stack"><strong>{row.urlDomain || "-"}</strong><span>{row.author || row.channels.slice(0, 2).join(", ") || "Benzinga"}</span></div></td>
          </tr>
        ) : <tr key={`empty-${index}`}><td colSpan={6}>{state.loading ? <span className="inline-loading-message"><span className="loading-spinner" aria-hidden="true" />Loading today's inserted news rows...</span> : searchQuery.trim() ? "No loaded news rows match this search." : "No inserted news rows found for today's market date."}</td></tr>)}</tbody>
      </table></div>
      {selectedRow ? <Modal className="news-full-detail-modal-panel" onClose={closeDetail} title="Inserted News Detail"><NewsTodayDetailModal detail={detail} error={detailError} loading={detailLoading} row={selectedRow} /></Modal> : null}
    </Panel>
  );
}
