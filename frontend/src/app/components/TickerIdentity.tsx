import { useEffect, useMemo, useState } from "react";

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

export function TickerLogo({ logoUrl, ticker }: { logoUrl?: string; ticker: string }) {
  return logoUrl ? <img alt="" className="ticker-logo" loading="lazy" onError={(event) => { event.currentTarget.hidden = true; }} src={logoUrl} title={ticker} /> : null;
}

function normalizeTickers(tickers: string[]) {
  return [...new Set(tickers.map((ticker) => String(ticker || "").trim().toUpperCase()).filter((ticker) => /^[A-Z][A-Z0-9.\-]{0,15}$/.test(ticker)))].sort();
}
