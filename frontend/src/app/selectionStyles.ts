export const SHARED_HOVER_SURFACE_CLASS_NAME = "shared-selection-surface-hoverable";
export const SHARED_SELECTED_SURFACE_CLASS_NAME = "shared-selection-surface-selected";
export const PRIMARY_ACTION_BUTTON_SURFACE_CLASS_NAME = "shared-button-surface-primary";
export const DESTRUCTIVE_ACTION_BUTTON_SURFACE_CLASS_NAME = "shared-button-surface-destructive";
export const GHOST_ACTION_BUTTON_SURFACE_CLASS_NAME = "shared-button-surface-ghost";

export function buildSegmentButtonClassName(selected: boolean): string {
  return selected ? `timeframe-button active ${SHARED_SELECTED_SURFACE_CLASS_NAME}` : `timeframe-button ${SHARED_HOVER_SURFACE_CLASS_NAME}`;
}

export function buildMenuItemButtonClassName(selected: boolean): string {
  return selected ? `nav-item active ${SHARED_SELECTED_SURFACE_CLASS_NAME}` : `nav-item ${SHARED_HOVER_SURFACE_CLASS_NAME}`;
}
