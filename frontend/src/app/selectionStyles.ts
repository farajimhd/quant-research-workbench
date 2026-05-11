export const SHARED_HOVER_SURFACE_CLASS_NAME = "shared-selection-surface-hoverable";
export const SHARED_SELECTED_SURFACE_CLASS_NAME = "shared-selection-surface-selected";
export const SHARED_HOVER_SURFACE_TOKEN_CLASS_NAME = SHARED_HOVER_SURFACE_CLASS_NAME;
export const SHARED_SELECTED_SURFACE_TOKEN_CLASS_NAME = SHARED_SELECTED_SURFACE_CLASS_NAME;
export const SHARED_SELECTED_EMPHASIS_TOKEN_CLASS_NAME = "shared-selection-emphasis";
export const PRIMARY_ACTION_BUTTON_SURFACE_CLASS_NAME = "shared-button-surface-primary";
export const DESTRUCTIVE_ACTION_BUTTON_SURFACE_CLASS_NAME = "shared-button-surface-destructive";
export const GHOST_ACTION_BUTTON_SURFACE_CLASS_NAME = "shared-button-surface-ghost";
export const SUBTLE_ACTION_BUTTON_CLASS_NAME = `subtle-action-button ${SHARED_HOVER_SURFACE_CLASS_NAME}`;
export const ICON_ACTION_BUTTON_CLASS_NAME = `toolbar-button ${SHARED_HOVER_SURFACE_CLASS_NAME}`;

export function buildSegmentButtonClassName(selected: boolean): string {
  return selected ? `timeframe-button active ${SHARED_SELECTED_SURFACE_CLASS_NAME}` : `timeframe-button ${SHARED_HOVER_SURFACE_CLASS_NAME}`;
}

export function buildMenuItemButtonClassName(selected: boolean): string {
  return selected ? `nav-item active ${SHARED_SELECTED_SURFACE_CLASS_NAME}` : `nav-item ${SHARED_HOVER_SURFACE_CLASS_NAME}`;
}

export function buildThemeMenuItemButtonClassName(selected: boolean): string {
  return selected ? `theme-menu-item active ${SHARED_SELECTED_SURFACE_CLASS_NAME}` : `theme-menu-item ${SHARED_HOVER_SURFACE_CLASS_NAME}`;
}
