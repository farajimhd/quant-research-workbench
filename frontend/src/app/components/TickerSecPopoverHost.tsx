import { type ReactNode, useCallback, useEffect, useState } from "react";

import { TickerSecPopover } from "./SecContainers";
import { OPEN_TICKER_SEC_EVENT, type OpenTickerSecDetail } from "./TickerSecPopoverContext";

export function TickerSecPopoverProvider({ children }: { children: ReactNode }) {
  const [popover, setPopover] = useState<OpenTickerSecDetail | null>(null);
  const open = useCallback((element: HTMLElement, ticker: string) => {
    const symbol = ticker.trim().toUpperCase();
    if (!symbol) return;
    const rect = element.getBoundingClientRect();
    setPopover({
      anchor: { bottom: rect.bottom, left: rect.left, right: rect.right, top: rect.top },
      ticker: symbol,
    });
  }, []);
  useEffect(() => {
    const handleOpen = (event: Event) => setPopover((event as CustomEvent<OpenTickerSecDetail>).detail);
    const handleSecAction = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const action = target.closest<HTMLElement>("[data-open-sec-ticker]");
      if (!action) return;
      open(action, action.dataset.openSecTicker ?? "");
    };
    window.addEventListener(OPEN_TICKER_SEC_EVENT, handleOpen);
    document.addEventListener("pointerdown", handleSecAction, true);
    document.addEventListener("click", handleSecAction, true);
    return () => {
      window.removeEventListener(OPEN_TICKER_SEC_EVENT, handleOpen);
      document.removeEventListener("pointerdown", handleSecAction, true);
      document.removeEventListener("click", handleSecAction, true);
    };
  }, [open]);
  return <>{children}{popover ? <TickerSecPopover anchor={popover.anchor} onClose={() => setPopover(null)} ticker={popover.ticker} /> : null}</>;
}
