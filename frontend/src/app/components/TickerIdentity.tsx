import { useEffect, useMemo, useState } from "react";
import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";

import { api, query } from "../../api/client";

export type TickerPresentation = {
  issuer_name: string;
  logo_url: string;
  ticker: string;
};

type TickerPresentationPayload = {
  presentations: Record<string, TickerPresentation>;
};

const presentationCache = new Map<string, TickerPresentation | null>();
const pendingRequests = new Map<string, Promise<void>>();
type TickerChange = { absolute_change: number | null; as_of: string; current_price: number | null; percent_change: number | null; previous_close: number | null; previous_session_date: string; ticker: string };
const changeCache = new Map<string, TickerChange | null>();
const pendingChangeRequests = new Map<string, Promise<void>>();

export function useTickerPresentations(tickers: string[]) {
  const tickerKey = useMemo(() => normalizeTickers(tickers).join(","), [tickers]);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const normalized = tickerKey ? tickerKey.split(",") : [];
    const missing = normalized.filter((ticker) => !presentationCache.has(ticker));
    if (!missing.length) return;
    const requestKey = missing.join(",");
    let request = pendingRequests.get(requestKey);
    if (!request) {
      request = api<TickerPresentationPayload>(`/api/trading/ticker-presentations${query({ tickers: requestKey })}`, { timeoutMs: 15000 })
        .then((payload) => {
          missing.forEach((ticker) => presentationCache.set(ticker, payload.presentations[ticker] ?? null));
        })
        .finally(() => pendingRequests.delete(requestKey));
      pendingRequests.set(requestKey, request);
    }
    let active = true;
    request.then(() => { if (active) setRevision((value) => value + 1); }).catch(() => undefined);
    return () => { active = false; };
  }, [tickerKey]);

  return useMemo(() => Object.fromEntries(
    (tickerKey ? tickerKey.split(",") : []).flatMap((ticker) => {
      const presentation = presentationCache.get(ticker);
      return presentation ? [[ticker, presentation]] : [];
    }),
  ) as Record<string, TickerPresentation>, [revision, tickerKey]);
}

export function TickerIdentity({ className = "", logoUrl = "", ticker }: { className?: string; logoUrl?: string; ticker: string }) {
  const normalized = ticker.trim().toUpperCase();
  return <span className={`ticker-identity${className ? ` ${className}` : ""}`}>
    {logoUrl ? <img alt="" loading="lazy" onError={(event) => { event.currentTarget.hidden = true; }} src={logoUrl} /> : null}
    <span>{normalized || "—"}</span>
  </span>;
}

export function TickerIdentityWithChange({ asOf, className = "", logoUrl = "", ticker }: { asOf: string; className?: string; logoUrl?: string; ticker: string }) {
  return <span className="ticker-identity-with-change"><TickerIdentity className={className} logoUrl={logoUrl} ticker={ticker} /><TickerChangeBadge asOf={asOf} ticker={ticker} /></span>;
}

export function TickerChangeBadge({ asOf, ticker }: { asOf: string; ticker: string }) {
  const change = useTickerChange(ticker, asOf);
  if (!change || change.percent_change == null || change.absolute_change == null || change.previous_close == null) return <span className="ticker-change-badge" data-tone="unavailable" title="Prior 20:00 ET session close is unavailable.">—</span>;
  const tone = change.percent_change > 0.0001 ? "up" : change.percent_change < -0.0001 ? "down" : "flat";
  const Icon = tone === "up" ? ArrowUpRight : tone === "down" ? ArrowDownRight : ArrowRight;
  const sign = change.percent_change > 0 ? "+" : "";
  return <span className="ticker-change-badge" data-tone={tone} title={`Versus ${change.previous_session_date} 20:00 ET close ${formatTickerPrice(change.previous_close)}; current ${formatTickerPrice(change.current_price ?? 0)}.`}><Icon size={13} /><strong>{sign}{change.percent_change.toFixed(2)}%</strong><small>{formatTickerChange(change.absolute_change)}</small></span>;
}

export function TickerLogo({ logoUrl, ticker }: { logoUrl?: string; ticker: string }) {
  return logoUrl ? <img alt="" className="ticker-logo" loading="lazy" onError={(event) => { event.currentTarget.hidden = true; }} src={logoUrl} title={ticker} /> : null;
}

function normalizeTickers(tickers: string[]) {
  return [...new Set(tickers.map((ticker) => String(ticker || "").trim().toUpperCase()).filter((ticker) => /^[A-Z][A-Z0-9.\-]{0,15}$/.test(ticker)))].sort();
}

function useTickerChange(ticker: string, asOf: string) {
  const normalized = ticker.trim().toUpperCase();
  const key = normalized && asOf ? `${normalized}|${asOf}` : "";
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!key || changeCache.has(key)) return;
    let request = pendingChangeRequests.get(key);
    if (!request) {
      request = api<TickerChange>(`/api/trading/ticker-change/${encodeURIComponent(normalized)}${query({ as_of: asOf })}`, { timeoutMs: 120000 })
        .then((payload) => { changeCache.set(key, payload); })
        .catch(() => { changeCache.set(key, null); })
        .finally(() => pendingChangeRequests.delete(key));
      pendingChangeRequests.set(key, request);
    }
    let active = true;
    request.then(() => { if (active) setRevision((value) => value + 1); });
    return () => { active = false; };
  }, [asOf, key, normalized]);
  return useMemo(() => changeCache.get(key) ?? null, [key, revision]);
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
