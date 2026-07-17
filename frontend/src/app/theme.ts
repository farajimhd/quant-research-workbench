export type AppThemeId =
  | "light"
  | "slate"
  | "parchment"
  | "dawn"
  | "harbor"
  | "dark"
  | "forest"
  | "graphite"
  | "ember"
  | "amethyst";

type AppThemeTone = "dark" | "light";

type BasePalette = {
  background: string;
  border: string;
  card: string;
  foreground: string;
  muted: string;
  mutedForeground: string;
  primary: string;
  secondary: string;
  sidebar: string;
  sidebarForeground: string;
};

type AppThemeTokenMap = {
  accent: string;
  accentSoft: string;
  background: string;
  border: string;
  card: string;
  cardMuted: string;
  chartAfterHours: string;
  chartPremarket: string;
  canvasLinkGroups: readonly [string, string, string, string, string, string, string];
  metricAccents: readonly [string, string, string, string];
  chromeBackground: string;
  chromeBorder: string;
  chromeMuted: string;
  chromeShadow: string;
  chromeText: string;
  controlBackground: string;
  danger: string;
  divider: string;
  focusRing: string;
  foreground: string;
  menuBackground: string;
  menuShadow: string;
  marketPrice: string;
  marketRate: string;
  muted: string;
  mutedForeground: string;
  newsCold: string;
  newsHot: string;
  newsOld: string;
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
  tone: AppThemeTone;
  tokens: AppThemeTokenMap;
};

const BODY_FONT_STACK = "\"Inter\", \"Segoe UI Variable Text\", \"Segoe UI\", sans-serif";
const DISPLAY_FONT_STACK = "\"Inter\", \"Segoe UI Variable Display\", \"Segoe UI\", sans-serif";

const paletteDefinitions: Array<{
  description: string;
  label: string;
  palette: BasePalette;
  themeId: AppThemeId;
  tone: AppThemeTone;
}> = [
  {
    themeId: "light",
    label: "Light",
    tone: "light",
    description: "The primary white-shell operations theme used as the product baseline.",
    palette: {
      background: "#ffffff",
      border: "rgba(0, 0, 0, 0.1)",
      card: "#ffffff",
      foreground: "#030213",
      muted: "#ececf0",
      mutedForeground: "#717182",
      primary: "#030213",
      secondary: "#f6f7f8",
      sidebar: "#fafafa",
      sidebarForeground: "#030213"
    }
  },
  {
    themeId: "slate",
    label: "Slate",
    tone: "light",
    description: "A cool editorial light theme with steel-blue structure and restrained contrast.",
    palette: {
      background: "#f2f6fb",
      border: "#d6e0ec",
      card: "#fbfdff",
      foreground: "#142334",
      muted: "#e7eef6",
      mutedForeground: "#607489",
      primary: "#2f5f95",
      secondary: "#e7eef6",
      sidebar: "#ebf2f9",
      sidebarForeground: "#142334"
    }
  },
  {
    themeId: "parchment",
    label: "Parchment",
    tone: "light",
    description: "A warm editorial light theme with parchment neutrals and brass emphasis.",
    palette: {
      background: "#fbf4e8",
      border: "#dbcdb6",
      card: "#fff9ef",
      foreground: "#34281a",
      muted: "#f2e7d5",
      mutedForeground: "#7d6952",
      primary: "#9c6534",
      secondary: "#f2e7d5",
      sidebar: "#f8eedf",
      sidebarForeground: "#34281a"
    }
  },
  {
    themeId: "dawn",
    label: "Dawn",
    tone: "light",
    description: "A blush-toned light theme with rose accents and softer editorial warmth.",
    palette: {
      background: "#fff1f5",
      border: "#ecd6df",
      card: "#fff7fa",
      foreground: "#3a222b",
      muted: "#f8e6ee",
      mutedForeground: "#8a6672",
      primary: "#c14e7a",
      secondary: "#f8e6ee",
      sidebar: "#fdf0f5",
      sidebarForeground: "#3a222b"
    }
  },
  {
    themeId: "harbor",
    label: "Harbor",
    tone: "light",
    description: "A sea-glass light theme with mint-aqua emphasis and cleaner coastal contrast.",
    palette: {
      background: "#eefaf6",
      border: "#cfe5dc",
      card: "#f9fffc",
      foreground: "#163129",
      muted: "#e1f2ec",
      mutedForeground: "#5d7c73",
      primary: "#1c8a73",
      secondary: "#e1f2ec",
      sidebar: "#e9f7f1",
      sidebarForeground: "#163129"
    }
  },
  {
    themeId: "dark",
    label: "Dark",
    tone: "dark",
    description: "A curated VS Code-style neutral dark variant with vivid semantic emphasis.",
    palette: {
      background: "#16181d",
      border: "#313740",
      card: "#1c2027",
      foreground: "#f3f5f7",
      muted: "#232830",
      mutedForeground: "#b7c0cb",
      primary: "#e6c06a",
      secondary: "#232830",
      sidebar: "#14171c",
      sidebarForeground: "#f3f5f7"
    }
  },
  {
    themeId: "forest",
    label: "Forest",
    tone: "dark",
    description: "A curated evergreen dark variant with brighter mint contrast for lower-glare monitoring work.",
    palette: {
      background: "#0b120e",
      border: "#24382d",
      card: "#111914",
      foreground: "#ecf6ef",
      muted: "#18211c",
      mutedForeground: "#a8c5b1",
      primary: "#45e0a9",
      secondary: "#18211c",
      sidebar: "#09100c",
      sidebarForeground: "#ecf6ef"
    }
  },
  {
    themeId: "graphite",
    label: "Graphite",
    tone: "dark",
    description: "A cool graphite dark theme with cyan emphasis and sharply neutral surfaces.",
    palette: {
      background: "#0f1318",
      border: "#28323b",
      card: "#151b23",
      foreground: "#edf3f8",
      muted: "#1d2630",
      mutedForeground: "#a5b2bf",
      primary: "#65c7ff",
      secondary: "#1d2630",
      sidebar: "#0c1015",
      sidebarForeground: "#edf3f8"
    }
  },
  {
    themeId: "ember",
    label: "Ember",
    tone: "dark",
    description: "A warm dark theme built around ember reds and clear ivory text for night monitoring.",
    palette: {
      background: "#120c0b",
      border: "#372622",
      card: "#17100f",
      foreground: "#f7eeeb",
      muted: "#221716",
      mutedForeground: "#c7afa9",
      primary: "#ff8a63",
      secondary: "#221716",
      sidebar: "#0f0908",
      sidebarForeground: "#f7eeeb"
    }
  },
  {
    themeId: "amethyst",
    label: "Amethyst",
    tone: "dark",
    description: "A plum-toned dark theme with neon-violet emphasis and low-glare dark surfaces.",
    palette: {
      background: "#0d0a13",
      border: "#2d2438",
      card: "#15101d",
      foreground: "#f3eef8",
      muted: "#201829",
      mutedForeground: "#b6abc6",
      primary: "#c77dff",
      secondary: "#201829",
      sidebar: "#0a0810",
      sidebarForeground: "#f3eef8"
    }
  }
];

