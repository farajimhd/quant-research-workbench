import { displayName, formatCompactNumber } from "../../app/format";
import { DebugObjectBlock } from "./DebugObjectBlock";
import type {
  NewsCoverageHistoryRow,
  NewsEnrichmentArticleRow,
  NewsEnrichmentHistoryRow,
  NewsPublishHistoryRow,
} from "./newsWorkContracts";
import { formatSeconds, newsEnrichmentArticleUrlLabel, shortPollId } from "./newsWorkPresentation";
import { formatLogTime } from "./time";
import { workStatusClass } from "./workPresentation";

export function NewsPublishDetailModal({ row }: { row: NewsPublishHistoryRow }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="news-publish-detail">
      <div className={`news-publish-detail-status ${statusClass}`}>
        <span>{displayName(row.event)}</span>
        <strong>{row.title || "Untitled news row"}</strong>
      </div>
      <dl className="service-log-detail-grid">
        <div>
          <dt>Logged At</dt>
          <dd>{row.time ? formatLogTime(row.time) : "-"}</dd>
        </div>
        <div>
          <dt>Published At</dt>
          <dd>{row.publishedAt ? formatLogTime(row.publishedAt) : "-"}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{displayName(row.coverageMode)}</dd>
        </div>
        <div>
          <dt>Tickers</dt>
          <dd>{row.tickers || "-"}</dd>
        </div>
        <div>
          <dt>Inserted</dt>
          <dd>{formatCompactNumber(row.insertedRows)}</dd>
        </div>
        <div>
          <dt>Skipped</dt>
          <dd>{formatCompactNumber(row.skippedRows)}</dd>
        </div>
        <div>
          <dt>Ticker Links</dt>
          <dd>{formatCompactNumber(row.tickerRows)}</dd>
        </div>
        <div>
          <dt>Poll ID</dt>
          <dd>{row.pollId || "-"}</dd>
        </div>
        <div>
          <dt>Provider Article ID</dt>
          <dd>{row.providerArticleId || "-"}</dd>
        </div>
        <div>
          <dt>Canonical News ID</dt>
          <dd>{row.canonicalNewsId || "-"}</dd>
        </div>
        <div className="wide">
          <dt>Enrichment</dt>
          <dd>{row.enrichment || "-"}</dd>
        </div>
        <div className="wide">
          <dt>Quality Flags</dt>
          <dd>{row.qualityFlags.length ? row.qualityFlags.join(", ") : "-"}</dd>
        </div>
      </dl>
    </div>
  );
}

