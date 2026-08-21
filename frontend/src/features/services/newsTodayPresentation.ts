import { formatCompactNumber } from "../../app/format";
import type { NewsTodayRow, NewsTodaySummary } from "./newsContracts";
import { numericMetric, stringArrayMetric, stringMetric, uniqueStringSample } from "./metrics";
import { formatLogTime, parseServiceTimestamp } from "./time";
import { isRecord } from "./workPresentation";

export function newsTodayFilteredRows(rows: NewsTodayRow[], query: string) {
  const terms = query.toLowerCase().split(/\s+/).map((term) => term.trim()).filter(Boolean);
  if (!terms.length) return rows;
  return rows.filter((row) => {
    const haystack = newsTodaySearchText(row);
    return terms.every((term) => haystack.includes(term));
  });
}

function newsTodaySearchText(row: NewsTodayRow) {
  return [
    row.articleUrl,
    row.author,
    row.canonicalNewsId,
    row.downloadedAtUtc,
    row.externalFetchStatus,
    row.normalizedTitle,
    row.pdfExtractStatus,
    row.providerArticleId,
    row.publishedAtUtc,
    row.textPreview,
    row.title,
    row.urlDomain,
    formatLogTime(row.publishedAtUtc),
    newsTodayTickerLabel(row),
    newsTodayTextLabel(row),
    newsTodayFlagLabel(row),
    row.channels.join(" "),
    row.contentQualityFlags.join(" "),
    row.providerTags.join(" "),
    row.tickerLinkSample.join(" "),
    row.tickers.join(" "),
  ].join(" ").toLowerCase();
}

export function newsTodaySummaryFromPayload(summaryPayload: unknown, rows: NewsTodayRow[]): NewsTodaySummary {
  const fallback = rows.reduce(
    (summary, row) => {
      const tickerCount = row.tickerLinkCount || row.tickers.length;
      return {
        externalText: summary.externalText + (row.hasExternalText ? 1 : 0),
        latest: !summary.latest || parseServiceTimestamp(row.publishedAtUtc) > parseServiceTimestamp(summary.latest) ? row.publishedAtUtc : summary.latest,
        loadedRows: rows.length,
        multiTickerRows: summary.multiTickerRows + (tickerCount > 1 ? 1 : 0),
        noTickerRows: summary.noTickerRows + (tickerCount <= 0 ? 1 : 0),
        oneTickerRows: summary.oneTickerRows + (tickerCount === 1 ? 1 : 0),
        pdfRows: summary.pdfRows + (row.hasPdf ? 1 : 0),
        totalRows: rows.length,
        withTicker: summary.withTicker + (tickerCount > 0 ? 1 : 0),
      };
    },
    { externalText: 0, latest: "", loadedRows: rows.length, multiTickerRows: 0, noTickerRows: 0, oneTickerRows: 0, pdfRows: 0, totalRows: rows.length, withTicker: 0 },
  );
  if (!isRecord(summaryPayload)) return fallback;
  return {
    externalText: numericMetric(summaryPayload, ["external_text_rows"]) || fallback.externalText,
    latest: stringMetric(summaryPayload, ["latest_published_at_utc"]) || fallback.latest,
    loadedRows: numericMetric(summaryPayload, ["loaded_rows"]) || rows.length,
    multiTickerRows: numericMetric(summaryPayload, ["multi_ticker_rows"]) || fallback.multiTickerRows,
    noTickerRows: numericMetric(summaryPayload, ["no_ticker_rows"]) || fallback.noTickerRows,
    oneTickerRows: numericMetric(summaryPayload, ["one_ticker_rows"]) || fallback.oneTickerRows,
    pdfRows: numericMetric(summaryPayload, ["pdf_rows"]) || fallback.pdfRows,
    totalRows: numericMetric(summaryPayload, ["total_rows"]) || fallback.totalRows,
    withTicker: numericMetric(summaryPayload, ["with_ticker_rows"]) || fallback.withTicker,
  };
}