export const APP_THEMES: readonly AppThemeDefinition[] = paletteDefinitions.map(buildTheme);
export const DEFAULT_THEME_ID: AppThemeId = "light";

export function isAppThemeId(value: string): value is AppThemeId {
  return APP_THEMES.some((theme) => theme.themeId === value);
}

export function getThemeDefinition(themeId: AppThemeId): AppThemeDefinition {
  const theme = APP_THEMES.find((candidate) => candidate.themeId === themeId);
  if (!theme) throw new Error(`Theme '${themeId}' is not registered.`);
  return theme;
}

export function applyThemeDefinition(target: HTMLElement, themeId: AppThemeId = DEFAULT_THEME_ID): void {
  const theme = getThemeDefinition(themeId);
  const tokens = theme.tokens;
  const variables: Record<string, string> = {
    "--accent": tokens.accent,
    "--accent-foreground": tokens.foreground,
    "--accent-soft": tokens.accentSoft,
    "--background": tokens.background,
    "--border": tokens.border,
    "--card": tokens.card,
    "--card-foreground": tokens.foreground,
    "--card-muted": tokens.cardMuted,
    "--canvas-link-blue": tokens.canvasLinkGroups[0],
    "--canvas-link-green": tokens.canvasLinkGroups[1],
    "--canvas-link-amber": tokens.canvasLinkGroups[2],
    "--canvas-link-violet": tokens.canvasLinkGroups[3],
    "--canvas-link-rose": tokens.canvasLinkGroups[4],
    "--canvas-link-cyan": tokens.canvasLinkGroups[5],
    "--canvas-link-orange": tokens.canvasLinkGroups[6],
    "--metric-accent-1": tokens.metricAccents[0],
    "--metric-accent-2": tokens.metricAccents[1],
    "--metric-accent-3": tokens.metricAccents[2],
    "--metric-accent-4": tokens.metricAccents[3],
    "--control-bg": tokens.controlBackground,
    "--danger": tokens.danger,
    "--destructive": tokens.danger,
    "--destructive-foreground": "#ffffff",
    "--divider": tokens.divider,
    "--focus-ring": tokens.focusRing,
    "--font-body": BODY_FONT_STACK,
    "--font-display": DISPLAY_FONT_STACK,
    "--foreground": tokens.foreground,
    "--input": theme.tone === "light" ? "transparent" : tokens.muted,
    "--input-background": tokens.controlBackground,
    "--menu-bg": tokens.menuBackground,
    "--menu-shadow": tokens.menuShadow,
    "--market-price": tokens.marketPrice,
    "--market-rate": tokens.marketRate,
    "--muted": tokens.muted,
    "--muted-foreground": tokens.mutedForeground,
    // Product-wide news-temperature contract: hot is neon red, cold is neon blue,
    // and old is neutral. News UI must use these tokens, never success/danger/info.
    "--news-cold": tokens.newsCold,
    "--news-hot": tokens.newsHot,
    "--news-old": tokens.newsOld,
    "--page-bg": tokens.background,
    "--popover": tokens.popover,
    "--popover-foreground": tokens.foreground,
    "--primary": tokens.primary,
    "--primary-foreground": tokens.primaryForeground,
    "--progress-track": tokens.progressTrack,
    "--ring": tokens.focusRing,
    "--secondary": tokens.cardMuted,
    "--secondary-foreground": tokens.foreground,
    "--shell-bg": tokens.chromeBackground,
    "--shell-border": tokens.chromeBorder,
    "--shell-muted": tokens.chromeMuted,
    "--shell-shadow": tokens.chromeShadow,
    "--shell-text": tokens.chromeText,
    "--sidebar": tokens.sidebar,
    "--sidebar-accent": tokens.sidebarAccent,
    "--sidebar-accent-foreground": tokens.sidebarForeground,
    "--sidebar-bg": tokens.sidebar,
    "--sidebar-border": tokens.chromeBorder,
    "--sidebar-foreground": tokens.sidebarForeground,
    "--sidebar-hover-bg": tokens.sidebarAccent,
    "--sidebar-primary": tokens.sidebarPrimary,
    "--sidebar-primary-foreground": tokens.primaryForeground,
    "--sidebar-section-text": tokens.mutedForeground,
    "--success": tokens.success,
    "--surface": tokens.card,
    "--surface-alt": tokens.cardMuted,
    "--surface-border": tokens.border,
    "--surface-shadow": tokens.chromeShadow,
    "--surface-strong": tokens.card,
    "--switch-background": tokens.border,
    "--text-muted": tokens.mutedForeground,
    "--text-primary": tokens.foreground,
    "--warning": tokens.warning,
    "--badge-success-bg": withOpacity(tokens.success, theme.tone === "light" ? "0.10" : "0.16"),
    "--badge-success-border": withOpacity(tokens.success, theme.tone === "light" ? "0.18" : "0.32"),
    "--badge-success-fg": tokens.success,
    "--badge-danger-bg": withOpacity(tokens.danger, theme.tone === "light" ? "0.10" : "0.16"),
    "--badge-danger-border": withOpacity(tokens.danger, theme.tone === "light" ? "0.20" : "0.34"),
    "--badge-danger-fg": tokens.danger,
    "--badge-info-bg": withOpacity(tokens.primary, theme.tone === "light" ? "0.10" : "0.16"),
    "--badge-info-border": withOpacity(tokens.primary, theme.tone === "light" ? "0.20" : "0.34"),
    "--badge-info-fg": tokens.primary,
    "--badge-warning-bg": withOpacity(tokens.warning, theme.tone === "light" ? "0.12" : "0.16"),
    "--badge-warning-border": withOpacity(tokens.warning, theme.tone === "light" ? "0.22" : "0.34"),
    "--badge-warning-fg": tokens.warning,
    "--badge-muted-bg": withOpacity(tokens.mutedForeground, theme.tone === "light" ? "0.08" : "0.12"),
    "--badge-muted-border": withOpacity(tokens.mutedForeground, theme.tone === "light" ? "0.16" : "0.26"),
    "--badge-muted-fg": tokens.mutedForeground,
    "--badge-neutral-bg": withOpacity(tokens.mutedForeground, theme.tone === "light" ? "0.10" : "0.15"),
    "--badge-neutral-border": withOpacity(tokens.mutedForeground, theme.tone === "light" ? "0.18" : "0.28"),
    "--badge-neutral-fg": tokens.mutedForeground,
    "--chart-background": tokens.card,
    "--chart-after-hours": tokens.chartAfterHours,
    "--chart-grid": mix(tokens.border, tokens.card, theme.tone === "light" ? 0.5 : 0.8),
    "--chart-premarket": tokens.chartPremarket,
    "--chart-text": tokens.mutedForeground
  };

  for (const [name, value] of Object.entries(variables)) {
    target.style.setProperty(name, value);
  }
  target.classList.remove(...APP_THEMES.map((candidate) => candidate.themeId));
  target.classList.add(theme.themeId);
  target.dataset.shellTheme = theme.themeId;
  target.style.colorScheme = theme.tone;
}

