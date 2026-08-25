import { type FormEvent, useEffect, useMemo, useState } from "react";
import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";

import { api, query } from "../../api/client";
import { useWallClock } from "./useWallClock";

export type TickerPresentation = {
  country: string;
  issuer_name: string;
  live_news_recency?: "hot" | "cold" | "old" | "none";
  logo_url: string;
  market_is_halted?: boolean;
  sec_recency?: "hot" | "cold" | "old" | "none";
  ticker: string;
  trading_status?: string;
};

type TickerPresentationPayload = {
  presentations: Record<string, TickerPresentation>;
  status?: "partial" | "ready" | "unavailable";
};

const presentationCache = new Map<string, TickerPresentation | null>();
const presentationFetchedAt = new Map<string, number>();
const pendingPresentationRequests = new Map<string, Promise<void>>();
const presentationListeners = new Set<() => void>();
const failedLogoUrls = new Set<string>();
const PRESENTATION_REQUEST_BATCH_SIZE = 200;
const LIVE_RECENCY_TTL_MS = 30_000;
type TickerChange = { absolute_change: number | null; as_of: string; current_price: number | null; percent_change: number | null; previous_close: number | null; previous_session_date: string; reference_status: "ready" | "unavailable"; source: string; ticker: string };
const changeCache = new Map<string, TickerChange | null>();
const pendingChangeRequests = new Map<string, Promise<void>>();

export function useTickerPresentations(tickers: string[], { includeMarketState = false, includeRecency = false }: { includeMarketState?: boolean; includeRecency?: boolean } = {}) {
  const tickerKey = useMemo(() => normalizeTickers(tickers).join(","), [tickers]);
  const [revision, setRevision] = useState(0);
  const livePresentation = includeMarketState || includeRecency;
  const wallClockMs = useWallClock(LIVE_RECENCY_TTL_MS, livePresentation);
  const cachePrefix = `${includeRecency ? "recency" : "base"}:${includeMarketState ? "state" : "base"}:`;

  useEffect(() => {
    const listener = () => setRevision((value) => value + 1);
    presentationListeners.add(listener);
    return () => { presentationListeners.delete(listener); };
  }, []);

  useEffect(() => {
    const normalized = tickerKey ? tickerKey.split(",") : [];
    const now = Date.now();
    const missing = normalized.filter((ticker) => {
      const cacheKey = `${cachePrefix}${ticker}`;
      return !presentationCache.has(cacheKey)
        || (livePresentation && now - (presentationFetchedAt.get(cacheKey) ?? 0) >= LIVE_RECENCY_TTL_MS);
    });
    if (!missing.length) return;
    const requests = new Set<Promise<void>>();
    missing.forEach((ticker) => {
      const pending = pendingPresentationRequests.get(`${cachePrefix}${ticker}`);
      if (pending) requests.add(pending);
    });
    const fresh = missing.filter((ticker) => !pendingPresentationRequests.has(`${cachePrefix}${ticker}`));
    chunkTickers(fresh, PRESENTATION_REQUEST_BATCH_SIZE).forEach((batch) => requests.add(requestTickerPresentationBatch(batch, includeMarketState, includeRecency)));
    const request = Promise.all(requests);
    let active = true;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    request
      .catch(() => { if (active) retryTimer = setTimeout(() => setRevision((value) => value + 1), 5000); });
    return () => { active = false; if (retryTimer) clearTimeout(retryTimer); };
  }, [cachePrefix, includeMarketState, includeRecency, livePresentation, revision, tickerKey, wallClockMs]);

  return useMemo(() => Object.fromEntries(
    (tickerKey ? tickerKey.split(",") : []).flatMap((ticker) => {
      const presentation = presentationCache.get(`${cachePrefix}${ticker}`);
      return presentation ? [[ticker, presentation]] : [];
    }),
  ) as Record<string, TickerPresentation>, [cachePrefix, revision, tickerKey]);
}

