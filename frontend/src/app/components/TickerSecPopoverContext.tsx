export type TickerSecPopoverAnchor = { bottom: number; left: number; right: number; top: number };
export type OpenTickerSecDetail = { anchor: TickerSecPopoverAnchor; ticker: string };

export const OPEN_TICKER_SEC_EVENT = "quant-open-ticker-sec";

export function dispatchTickerSecPopover(element: HTMLElement, ticker: string) {
  const symbol = ticker.trim().toUpperCase();
  if (!symbol) return;
  const rect = element.getBoundingClientRect();
  window.dispatchEvent(new CustomEvent<OpenTickerSecDetail>(OPEN_TICKER_SEC_EVENT, {
    detail: { anchor: { bottom: rect.bottom, left: rect.left, right: rect.right, top: rect.top }, ticker: symbol },
  }));
}
