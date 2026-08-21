import { useEffect, useId, useRef, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { Button } from "./Button";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function Modal({
  children,
  className,
  closeOnBackdrop = false,
  onClose,
  title,
}: {
  children: ReactNode;
  className?: string;
  closeOnBackdrop?: boolean;
  onClose: () => void;
  title: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  onCloseRef.current = onClose;

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const focusInitialElement = window.requestAnimationFrame(() => {
      const panel = panelRef.current;
      const firstFocusable = panel?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      (firstFocusable ?? panel)?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)]
        .filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
      if (!focusable.length) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusInitialElement);
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, []);

  const panelClassName = className ? `modal-panel ${className}` : "modal-panel";
  const backdropClassName = className ? `modal-backdrop ${className}-backdrop` : "modal-backdrop";
  const handleBackdropMouseDown = (event: MouseEvent<HTMLDivElement>) => {
    if (closeOnBackdrop && event.target === event.currentTarget) onClose();
  };
  const modal = (
    <div className={backdropClassName} onMouseDown={handleBackdropMouseDown} role="presentation">
      <div aria-labelledby={titleId} aria-modal="true" className={panelClassName} ref={panelRef} role="dialog" tabIndex={-1}>
        <div className="modal-header">
          <h2 id={titleId}>{title}</h2>
          <Button aria-label={`Close ${title}`} iconOnly onClick={onClose}><X size={16} /></Button>
        </div>
        {children}
      </div>
    </div>
  );
  return typeof document === "undefined" ? modal : createPortal(modal, document.body);
}