function requestTickerPresentationBatch(tickers: string[], includeMarketState: boolean, includeRecency: boolean) {
  if (!tickers.length) return Promise.resolve();
  const requestKey = tickers.join(",");
  const cachePrefix = `${includeRecency ? "recency" : "base"}:${includeMarketState ? "state" : "base"}:`;
  const request = api<TickerPresentationPayload>(`/api/trading/ticker-presentations${query({ include_market_state: includeMarketState || undefined, include_recency: includeRecency || undefined, tickers: requestKey })}`, { timeoutMs: 15000 })
    .then((payload) => {
      if (payload.status === "unavailable") throw new Error("Ticker presentations are temporarily unavailable.");
      const fetchedAt = Date.now();
      tickers.forEach((ticker) => {
        const cacheKey = `${cachePrefix}${ticker}`;
        presentationCache.set(cacheKey, payload.presentations[ticker] ?? null);
        presentationFetchedAt.set(cacheKey, fetchedAt);
      });
      presentationListeners.forEach((listener) => listener());
    })
    .finally(() => tickers.forEach((ticker) => {
      const cacheKey = `${cachePrefix}${ticker}`;
      if (pendingPresentationRequests.get(cacheKey) === request) pendingPresentationRequests.delete(cacheKey);
    }));
  tickers.forEach((ticker) => pendingPresentationRequests.set(`${cachePrefix}${ticker}`, request));
  return request;
}

function chunkTickers(tickers: string[], size: number) {
  const chunks: string[][] = [];
  for (let index = 0; index < tickers.length; index += size) chunks.push(tickers.slice(index, index + size));
  return chunks;
}

export function TickerIdentity({ className = "", logoUrl = "", showLogoPlaceholder = false, ticker }: { className?: string; logoUrl?: string; showLogoPlaceholder?: boolean; ticker: string }) {
  const normalized = ticker.trim().toUpperCase();
  return <span className={`ticker-identity${className ? ` ${className}` : ""}`}>
    <TickerLogoImage fallbackText={showLogoPlaceholder ? normalized.slice(0, 2) : ""} logoUrl={logoUrl} title={normalized} />
    <span>{normalized || "—"}</span>
  </span>;
}

export function TickerIdentityWithChange({ asOf, className = "", inputAriaLabel = "Ticker", logoUrl = "", onTickerChange, ticker }: { asOf: string; className?: string; inputAriaLabel?: string; logoUrl?: string; onTickerChange?: (ticker: string) => void; ticker: string }) {
  return <span className="ticker-identity-with-change">{onTickerChange
    ? <TickerIdentityInput ariaLabel={inputAriaLabel} className={className} logoUrl={logoUrl} onTickerChange={onTickerChange} ticker={ticker} />
    : <TickerIdentity className={className} logoUrl={logoUrl} ticker={ticker} />}
    <TickerChangeBadge asOf={asOf} ticker={ticker} />
  </span>;
}

export function TickerIdentityInput({ ariaLabel = "Ticker", className = "", logoUrl = "", onTickerChange, ticker }: { ariaLabel?: string; className?: string; logoUrl?: string; onTickerChange: (ticker: string) => void; ticker: string }) {
  const normalizedTicker = normalizeTicker(ticker);
  const [draftTicker, setDraftTicker] = useState(normalizedTicker);
  useEffect(() => setDraftTicker(normalizedTicker), [normalizedTicker]);
  function commitTicker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextTicker = normalizeTicker(draftTicker);
    if (!nextTicker) {
      setDraftTicker(normalizedTicker);
      return;
    }
    setDraftTicker(nextTicker);
    if (nextTicker !== normalizedTicker) onTickerChange(nextTicker);
  }
  return <form className={`ticker-identity-input${className ? ` ${className}` : ""}`} onSubmit={commitTicker}>
    <TickerLogo logoUrl={logoUrl} ticker={ticker} />
    <input aria-label={ariaLabel} autoCapitalize="characters" maxLength={16} onChange={(event) => setDraftTicker(normalizeTicker(event.target.value))} spellCheck={false} value={draftTicker} />
  </form>;
}

