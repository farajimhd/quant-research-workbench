import { useState, type CSSProperties } from "react";
import { Flame, Megaphone, Newspaper, X } from "lucide-react";

import { clockTimestampSeconds, type TradingSession } from "./time";

export type LiveNewsArticle = {
  age_minutes: number;
  body_text?: string;
  channels: string[];
  pdf_text?: string;
  published_et: string;
  recency: string;
  tags: string[];
  teaser?: string;
  ticker: string;
  ticker_count?: number;
  tickers?: string[];
  title: string;
  url: string;
};

export function LiveNewsSection({
  collapsible = false,
  defaultOpen = true,
  empty,
  items,
  onOpen,
  title,
}: {
  collapsible?: boolean;
  defaultOpen?: boolean;
  empty: string;
  items: LiveNewsArticle[];
  onOpen: (item: LiveNewsArticle) => void;
  title: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const showBody = !collapsible || open;
  return (
    <section className={showBody ? "live-news-section" : "live-news-section collapsed"}>
      <button className={collapsible ? "live-news-section-title collapsible" : "live-news-section-title"} type="button" onClick={() => collapsible && setOpen((current) => !current)}>
        <span>{title}</span>
        <strong>{collapsible ? `${items.length} ${open ? "Hide" : "Show"}` : items.length}</strong>
      </button>
      {showBody && items.length ? (
        <div className="live-news-list">
          {items.map((item, index) => (
            <LiveNewsItem item={item} key={`${item.published_et}-${index}`} onOpen={onOpen} />
          ))}
        </div>
      ) : showBody ? (
        <p>{empty}</p>
      ) : null}
    </section>
  );
}

function LiveNewsItem({ item, onOpen }: { item: LiveNewsArticle; onOpen: (item: LiveNewsArticle) => void }) {
  const indicator = liveNewsIndicator(item);
  const NewsIcon = indicator.icon;
  return (
    <button className="live-news-item-button" type="button" onClick={() => onOpen(item)} title={item.title}>
      <div className="live-news-meta">
        <time dateTime={item.published_et}>{formatNewsDateTime(item.published_et)}</time>
      </div>
      <div className="live-news-title-row">
        <NewsIcon className={`live-news-type-icon ${indicator.className}`} size={15} aria-label={indicator.label} />
        <strong>{item.title}</strong>
      </div>
      <div className="live-news-labels" aria-label="News labels">
        <span className={`live-news-category-label ${indicator.className}`}>{indicator.label}</span>
        {newsLabels(item).map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
    </button>
  );
}

export function LiveNewsDetailPopover({ item, onClose }: { item: LiveNewsArticle; onClose: () => void }) {
  const indicator = liveNewsIndicator(item);
  const NewsIcon = indicator.icon;
  const bodyText = [item.teaser, item.body_text].map((value) => String(value || "").trim()).filter(Boolean).join("\n\n");
  const pdfText = String(item.pdf_text || "").trim();
  const [textZoom, setTextZoom] = useState(1);
  const textZoomLabel = `${Math.round(textZoom * 100)}%`;
  const textZoomStyle = { "--live-news-text-zoom": textZoom } as CSSProperties;
  return (
    <div className="live-news-detail-backdrop" role="presentation" onMouseDown={onClose}>
      <aside
        className="live-news-detail-popover"
        role="dialog"
        aria-modal="true"
        aria-label="News details"
        style={textZoomStyle}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="live-news-detail-header">
          <div>
            <span className={`live-news-detail-category ${indicator.className}`}>
              <NewsIcon size={15} />
              {indicator.label}
            </span>
            <h3>{item.title}</h3>
            <time dateTime={item.published_et}>{formatNewsDateTime(item.published_et)}</time>
          </div>
          <div className="live-news-detail-actions">
            <div className="live-news-zoom-control" aria-label="Article text zoom">
              <button type="button" title="Decrease text size" onClick={() => setTextZoom((value) => Math.max(0.9, Number((value - 0.1).toFixed(2))))}>
                A-
              </button>
              <span>{textZoomLabel}</span>
              <button type="button" title="Increase text size" onClick={() => setTextZoom((value) => Math.min(1.6, Number((value + 0.1).toFixed(2))))}>
                A+
              </button>
            </div>
            <button className="icon-button" type="button" title="Close news" onClick={onClose}>
              <X size={15} />
            </button>
          </div>
        </header>
        <div className="live-news-detail-labels">
          {newsLabels(item, 8).map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
        <div className="live-news-detail-scroll">
          <section>
            <h4>Article</h4>
            {bodyText ? <p className="live-news-detail-text">{bodyText}</p> : <p className="muted">No article text was cached for this headline.</p>}
          </section>
          {pdfText ? (
            <section>
              <h4>PDF Text</h4>
              <p className="live-news-detail-text">{pdfText}</p>
            </section>
          ) : null}
          {item.url ? (
            <section>
              <h4>Source</h4>
              <p className="live-news-detail-source">{item.url}</p>
            </section>
          ) : null}
        </div>
      </aside>
    </div>
  );
}

export function liveNewsItems(row: Record<string, unknown>, session: TradingSession): LiveNewsArticle[] {
  const value = row.live_news_items;
  if (!Array.isArray(value)) return [];
  const cutoffSeconds = clockTimestampSeconds(session.sessionDate, session.barTime);
  return value
    .filter((item): item is LiveNewsArticle => Boolean(item && typeof item === "object" && "title" in item))
    .filter((item) => {
      if (!cutoffSeconds) return true;
      const publishedSeconds = Math.floor(Date.parse(item.published_et) / 1000);
      return Number.isFinite(publishedSeconds) && publishedSeconds <= cutoffSeconds;
    })
    .slice(0, 8);
}

function formatNewsDateTime(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "";
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    timeZone: "America/New_York",
    timeZoneName: "short",
    year: "numeric",
  }).format(new Date(timestamp));
}

function newsLabels(item: LiveNewsArticle, maxLabels = 3) {
  const labels = [...(item.tickers?.length ? item.tickers : [item.ticker]), ...(item.channels ?? []), ...(item.tags ?? [])]
    .map((label) => String(label || "").trim())
    .filter(Boolean);
  return Array.from(new Set(labels)).slice(0, maxLabels);
}

function liveNewsIndicator(item: LiveNewsArticle): { className: string; icon: typeof Newspaper; label: string } {
  if (newsTickerCount(item) > 1) return { className: "multi", icon: Newspaper, label: "Market News" };
  if ((item.recency || "").toLowerCase() === "hot") return { className: "hot-company", icon: Megaphone, label: "Company News" };
  return { className: "company", icon: Flame, label: "Company News" };
}

export function newsTickerCount(item: LiveNewsArticle) {
  if (Number.isFinite(item.ticker_count) && Number(item.ticker_count) > 0) return Number(item.ticker_count);
  return item.tickers?.length || 1;
}