export function newsTodayRowFromPayload(row: Record<string, unknown>): NewsTodayRow {
  return {
    articleUrl: stringMetric(row, ["article_url"]), author: stringMetric(row, ["author"]), bodyChars: numericMetric(row, ["body_chars"]), canonicalNewsId: stringMetric(row, ["canonical_news_id"]),
    channels: stringArrayMetric(row, ["channels"]), contentQualityFlags: stringArrayMetric(row, ["content_quality_flags"]), downloadedAtUtc: stringMetric(row, ["downloaded_at_utc"]), externalChars: numericMetric(row, ["external_chars"]),
    externalFetchStatus: stringMetric(row, ["external_fetch_status"]), fullTextChars: numericMetric(row, ["full_text_chars"]), hasBody: Boolean(Number(row.has_body || 0)), hasExternalText: Boolean(Number(row.has_external_text || 0)),
    hasPdf: Boolean(Number(row.has_pdf || 0)), isTitleOnly: Boolean(Number(row.is_title_only || 0)), normalizedTitle: stringMetric(row, ["normalized_title"]), pdfChars: numericMetric(row, ["pdf_chars"]),
    pdfExtractStatus: stringMetric(row, ["pdf_extract_status"]), providerArticleId: stringMetric(row, ["provider_article_id"]), providerTags: stringArrayMetric(row, ["provider_tags"]), publishedAtUtc: stringMetric(row, ["published_at_utc"]),
    textPreview: stringMetric(row, ["text_preview"]), tickerLinkCount: numericMetric(row, ["ticker_link_count"]), tickerLinkSample: stringArrayMetric(row, ["ticker_link_sample"]), tickers: stringArrayMetric(row, ["tickers"]), title: stringMetric(row, ["title"]), urlDomain: stringMetric(row, ["url_domain"]),
  };
}

export function newsTodayTickerLabel(row: NewsTodayRow) {
  const tickers = row.tickers.length ? row.tickers : row.tickerLinkSample;
  if (!tickers.length) return "-";
  const label = tickers.slice(0, 4).join(", ");
  const extra = Math.max(0, tickers.length - 4);
  return extra ? `${label} +${extra}` : label;
}

export function newsTodayTickerChips(row: NewsTodayRow) {
  const tickers = row.tickers.length ? row.tickers : row.tickerLinkSample;
  if (!tickers.length) return ["-"];
  const labels = tickers.slice(0, 3);
  const extra = Math.max(0, tickers.length - labels.length);
  return extra ? [...labels, `+${extra}`] : labels;
}

export function newsTodayTextLabel(row: NewsTodayRow) {
  const parts = [row.bodyChars ? `body ${formatCompactNumber(row.bodyChars)}` : "", row.externalChars ? `ext ${formatCompactNumber(row.externalChars)}` : "", row.pdfChars ? `pdf ${formatCompactNumber(row.pdfChars)}` : ""].filter(Boolean);
  return parts.length ? parts.join(" / ") : row.isTitleOnly ? "title only" : "-";
}

function newsTodayFlagLabel(row: NewsTodayRow) {
  const flags = row.contentQualityFlags;
  if (!flags.length) return "-";
  const label = flags.slice(0, 2).join(", ");
  const extra = Math.max(0, flags.length - 2);
  return extra ? `${label} +${extra}` : label;
}

export function newsTodayFlagChips(row: NewsTodayRow) {
  const flags = row.contentQualityFlags;
  if (!flags.length) return ["-"];
  const labels = flags.slice(0, 2);
  const extra = Math.max(0, flags.length - labels.length);
  return extra ? [...labels, `+${extra}`] : labels;
}

export function newsTodayRowTone(row: NewsTodayRow) {
  const tickerCount = row.tickerLinkCount || row.tickers.length;
  return row.hasPdf ? "has-pdf" : row.hasExternalText ? "has-external-text" : tickerCount > 1 ? "multi-ticker" : tickerCount === 1 ? "one-ticker" : row.isTitleOnly ? "title-only" : "broad-news";
}

export function newsDetailTickers(dbRow: Record<string, unknown>, tickerRows: Record<string, unknown>[], row: NewsTodayRow) {
  const relationTickers = tickerRows.map((item) => stringMetric(item, ["ticker", "symbol", "primary_ticker", "normalized_ticker"])).filter(Boolean);
  return uniqueStringSample([...stringArrayMetric(dbRow, ["tickers"]), ...row.tickers, ...row.tickerLinkSample, ...relationTickers], 48);
}