export function TickerChangeBadge({ asOf, ticker }: { asOf: string; ticker: string }) {
  const change = useTickerChange(ticker, asOf);
  if (!change || change.percent_change == null || change.absolute_change == null || change.previous_close == null) return <span className="ticker-change-badge" data-tone="unavailable" title="Prior 20:00 ET session close is unavailable.">—</span>;
  const tone = change.percent_change > 0.0001 ? "up" : change.percent_change < -0.0001 ? "down" : "flat";
  const Icon = tone === "up" ? ArrowUpRight : tone === "down" ? ArrowDownRight : ArrowRight;
  const sign = change.percent_change > 0 ? "+" : "";
  return <span aria-label={`Day change ${sign}${change.percent_change.toFixed(2)} percent`} className="ticker-change-badge" data-tone={tone} title={`Day change at ${change.as_of}. Versus ${change.previous_session_date} previous-session close ${formatTickerPrice(change.previous_close)}; current ${formatTickerPrice(change.current_price ?? 0)}. This is not the scanner squeeze move.`}><Icon size={13} /><em>Day</em><strong>{sign}{change.percent_change.toFixed(2)}%</strong><small>{formatTickerChange(change.absolute_change)}</small></span>;
}

export function TickerLogo({ logoUrl, showLogoPlaceholder = false, ticker }: { logoUrl?: string; showLogoPlaceholder?: boolean; ticker: string }) {
  const normalized = ticker.trim().toUpperCase();
  return <TickerLogoImage className="ticker-logo" fallbackText={showLogoPlaceholder ? normalized.slice(0, 2) : ""} logoUrl={logoUrl} title={normalized} />;
}

function TickerLogoImage({ className, fallbackText = "", logoUrl, title }: { className?: string; fallbackText?: string; logoUrl?: string; title?: string }) {
  const [failedUrl, setFailedUrl] = useState("");
  const failed = logoUrl ? failedUrl === logoUrl || failedLogoUrls.has(logoUrl) : false;
  if (!logoUrl || failed) return fallbackText ? <span aria-hidden="true" className={`${className ? `${className} ` : ""}ticker-logo-fallback`} title={title}>{fallbackText}</span> : null;
  return <img
    alt=""
    aria-hidden="true"
    className={className}
    decoding="async"
    draggable={false}
    fetchPriority="low"
    loading="lazy"
    onError={() => {
      failedLogoUrls.add(logoUrl);
      setFailedUrl(logoUrl);
    }}
    src={logoUrl}
    title={title}
  />;
}

function normalizeTickers(tickers: string[]) {
  return [...new Set(tickers.map((ticker) => String(ticker || "").trim().toUpperCase()).filter((ticker) => /^[A-Z][A-Z0-9.\-]{0,15}$/.test(ticker)))].sort();
}

function normalizeTicker(value: string) {
  return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9.\-]/g, "").slice(0, 16);
}

function useTickerChange(ticker: string, asOf: string) {
  const normalized = ticker.trim().toUpperCase();
  // Day change is a second-resolution presentation metric. Canvas can render
  // the same identity in several panels whose wall clocks differ only by a few
  // milliseconds; normalize that shared cutoff so they coalesce into one
  // causal request instead of saturating QMD History with duplicate scans.
  const changeAsOf = useMemo(() => normalizeTickerChangeAsOf(asOf), [asOf]);
  const key = normalized && changeAsOf ? `${normalized}|${changeAsOf}` : "";
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!key || changeCache.has(key)) return;
    let request = pendingChangeRequests.get(key);
    if (!request) {
      request = api<TickerChange>(`/api/trading/ticker-change/${encodeURIComponent(normalized)}${query({ as_of: changeAsOf })}`, { timeoutMs: 120000 })
        .then((payload) => { changeCache.set(key, payload); })
        .catch(() => { changeCache.set(key, null); })
        .finally(() => pendingChangeRequests.delete(key));
      pendingChangeRequests.set(key, request);
    }
    let active = true;
    request.then(() => { if (active) setRevision((value) => value + 1); });
    return () => { active = false; };
  }, [changeAsOf, key, normalized]);
  return useMemo(() => changeCache.get(key) ?? null, [key, revision]);
}

export function normalizeTickerChangeAsOf(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Date(Math.floor(timestamp / 1000) * 1000).toISOString();
}

function formatTickerPrice(value: number) {
  const absolute = Math.abs(value);
  return absolute >= 100 ? value.toFixed(2) : value.toFixed(absolute < 1 ? 4 : 2);
}

function formatTickerChange(value: number) {
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const absolute = Math.abs(value);
  if (absolute > 0 && absolute < 1) return `${sign}${(absolute * 100).toFixed(absolute < 0.01 ? 2 : 1)}¢`;
  return `${sign}$${absolute.toFixed(2)}`;
}