function buildTheme({
  description,
  label,
  palette,
  themeId,
  tone
}: {
  description: string;
  label: string;
  palette: BasePalette;
  themeId: AppThemeId;
  tone: AppThemeTone;
}): AppThemeDefinition {
  const success = tone === "light" ? "#1f9d55" : themeId === "dark" ? "#4ade80" : "#56f1bb";
  const warning = tone === "light" ? "#6d28d9" : "#ffd166";
  const danger = tone === "light" ? "#c4324f" : "#ff8f8f";
  return {
    themeId,
    label,
    tone,
    description,
    tokens: {
      accent: palette.primary,
      accentSoft: tone === "light" ? "rgba(3, 2, 19, 0.06)" : withOpacity(palette.primary, "0.14"),
      background: palette.background,
      border: palette.border,
      card: palette.card,
      cardMuted: tone === "light" ? palette.secondary : palette.muted,
      chartAfterHours: tone === "light" ? "#78b8e8" : "#5ba8df",
      chartPremarket: tone === "light" ? "#f2a65a" : "#f0a85f",
      canvasLinkGroups: tone === "light"
        ? ["#007dff", "#00c853", "#d6b000", "#8f00ff", "#ff1493", "#00a6a6", "#ff5a00"]
        : ["#00c8ff", "#39ff14", "#ffee00", "#bf5fff", "#ff3bd4", "#00ffd5", "#ff7a00"],
      metricAccents: tone === "light"
        ? ["#008143", "#00758a", "#6d3fd1", "#006edc"]
        : ["#56f1bb", "#00d8ff", "#c68cff", "#65c7ff"],
      chromeBackground: withOpacity(palette.background, tone === "light" ? "0.95" : "0.92"),
      chromeBorder: palette.border,
      chromeMuted: palette.mutedForeground,
      chromeShadow: tone === "light" ? "0 10px 30px rgba(15, 23, 42, 0.04)" : "0 18px 40px rgba(2, 6, 23, 0.34)",
      chromeText: palette.foreground,
      controlBackground: tone === "light" ? "#ffffff" : palette.secondary,
      danger,
      divider: palette.border,
      focusRing: tone === "light" ? "rgba(3, 2, 19, 0.14)" : withOpacity(palette.primary, "0.22"),
      foreground: palette.foreground,
      menuBackground: palette.card,
      menuShadow: tone === "light" ? "0 18px 42px rgba(15, 23, 42, 0.12)" : "0 18px 42px rgba(2, 6, 23, 0.34)",
      // Financial text uses type color, never directional red/green: blue marks
      // money and price values; violet marks percentages, rates, and basis points.
      marketPrice: tone === "light" ? "#006edc" : "#65c7ff",
      marketRate: tone === "light" ? "#6d3fd1" : "#c68cff",
      muted: palette.muted,
      mutedForeground: palette.mutedForeground,
      newsCold: tone === "light" ? "#007dff" : "#00c8ff",
      newsHot: tone === "light" ? "#ff1744" : "#ff3b5c",
      newsOld: palette.mutedForeground,
      popover: palette.card,
      primary: palette.primary,
      primaryForeground: "#ffffff",
      progressTrack: tone === "light" ? "rgba(113, 113, 130, 0.16)" : withOpacity(palette.primary, "0.16"),
      sidebar: palette.sidebar,
      sidebarAccent: tone === "light" ? palette.secondary : withOpacity(palette.primary, "0.10"),
      sidebarForeground: palette.sidebarForeground,
      sidebarPrimary: palette.primary,
      success,
      warning
    }
  };
}

function withOpacity(color: string, opacity: string): string {
  if (color.startsWith("rgba(") || color.startsWith("rgb(")) return color;
  if (!color.startsWith("#") || color.length !== 7) return color;
  const red = Number.parseInt(color.slice(1, 3), 16);
  const green = Number.parseInt(color.slice(3, 5), 16);
  const blue = Number.parseInt(color.slice(5, 7), 16);
  return `rgba(${red}, ${green}, ${blue}, ${opacity})`;
}

function mix(first: string, fallback: string, opacity: number): string {
  const value = first.startsWith("rgba(") || first.startsWith("rgb(") ? first : withOpacity(first, String(opacity));
  return value === first && !first.startsWith("rgba(") ? fallback : value;
}