export function NewsEnrichmentDetailModal({ row }: { row: NewsEnrichmentHistoryRow }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="news-publish-detail">
      <div className={`news-publish-detail-status ${statusClass}`}>
        <span>{displayName(row.status)}</span>
        <strong>{row.title || "Background enrichment event"}</strong>
      </div>
      <dl className="service-log-detail-grid">
        <div>
          <dt>Logged At</dt>
          <dd>{row.time ? formatLogTime(row.time) : "-"}</dd>
        </div>
        <div>
          <dt>Event</dt>
          <dd>{displayName(row.event)}</dd>
        </div>
        <div>
          <dt>Mode</dt>
          <dd>{displayName(row.mode)}</dd>
        </div>
        <div>
          <dt>Poll ID</dt>
          <dd>{row.pollId || "-"}</dd>
        </div>
        <div>
          <dt>Worker</dt>
          <dd>{row.worker || "-"}</dd>
        </div>
        <div>
          <dt>Queue Size</dt>
          <dd>{formatCompactNumber(row.queueSize)}</dd>
        </div>
        <div>
          <dt>Articles</dt>
          <dd>{formatCompactNumber(row.articleCount)}</dd>
        </div>
        <div>
          <dt>Fetch Tasks</dt>
          <dd>{formatCompactNumber(row.fetchTasks)}</dd>
        </div>
        <div>
          <dt>Extracted URL Text</dt>
          <dd>{formatCompactNumber(row.enrichedUrls)}</dd>
        </div>
        <div>
          <dt>Failed Articles</dt>
          <dd>{formatCompactNumber(row.failedArticles)}</dd>
        </div>
        <div>
          <dt>Runtime</dt>
          <dd>{row.wallSeconds ? formatSeconds(row.wallSeconds) : "-"}</dd>
        </div>
        <div>
          <dt>Provider Article ID</dt>
          <dd>{row.providerArticleId || "-"}</dd>
        </div>
        <div className="wide">
          <dt>News Titles</dt>
          <dd>{row.titleSample.length ? row.titleSample.join(" | ") : "-"}</dd>
        </div>
        <div className="wide">
          <dt>Enrichment URLs</dt>
          <dd>{row.urlSample.length ? row.urlSample.join(" | ") : "-"}</dd>
        </div>
        <div className="wide">
          <dt>Domains</dt>
          <dd>{row.domainSample.length ? row.domainSample.join(", ") : "-"}</dd>
        </div>
        <div className="wide">
          <dt>Detail</dt>
          <dd>{row.detail || "-"}</dd>
        </div>
      </dl>
      {row.items.length ? (
        <section className="news-enrichment-relation-section">
          <div className="news-enrichment-relation-heading">
            <span>Article Relation</span>
            <strong>{formatCompactNumber(row.items.length)} item{row.items.length === 1 ? "" : "s"}</strong>
          </div>
          <div className="news-enrichment-relation-table-wrap">
            <table className="news-enrichment-relation-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>URLs</th>
                  <th>Tickers</th>
                  <th>Provider ID</th>
                  <th>Fetch</th>
                </tr>
              </thead>
              <tbody>
                {row.items.map((item, index) => (
                  <tr key={`${item.canonicalNewsId || item.providerArticleId || item.title}-${index}`}>
                    <td title={item.title}>{item.title || "-"}</td>
                    <td title={item.urlSample.join(" | ") || item.domainSample.join(", ")}>
                      {newsEnrichmentArticleUrlLabel(item)}
                    </td>
                    <td>{item.tickers || "-"}</td>
                    <td title={item.providerArticleId || item.canonicalNewsId}>
                      {item.providerArticleId || shortPollId(item.canonicalNewsId) || "-"}
                    </td>
                    <td>
                      <span className={`service-work-mini-status ${item.requiresEnrichment ? "active" : "idle"}`}>
                        {item.externalFetchStatus ? displayName(item.externalFetchStatus) : item.requiresEnrichment ? "needed" : "not needed"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="news-enrichment-debug-list">
            {row.items.map((item, index) => (
              <NewsEnrichmentArticleDebugCard item={item} key={`${item.canonicalNewsId || item.providerArticleId || item.title}-debug-${index}`} />
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

export function NewsEnrichmentArticleDebugCard({ item }: { item: NewsEnrichmentArticleRow }) {
  return (
    <article className="news-enrichment-debug-card">
      <header>
        <span>{item.tickers || "No ticker"}</span>
        <strong>{item.title || "Untitled enrichment item"}</strong>
      </header>
      <dl className="news-enrichment-debug-meta">
        <div><dt>Provider ID</dt><dd>{item.providerArticleId || "-"}</dd></div>
        <div><dt>Canonical ID</dt><dd>{item.canonicalNewsId || "-"}</dd></div>
        <div><dt>Published</dt><dd>{item.publishedAt ? formatLogTime(item.publishedAt) : "-"}</dd></div>
        <div><dt>URL Count</dt><dd>{formatCompactNumber(item.urlCount)}</dd></div>
        <div><dt>Fetch Status</dt><dd>{item.externalFetchStatus ? displayName(item.externalFetchStatus) : item.requiresEnrichment ? "needed" : "not needed"}</dd></div>
        <div><dt>PDF</dt><dd>{item.hasPdf ? "yes" : "no"}</dd></div>
      </dl>
      <DebugObjectBlock title="URLs And Resolution" value={item.urlResolution} />
      <DebugObjectBlock title="Pre-Enriched Normalized Row" value={item.preEnrichedRow} />
      <DebugObjectBlock title="Raw Provider Payload" value={item.providerPayload} />
    </article>
  );
}

export function NewsCoverageDetailModal({ row }: { row: NewsCoverageHistoryRow }) {
  const statusClass = workStatusClass(row.status);
  return (
    <div className="news-publish-detail">
      <div className={`news-publish-detail-status ${statusClass}`}>
        <span>{displayName(row.status)}</span>
        <strong>{row.stage || "Coverage event"}</strong>
      </div>
      <dl className="service-log-detail-grid">
        <div>
          <dt>Logged At</dt>
          <dd>{row.time ? formatLogTime(row.time) : "-"}</dd>
        </div>
        <div>
          <dt>Event</dt>
          <dd>{displayName(row.event)}</dd>
        </div>
        <div>
          <dt>Coverage Id</dt>
          <dd>{row.coverageId || "-"}</dd>
        </div>
        <div>
          <dt>Stage</dt>
          <dd>{row.stage || "-"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{displayName(row.status)}</dd>
        </div>
        <div>
          <dt>Window Start</dt>
          <dd>{row.startUtc ? formatLogTime(row.startUtc) : "-"}</dd>
        </div>
        <div>
          <dt>Window End</dt>
          <dd>{row.endUtc ? formatLogTime(row.endUtc) : "-"}</dd>
        </div>
        <div>
          <dt>Gaps</dt>
          <dd>{formatCompactNumber(row.gapCount)}</dd>
        </div>
        <div>
          <dt>Chunks</dt>
          <dd>{row.totalChunks ? `${formatCompactNumber(row.chunkCount)}/${formatCompactNumber(row.totalChunks)}` : formatCompactNumber(row.chunkCount)}</dd>
        </div>
        <div>
          <dt>In Flight</dt>
          <dd>{formatCompactNumber(row.inFlight)}</dd>
        </div>
        <div>
          <dt>Rows</dt>
          <dd>{formatCompactNumber(row.rows)}</dd>
        </div>
        <div className="wide">
          <dt>Script</dt>
          <dd>{row.script || "-"}</dd>
        </div>
        <div className="wide">
          <dt>Detail</dt>
          <dd>{row.detail || "-"}</dd>
        </div>
      </dl>
    </div>
  );
}
