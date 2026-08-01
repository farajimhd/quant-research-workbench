import { ChevronDown } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

export type InventoryFilterOption = { label: string; value: string };

export function inventoryEligibilityOptions(label: string): InventoryFilterOption[] {
  return [{ value: "", label: `Any ${label.toLowerCase()}` }, { value: "eligible", label: "Eligible" }, { value: "ineligible", label: "Not eligible" }];
}

type MenuPlacement = CSSProperties & { maxHeight: number; width: number };

export function InventoryFilterSelect({ ariaLabel, onChange, options, value }: { ariaLabel: string; onChange: (value: string) => void; options: InventoryFilterOption[]; value: string | number }) {
  const normalizedValue = String(value);
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === normalizedValue));
  const selected = options[selectedIndex] ?? options[0];
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const [placement, setPlacement] = useState<MenuPlacement>({ left: 0, maxHeight: 240, top: 0, width: 170 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = `inventory-filter-${useId().replaceAll(":", "")}`;

  const placeMenu = useCallback(() => {
    const button = buttonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const scale = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--app-zoom")) || 1;
    const readableScale = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--app-readable-scale")) || 1;
    const menu = menuRef.current;
    const availableBelow = window.innerHeight - rect.bottom - 8;
    const availableAbove = rect.top - 8;
    const heightCap = 420 * readableScale;
    const contentHeight = menu?.scrollHeight || heightCap;
    const desiredHeight = Math.min(contentHeight, heightCap);
    const openAbove = availableBelow < desiredHeight && availableAbove > availableBelow;
    const availableHeight = openAbove ? availableAbove : availableBelow;
    const maxHeight = Math.max(80, Math.min(heightCap, availableHeight));
    const renderedHeight = Math.min(contentHeight, maxHeight);
    const option = menu?.querySelector<HTMLElement>(".inventory-filter-option");
    const optionStyle = option ? getComputedStyle(option) : null;
    const measureContext = document.createElement("canvas").getContext("2d");
    if (measureContext && optionStyle) measureContext.font = optionStyle.font;
    const labelWidth = measureContext ? Math.max(0, ...options.map(({ label }) => measureContext.measureText(label).width)) : 0;
    const optionPadding = optionStyle ? Number.parseFloat(optionStyle.paddingLeft) + Number.parseFloat(optionStyle.paddingRight) : 24 * scale;
    const menuStyle = menu ? getComputedStyle(menu) : null;
    const menuChrome = menuStyle ? Number.parseFloat(menuStyle.paddingLeft) + Number.parseFloat(menuStyle.paddingRight) + Number.parseFloat(menuStyle.borderLeftWidth) + Number.parseFloat(menuStyle.borderRightWidth) : 10 * scale;
    const scrollbarAllowance = contentHeight > maxHeight ? 18 : 0;
    const width = Math.min(window.innerWidth - 16, Math.max(rect.width, 170 * scale, labelWidth + optionPadding + menuChrome + scrollbarAllowance));
    setPlacement({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      maxHeight,
      top: openAbove ? Math.max(8, rect.top - renderedHeight - 4) : rect.bottom + 4,
      width,
    });
  }, [options]);

  useLayoutEffect(() => {
    if (!open) return;
    placeMenu();
    const update = () => placeMenu();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [open, placeMenu]);

  useEffect(() => {
    if (!open) return;
    setActiveIndex(selectedIndex);
    const frame = window.requestAnimationFrame(() => menuRef.current?.querySelector<HTMLButtonElement>(`[data-option-index="${selectedIndex}"]`)?.focus());
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!buttonRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open, selectedIndex]);

  function select(nextIndex: number) {
    const option = options[nextIndex];
    if (!option) return;
    onChange(option.value);
    setOpen(false);
    window.requestAnimationFrame(() => buttonRef.current?.focus());
  }

  function move(nextIndex: number) {
    const bounded = (nextIndex + options.length) % options.length;
    setActiveIndex(bounded);
    menuRef.current?.querySelector<HTMLButtonElement>(`[data-option-index="${bounded}"]`)?.focus();
  }

  function onButtonKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) return;
    event.preventDefault();
    setOpen(true);
  }

  function onOptionKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === "ArrowDown") { event.preventDefault(); move(index + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); move(index - 1); }
    else if (event.key === "Home") { event.preventDefault(); move(0); }
    else if (event.key === "End") { event.preventDefault(); move(options.length - 1); }
    else if (event.key === "Escape") { event.preventDefault(); setOpen(false); buttonRef.current?.focus(); }
    else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(index); }
  }

  return <>
    <button aria-controls={open ? menuId : undefined} aria-expanded={open} aria-haspopup="listbox" aria-label={ariaLabel} className="inventory-filter-button" onClick={() => setOpen((current) => !current)} onKeyDown={onButtonKeyDown} ref={buttonRef} type="button">
      <span>{selected?.label ?? "Select"}</span><ChevronDown aria-hidden="true" size={12} />
    </button>
    {open ? createPortal(<div aria-label={ariaLabel} className="inventory-filter-menu" id={menuId} ref={menuRef} role="listbox" style={placement}>
      {options.map((option, index) => <button aria-selected={option.value === normalizedValue} className="inventory-filter-option" data-option-index={index} key={option.value} onClick={() => select(index)} onFocus={() => setActiveIndex(index)} onKeyDown={(event) => onOptionKeyDown(event, index)} role="option" tabIndex={activeIndex === index ? 0 : -1} type="button">{option.label}</button>)}
    </div>, document.body) : null}
  </>;
}
