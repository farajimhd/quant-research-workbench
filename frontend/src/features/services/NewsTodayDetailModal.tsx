import { DataTable } from "../../app/components/DataTable";
import { displayName, formatCompactNumber } from "../../app/format";
import type { NewsDetailPayload, NewsTodayRow } from "./newsContracts";
import { cleanNewsArticleText, newsArticleBlocks } from "./newsArticlePresentation";
import { newsDetailTickers } from "./newsTodayPresentation";
import { numericMetric, stringArrayMetric, stringMetric } from "./metrics";
import { ServiceMetadataTable } from "./ServiceMetadataTable";
import { ServiceTimeCard } from "./ServiceTimeCard";
import { EXCHANGE_TIME_ZONE, VANCOUVER_TIME_ZONE, formatReadableDateTime } from "./time";
import { formatValue, isRecord, normalizeRow } from "./workPresentation";

export function NewsTodayDetailModal({ detail, error, loading, row }: { detail: NewsDetailPayload | null; error: string; loading: boolean; row: NewsTodayRow }) {
  const dbRow = isRecord(detail?.row) ? detail.row : {};
  const tickerRows = Array.isArray(detail?.ticker_rows) ? detail.ticker_rows.filter(isRecord) : [];
  const title = stringMetric(dbRow, ["title", "normalized_title"]) || row.title || row.normalizedTitle || "Untitled news row";
  const publishedAt = stringMetric(dbRow, ["published_at_utc"]) || row.publishedAtUtc;
  const downloadedAt = stringMetric(dbRow, ["downloaded_at_utc"]) || row.downloadedAtUtc;
  const articleUrl = stringMetric(dbRow, ["article_url"]) || row.articleUrl;
  const domain = stringMetric(dbRow, ["url_domain"]) || row.urlDomain || "benzinga";
  const author = stringMetric(dbRow, ["author"]) || row.author || "Benzinga";
  const canonicalId = stringMetric(dbRow, ["canonical_news_id"]) || row.canonicalNewsId;
  const providerId = stringMetric(dbRow, ["provider_article_id"]) || row.providerArticleId;
  const tickers = newsDetailTickers(dbRow, tickerRows, row);
  const channels = stringArrayMetric(dbRow, ["channels"]).length ? stringArrayMetric(dbRow, ["channels"]) : row.channels;
  const providerTags = stringArrayMetric(dbRow, ["provider_tags"]).length ? stringArrayMetric(dbRow, ["provider_tags"]) : row.providerTags;
  const qualityFlags = stringArrayMetric(dbRow, ["content_quality_flags"]).length ? stringArrayMetric(dbRow, ["content_quality_flags"]) : row.contentQualityFlags;
  const textCandidates = newsDetailTextCandidates(dbRow, row);
  const primaryText = textCandidates[0] ?? { label: "No Body Text", value: row.textPreview || "No readable body text was returned for this news row." };
  const articleBlocks = newsArticleBlocks(primaryText.value, title, stringMetric(dbRow, ["teaser"]) || row.textPreview);
  const statRows = [
    { label: "Full text", value: numericMetric(dbRow, ["full_text_chars"]) || row.fullTextChars },
    { label: "Body", value: numericMetric(dbRow, ["body_chars"]) || row.bodyChars },
    { label: "External", value: numericMetric(dbRow, ["external_chars"]) || row.externalChars },
    { label: "PDF", value: numericMetric(dbRow, ["pdf_chars"]) || row.pdfChars },
  ].filter((item) => item.value).map((item) => ({ ...item, value: `${formatCompactNumber(item.value)} chars` }));
  const readableFacts = [
    { label: "Provider article", value: providerId || "-" }, { label: "Canonical row", value: canonicalId || "-" },
    { label: "Downloaded", value: downloadedAt ? formatReadableDateTime(downloadedAt, "UTC") : "-" }, { label: "Source domain", value: domain || "-" },
    { label: "Author", value: author || "-" }, { label: "Channels", value: channels.length ? channels.join(", ") : "-" },
    { label: "Provider tags", value: providerTags.length ? providerTags.join(", ") : "-" }, { label: "Text source", value: primaryText.label },
  ];
  const processingFacts = [
    { label: "External fetch", value: displayName(stringMetric(dbRow, ["external_fetch_status", "external_fetch_error"]) || row.externalFetchStatus || "not reported") },
    { label: "PDF extraction", value: displayName(stringMetric(dbRow, ["pdf_extract_status", "pdf_extract_error"]) || row.pdfExtractStatus || "not reported") },
    { label: "Normalizer", value: stringMetric(dbRow, ["normalizer_version"]) || "-" }, { label: "Raw artifact", value: stringMetric(dbRow, ["raw_artifact_path"]) || "-" },
  ];
  const remainingRows = Object.entries(dbRow).map(([key, value]) => ({ key, value: formatValue(key, value) }));
  return (
    <div className="news-full-detail">
      <article className="news-full-article-card">
        <header className="news-full-article-header">
          <div className="news-full-article-meta-line"><span className="news-full-provider-pill">Benzinga</span><span>{domain}</span><span>{tickers.length ? `${tickers.length} ticker${tickers.length === 1 ? "" : "s"}` : "Market-wide"}</span><span>{qualityFlags.length ? qualityFlags.slice(0, 3).map(displayName).join(" / ") : "No quality flags"}</span></div>
          <h3>{title}</h3><p>{stringMetric(dbRow, ["teaser"]) || row.textPreview || "No summary text was returned for this news row."}</p>
          <div className="news-full-ticker-row">{(tickers.length ? tickers : ["No ticker linked"]).slice(0, 18).map((ticker) => <span className={tickers.length ? "news-full-ticker-chip" : "news-full-muted-chip"} key={ticker}>{ticker}</span>)}{tickers.length > 18 ? <span className="news-full-muted-chip">+{tickers.length - 18} more</span> : null}</div>
        </header>
        <div className="news-full-time-grid"><ServiceTimeCard label="Market time" timeZone={EXCHANGE_TIME_ZONE} value={publishedAt} /><ServiceTimeCard label="Vancouver" timeZone={VANCOUVER_TIME_ZONE} value={publishedAt} /><ServiceTimeCard label="UTC" timeZone="UTC" value={publishedAt} /></div>
        <div className="news-full-readable-grid">
          <section className="news-full-readable-main"><div className="news-full-section-heading"><span>Readable body</span><strong>{primaryText.label}</strong></div><div className="news-full-readable-body">{articleBlocks.map((block, index) => block.kind === "list" ? <ul className="news-full-readable-list" key={`${primaryText.label}-${index}`}>{block.items.map((item, itemIndex) => <li key={`${item}-${itemIndex}`}>{item}</li>)}</ul> : <p className={`news-full-readable-${block.kind}`} key={`${primaryText.label}-${index}`}>{block.text}</p>)}</div></section>
          <aside className="news-full-readable-side"><DetailFacts title="Article Context" items={readableFacts} /><DetailFacts title="Processing" items={processingFacts} /></aside>
        </div>
        <footer className="news-full-article-footer">
          {articleUrl ? <a className="news-full-source-link" href={articleUrl} rel="noreferrer" target="_blank">Open source article</a> : <span className="news-full-source-link news-full-source-link-disabled">No source article URL</span>}
          <details className="news-full-technical-section"><summary><span>Technical details</span><strong>Raw fields, alternate text, ticker links</strong></summary><div className="news-full-technical-content">
            <section className="news-full-text-metrics">{(statRows.length ? statRows : [{ label: "Reported text", value: "No text length metadata reported." }]).map((item) => <div key={item.label}><span>{item.label}</span><strong>{item.value}</strong></div>)}</section>
            {textCandidates.slice(1).map((section) => <details className="news-full-text-section" key={section.label}><summary>{section.label}</summary><pre>{section.value}</pre></details>)}
            {tickerRows.length ? <section className="news-full-table-section"><h4>Ticker Relations</h4><DataTable fitToContent rows={tickerRows.map(normalizeRow)} /></section> : null}
            <section className="news-full-table-section"><h4>Actual Database Values</h4><ServiceMetadataTable rows={remainingRows} /></section>
          </div></details>
        </footer>
      </article>
      {loading ? <div className="news-full-detail-notice inline-loading-message"><span className="loading-spinner" aria-hidden="true" />Loading complete row from ClickHouse...</div> : null}
      {error ? <div className="news-full-detail-notice error">{error}</div> : null}
    </div>
  );
}

function DetailFacts({ items, title }: { items: Array<{ label: string; value: string }>; title: string }) {
  return <section><h4>{title}</h4><dl>{items.map((item) => <div className={item.label === "Raw artifact" ? "wide" : ""} key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></section>;
}

function newsDetailTextCandidates(dbRow: Record<string, unknown>, row: NewsTodayRow) {
  const candidates = [
    { label: "Provider body", value: stringMetric(dbRow, ["body_text"]) }, { label: "External source text", value: stringMetric(dbRow, ["external_text"]) },
    { label: "PDF extracted text", value: stringMetric(dbRow, ["pdf_text"]) }, { label: "Normalized full text", value: stringMetric(dbRow, ["normalized_full_text"]) },
    { label: "Teaser", value: stringMetric(dbRow, ["teaser"]) }, { label: "List preview", value: row.textPreview },
  ];
  return candidates.map((candidate) => ({ ...candidate, value: cleanNewsArticleText(candidate.value) })).filter((candidate, index, all) => candidate.value && all.findIndex((item) => item.value === candidate.value) === index);
}
