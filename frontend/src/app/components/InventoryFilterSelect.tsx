import { ChevronDown } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

export type InventoryFilterOption = { description?: string; label: string; value: string };

export function inventoryEligibilityOptions(label: string): InventoryFilterOption[] {
  return [{ value: "", label: `Any ${label.toLowerCase()}` }, { value: "eligible", label: "Eligible" }, { value: "ineligible", label: "Not eligible" }];
}

type MenuPlacement = CSSProperties & { maxHeight: number; width: number };

export function InventoryFilterSelect({ ariaLabel, className, defaultValue, onChange, options, searchable = false, searchPlaceholder = "Search…", value }: { ariaLabel: string; className?: string; defaultValue?: string | number; onChange: (value: string) => void; options: InventoryFilterOption[]; searchable?: boolean; searchPlaceholder?: string; value: string | number }) {
  const normalizedValue = String(value);
  const normalizedDefaultValue = String(defaultValue ?? options[0]?.value ?? "");
  const selectedIndex = Math.max(0, options.findIndex((option) => option.value === normalizedValue));
  const selected = options[selectedIndex] ?? options[0];
  const [open, setOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const [placement, setPlacement] = useState<MenuPlacement>({ left: 0, maxHeight: 240, top: 0, width: 170 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = `inventory-filter-${useId().replaceAll(":", "")}`;
  const matchingOptions = useMemo(() => {
    const term = searchText.trim().toLocaleUpperCase();
    if (!searchable) return options;
    if (!term) return options.filter((option) => !option.value || option.value === normalizedValue);
    return options.filter((option) => option.label.toLocaleUpperCase().includes(term) || option.value.toLocaleUpperCase().includes(term) || option.description?.toLocaleUpperCase().includes(term));
  }, [normalizedValue, options, searchText, searchable]);
  const visibleOptions = searchable ? matchingOptions.slice(0, 100) : matchingOptions;
  const hasDescriptions = options.some((option) => Boolean(option.description));

  const placeMenu = useCallback(() => {
    const button = buttonRef.current;
    if (!button) return;
    const rect = button.getBoundingClientRect();
    const rootStyle = getComputedStyle(document.documentElement);
    const scale = Number.parseFloat(rootStyle.getPropertyValue("--app-zoom")) || 1;
    const readableScale = Number.parseFloat(rootStyle.getPropertyValue("--app-readable-scale")) || 1;
    const overlayScale = Number.parseFloat(rootStyle.getPropertyValue("--app-overlay-scale")) || scale * readableScale;
    const menu = menuRef.current;
    const availableBelow = window.innerHeight - rect.bottom - 8;
    const availableAbove = rect.top - 8;
    const heightCap = 420 * overlayScale;
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
    const optionPadding = optionStyle ? Number.parseFloat(optionStyle.paddingLeft) + Number.parseFloat(optionStyle.paddingRight) : 24 * overlayScale;
    const menuStyle = menu ? getComputedStyle(menu) : null;
    const menuChrome = menuStyle ? Number.parseFloat(menuStyle.paddingLeft) + Number.parseFloat(menuStyle.paddingRight) + Number.parseFloat(menuStyle.borderLeftWidth) + Number.parseFloat(menuStyle.borderRightWidth) : 10 * overlayScale;
    const scrollbarAllowance = contentHeight > maxHeight ? 18 : 0;
    const textSafety = 28 * overlayScale;
    const width = Math.min(window.innerWidth - 16, Math.max(rect.width, 170 * overlayScale, hasDescriptions ? 380 * overlayScale : 210 * overlayScale, labelWidth + optionPadding + menuChrome + scrollbarAllowance + textSafety));
    setPlacement({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      maxHeight,
      top: openAbove ? Math.max(8, rect.top - renderedHeight - 4) : rect.bottom + 4,
      width,
    });
  }, [hasDescriptions, options]);

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
    setSearchText("");
    setActiveIndex(selectedIndex);
    const frame = window.requestAnimationFrame(() => searchable ? searchRef.current?.focus() : menuRef.current?.querySelector<HTMLButtonElement>(`[data-option-index="${selectedIndex}"]`)?.focus());
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!buttonRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [open, searchable, selectedIndex]);

  function select(nextIndex: number) {
    const option = visibleOptions[nextIndex];
    if (!option) return;
    onChange(option.value);
    setOpen(false);
    window.requestAnimationFrame(() => buttonRef.current?.focus());
  }

  function move(nextIndex: number) {
    if (!visibleOptions.length) return;
    const bounded = (nextIndex + visibleOptions.length) % visibleOptions.length;
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
    else if (event.key === "End") { event.preventDefault(); move(visibleOptions.length - 1); }
    else if (event.key === "Escape") { event.preventDefault(); setOpen(false); buttonRef.current?.focus(); }
    else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(index); }
  }

  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") { event.preventDefault(); move(0); }
    else if (event.key === "ArrowUp") { event.preventDefault(); move(visibleOptions.length - 1); }
    else if (event.key === "Enter" && visibleOptions.length === 1) { event.preventDefault(); select(0); }
    else if (event.key === "Escape") { event.preventDefault(); setOpen(false); buttonRef.current?.focus(); }
  }

  return <>
    <button aria-controls={open ? menuId : undefined} aria-expanded={open} aria-haspopup="listbox" aria-label={ariaLabel} className={`inventory-filter-button${className ? ` ${className}` : ""}`} data-filter-active={normalizedValue !== normalizedDefaultValue ? "true" : undefined} onClick={() => setOpen((current) => !current)} onKeyDown={onButtonKeyDown} ref={buttonRef} type="button">
      <span>{selected?.label ?? "Select"}</span><ChevronDown aria-hidden="true" size={12} />
    </button>
    {open ? createPortal(<div className="inventory-filter-menu" ref={menuRef} style={placement}>
      {searchable ? <label className="inventory-filter-search"><span className="sr-only">Search {ariaLabel}</span><input aria-label={`Search ${ariaLabel}`} onChange={(event) => { setSearchText(event.target.value); setActiveIndex(0); }} onKeyDown={onSearchKeyDown} placeholder={searchPlaceholder} ref={searchRef} type="search" value={searchText} /></label> : null}
      <div aria-label={ariaLabel} className="inventory-filter-options" id={menuId} role="listbox">{visibleOptions.map((option, index) => <button aria-selected={option.value === normalizedValue} className="inventory-filter-option" data-detailed={option.description ? "true" : undefined} data-option-index={index} key={option.value} onClick={() => select(index)} onFocus={() => setActiveIndex(index)} onKeyDown={(event) => onOptionKeyDown(event, index)} role="option" tabIndex={activeIndex === index ? 0 : -1} type="button">{option.description ? <span><strong>{option.label}</strong><small>{option.description}</small></span> : option.label}</button>)}</div>
      {searchable && !searchText.trim() && options.length > visibleOptions.length ? <span className="inventory-filter-hint">Type to search {options.length - 1} values</span> : null}
      {searchable && searchText.trim() && matchingOptions.length > visibleOptions.length ? <span className="inventory-filter-hint">Refine to narrow {matchingOptions.length} matches</span> : null}
      {searchable && !visibleOptions.length ? <span className="inventory-filter-empty">No matching ticker</span> : null}
    </div>, document.body) : null}
  </>;
}
