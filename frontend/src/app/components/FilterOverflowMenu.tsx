import { ChevronDown } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";

type FilterOverflowMenuProps = {
  activeCount?: number;
  children: ReactNode;
  label?: string;
};

export function FilterOverflowMenu({ activeCount = 0, children, label = "More filters" }: FilterOverflowMenuProps) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState<CSSProperties>({ left: 8, maxHeight: 420, top: 48, width: 380 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = `filter-overflow-${useId().replaceAll(":", "")}`;

  const placeMenu = useCallback(() => {
    const button = buttonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const readableScale = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--app-readable-scale")) || 1;
    const width = Math.min(window.innerWidth - 16, 400 * readableScale);
    const availableBelow = window.innerHeight - rect.bottom - 8;
    const availableAbove = rect.top - 8;
    const heightCap = Math.min(560 * readableScale, Math.max(180, window.innerHeight - 16));
    const desiredHeight = Math.min(menuRef.current?.scrollHeight || heightCap, heightCap);
    const openAbove = availableBelow < desiredHeight && availableAbove > availableBelow;
    const maxHeight = Math.max(160, Math.min(heightCap, openAbove ? availableAbove : availableBelow));
    setPlacement({
      left: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
      maxHeight,
      top: openAbove ? Math.max(8, rect.top - Math.min(desiredHeight, maxHeight) - 4) : rect.bottom + 4,
      width,
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    placeMenu();
    window.addEventListener("resize", placeMenu);
    window.addEventListener("scroll", placeMenu, true);
    return () => {
      window.removeEventListener("resize", placeMenu);
      window.removeEventListener("scroll", placeMenu, true);
    };
  }, [open, placeMenu]);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => menuRef.current?.querySelector<HTMLElement>("button, input")?.focus());
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target) || target.closest(".inventory-filter-menu")) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return <>
    <button aria-controls={open ? menuId : undefined} aria-expanded={open} aria-haspopup="dialog" className="filter-overflow-button" onClick={() => setOpen((current) => !current)} ref={buttonRef} type="button">
      <span>{label}</span>{activeCount ? <b aria-label={`${activeCount} active filters`}>{activeCount}</b> : null}<ChevronDown aria-hidden="true" size={13} />
    </button>
    {open ? createPortal(<div aria-label={label} className="filter-overflow-menu" id={menuId} ref={menuRef} role="dialog" style={placement}>{children}</div>, document.body) : null}
  </>;
}
