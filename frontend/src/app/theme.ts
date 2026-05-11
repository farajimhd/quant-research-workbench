export type AppThemeId = "light";

type ThemeTokens = {
  accent: string;
  accentSoft: string;
  background: string;
  border: string;
  card: string;
  cardMuted: string;
  chromeBackground: string;
  chromeBorder: string;
  chromeMuted: string;
  chromeText: string;
  danger: string;
  divider: string;
  focusRing: string;
  foreground: string;
  muted: string;
  mutedForeground: string;
  popover: string;
  primary: string;
  primaryForeground: string;
  progressTrack: string;
  sidebar: string;
  sidebarAccent: string;
  sidebarForeground: string;
  sidebarPrimary: string;
  success: string;
  warning: string;
};

export type AppThemeDefinition = {
  description: string;
  label: string;
  themeId: AppThemeId;
  tokens: ThemeTokens;
};

export const APP_THEME: AppThemeDefinition = {
  themeId: "light",
  label: "Light",
  description: "The white-shell operations baseline shared with trading-dashboard.",
  tokens: {
    accent: "#e9ebef",
    accentSoft: "rgba(3, 2, 19, 0.06)",
    background: "#ffffff",
    border: "rgba(0, 0, 0, 0.1)",
    card: "#ffffff",
    cardMuted: "#f6f7f8",
    chromeBackground: "#ffffff",
    chromeBorder: "rgba(0, 0, 0, 0.08)",
    chromeMuted: "#717182",
    chromeText: "#030213",
    danger: "#d4183d",
    divider: "rgba(0, 0, 0, 0.1)",
    focusRing: "#030213",
    foreground: "#030213",
    muted: "#ececf0",
    mutedForeground: "#717182",
    popover: "#ffffff",
    primary: "#030213",
    primaryForeground: "#ffffff",
    progressTrack: "#ececf0",
    sidebar: "#fafafa",
    sidebarAccent: "#f3f4f6",
    sidebarForeground: "#030213",
    sidebarPrimary: "#030213",
    success: "#067647",
    warning: "#b54708"
  }
};

const TOKEN_TO_VARIABLE: Record<keyof ThemeTokens, string> = {
  accent: "--accent",
  accentSoft: "--accent-soft",
  background: "--background",
  border: "--border",
  card: "--card",
  cardMuted: "--card-muted",
  chromeBackground: "--shell-bg",
  chromeBorder: "--shell-border",
  chromeMuted: "--shell-muted",
  chromeText: "--shell-text",
  danger: "--danger",
  divider: "--divider",
  focusRing: "--focus-ring",
  foreground: "--foreground",
  muted: "--muted",
  mutedForeground: "--muted-foreground",
  popover: "--popover",
  primary: "--primary",
  primaryForeground: "--primary-foreground",
  progressTrack: "--progress-track",
  sidebar: "--sidebar",
  sidebarAccent: "--sidebar-accent",
  sidebarForeground: "--sidebar-foreground",
  sidebarPrimary: "--sidebar-primary",
  success: "--success",
  warning: "--warning"
};

export function applyThemeDefinition(target: HTMLElement = document.documentElement): void {
  for (const [token, value] of Object.entries(APP_THEME.tokens) as Array<[keyof ThemeTokens, string]>) {
    target.style.setProperty(TOKEN_TO_VARIABLE[token], value);
  }
  target.dataset.theme = APP_THEME.themeId;
}
