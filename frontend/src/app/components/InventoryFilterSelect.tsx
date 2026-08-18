import { ChevronDown, Search } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

export type InventoryFilterOption = { description?: string; disabled?: boolean; family?: string; group?: string; interval?: string; intervals?: string[]; label: string; reference?: string; subgroup?: string; value: string };

export function inventoryEligibilityOptions(label: string): InventoryFilterOption[] {
  return [{ value: "", label: `Any ${label.toLowerCase()}` }, { value: "eligible", label: "Eligible" }, { value: "ineligible", label: "Not eligible" }];
}

type MenuPlacement = CSSProperties & { maxHeight: number; width: number };

export function InventoryFilterSelect({ ariaLabel, className, defaultValue, disabled = false, onChange, optionLimit = 100, options, placeholder, presentation = "compact", searchable = false, searchPlaceholder = "Search…", showAllOnOpen = false, value }: { ariaLabel: string; className?: string; defaultValue?: string | number; disabled?: boolean; onChange: (value: string) => void; optionLimit?: number; options: InventoryFilterOption[]; placeholder?: string; presentation?: "catalog" | "compact"; searchable?: boolean; searchPlaceholder?: string; showAllOnOpen?: boolean; value: string | number }) {
  const normalizedValue = String(value);
  const normalizedDefaultValue = String(defaultValue ?? options[0]?.value ?? "");
  const matchingIndex = options.findIndex((option) => option.value === normalizedValue);
  const selectedIndex = matchingIndex >= 0 ? matchingIndex : placeholder ? -1 : 0;
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined;
  const hasSelection = Boolean(normalizedValue && selected);
  const [open, setOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [activeIndex, setActiveIndex] = useState(Math.max(0, selectedIndex));
  const [placement, setPlacement] = useState<MenuPlacement>({ left: 0, maxHeight: 240, top: 0, width: 170 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = `inventory-filter-${useId().replaceAll(":", "")}`;
  const matchingOptions = useMemo(() => {
    const term = searchText.trim().toLocaleUpperCase();
    if (!searchable) return options;
    if (!term) return showAllOnOpen ? options : options.filter((option) => !option.value || option.value === normalizedValue);
    return options.filter((option) => option.label.toLocaleUpperCase().includes(term) || option.value.toLocaleUpperCase().includes(term) || option.description?.toLocaleUpperCase().includes(term));
  }, [normalizedValue, options, searchText, searchable, showAllOnOpen]);
  const visibleOptions = searchable && optionLimit > 0 ? matchingOptions.slice(0, optionLimit) : matchingOptions;
  const groupedOptions = useMemo(() => {
    if (!visibleOptions.some((option) => option.group)) return null;
    const groups = new Map<string, Map<string, Array<{ index: number; option: InventoryFilterOption }>>>();
    visibleOptions.forEach((option, index) => {
      const group = option.group || "Other";
      const subgroup = option.subgroup || "Definitions";
      const subgroups = groups.get(group) ?? new Map<string, Array<{ index: number; option: InventoryFilterOption }>>();
      subgroups.set(subgroup, [...(subgroups.get(subgroup) ?? []), { index, option }]);
      groups.set(group, subgroups);
    });
    return groups;
  }, [visibleOptions]);
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
    const heightCap = (presentation === "catalog" ? 460 : 420) * overlayScale;
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
    const width = Math.min(window.innerWidth - 16, Math.max(rect.width, 170 * overlayScale, presentation === "catalog" ? 430 * overlayScale : hasDescriptions ? 380 * overlayScale : 210 * overlayScale, labelWidth + optionPadding + menuChrome + scrollbarAllowance + textSafety));
    setPlacement({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      maxHeight,
      top: openAbove ? Math.max(8, rect.top - renderedHeight - 4) : rect.bottom + 4,
      width,
    });
  }, [hasDescriptions, options, presentation]);

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
    setActiveIndex(Math.max(0, selectedIndex));
    const frame = window.requestAnimationFrame(() => searchable ? searchRef.current?.focus() : menuRef.current?.querySelector<HTMLButtonElement>(`[data-option-index="${Math.max(0, selectedIndex)}"]`)?.focus());
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
    if (!option || option.disabled) return;
    onChange(option.value);
    setOpen(false);
    window.requestAnimationFrame(() => buttonRef.current?.focus());
  }

  function move(nextIndex: number, direction: 1 | -1 = 1) {
    if (!visibleOptions.length) return;
    for (let offset = 0; offset < visibleOptions.length; offset += 1) {
      const bounded = (nextIndex + offset * direction + visibleOptions.length) % visibleOptions.length;
      if (visibleOptions[bounded]?.disabled) continue;
      setActiveIndex(bounded);
      menuRef.current?.querySelector<HTMLButtonElement>(`[data-option-index="${bounded}"]`)?.focus();
      return;
    }
  }

  function onButtonKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) return;
    event.preventDefault();
    setOpen(true);
  }

  function onOptionKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === "ArrowDown") { event.preventDefault(); move(index + 1); }
    else if (event.key === "ArrowUp") { event.preventDefault(); move(index - 1, -1); }
    else if (event.key === "Home") { event.preventDefault(); move(0); }
    else if (event.key === "End") { event.preventDefault(); move(visibleOptions.length - 1, -1); }
    else if (event.key === "Escape") { event.preventDefault(); setOpen(false); buttonRef.current?.focus(); }
    else if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(index); }
  }

  function onSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") { event.preventDefault(); move(0); }
    else if (event.key === "ArrowUp") { event.preventDefault(); move(visibleOptions.length - 1, -1); }
    else if (event.key === "Enter" && visibleOptions.length === 1) { event.preventDefault(); select(0); }
    else if (event.key === "Escape") { event.preventDefault(); setOpen(false); buttonRef.current?.focus(); }
  }

  return <>
    <button aria-controls={open ? menuId : undefined} aria-expanded={open} aria-haspopup="listbox" aria-label={ariaLabel} className={`inventory-filter-button${className ? ` ${className}` : ""}`} data-filter-active={normalizedValue !== normalizedDefaultValue ? "true" : undefined} data-has-selection={hasSelection ? "true" : "false"} disabled={disabled} onClick={() => setOpen((current) => !current)} onKeyDown={onButtonKeyDown} ref={buttonRef} type="button">
      <span>{selected?.label ?? placeholder ?? "Select"}</span><ChevronDown aria-hidden="true" size={12} />
    </button>
    {open ? createPortal(<div className="inventory-filter-menu" data-presentation={presentation} data-searchable={searchable ? "true" : undefined} ref={menuRef} style={placement}>
      {searchable ? <label className="inventory-filter-search"><span className="sr-only">Search {ariaLabel}</span>{presentation === "catalog" ? <Search aria-hidden="true" size={14} /> : null}<input aria-label={`Search ${ariaLabel}`} onChange={(event) => { setSearchText(event.target.value); setActiveIndex(0); }} onKeyDown={onSearchKeyDown} placeholder={searchPlaceholder} ref={searchRef} type="search" value={searchText} /></label> : null}
      <div aria-label={ariaLabel} className="inventory-filter-options" data-grouped={groupedOptions ? "true" : undefined} id={menuId} role="listbox">{groupedOptions ? [...groupedOptions.entries()].map(([group, subgroups]) => <section aria-label={group} className="inventory-filter-group" key={group} role="group"><header><strong>{group}</strong><span>{[...subgroups.values()].reduce((sum, rows) => sum + rows.length, 0)}</span></header>{[...subgroups.entries()].map(([subgroup, rows]) => <div aria-label={subgroup} className="inventory-filter-subgroup" key={subgroup} role="group"><div className="inventory-filter-subgroup-label"><span>{subgroup}</span><em>{rows.length}</em></div>{rows.map(({ index, option }) => <InventoryOptionButton activeIndex={activeIndex} index={index} key={option.value} normalizedValue={normalizedValue} onFocus={setActiveIndex} onKeyDown={onOptionKeyDown} onSelect={select} option={option} />)}</div>)}</section>) : visibleOptions.map((option, index) => <InventoryOptionButton activeIndex={activeIndex} index={index} key={option.value} normalizedValue={normalizedValue} onFocus={setActiveIndex} onKeyDown={onOptionKeyDown} onSelect={select} option={option} />)}</div>
      {searchable && !searchText.trim() && options.length > visibleOptions.length ? <span className="inventory-filter-hint">Type to search {options.length - 1} values</span> : null}
      {searchable && searchText.trim() && matchingOptions.length > visibleOptions.length ? <span className="inventory-filter-hint">Refine to narrow {matchingOptions.length} matches</span> : null}
      {searchable && !visibleOptions.length ? <span className="inventory-filter-empty">No matching {ariaLabel.toLowerCase()}</span> : null}
    </div>, document.body) : null}
  </>;
}

function InventoryOptionButton({ activeIndex, index, normalizedValue, onFocus, onKeyDown, onSelect, option }: { activeIndex: number; index: number; normalizedValue: string; onFocus: (index: number) => void; onKeyDown: (event: KeyboardEvent<HTMLButtonElement>, index: number) => void; onSelect: (index: number) => void; option: InventoryFilterOption }) {
  return <button aria-selected={option.value === normalizedValue} className="inventory-filter-option" data-detailed={option.description ? "true" : undefined} data-option-index={index} disabled={option.disabled} onClick={() => onSelect(index)} onFocus={() => onFocus(index)} onKeyDown={(event) => onKeyDown(event, index)} role="option" tabIndex={!option.disabled && activeIndex === index ? 0 : -1} type="button">{option.description ? <span><strong>{option.label}</strong><small>{option.description}</small></span> : option.label}</button>;
}
