export const PAGE_KEYS = [
  "real-live-trading",
  "replay-trading",
  "backtest-trading",
  "backtest-debug",
  "research-workspace",
  "canvas-configuration",
  "data-catalog-configuration",
  "rule-set-configuration",
  "market-discovery-configuration",
  "typography-public-sans",
  "trading-action-configuration",
  "strategy-configuration",
  "assignment-configuration",
  "portfolio-configuration",
  "oms-configuration",
  "account-configuration",
  "revision-configuration",
  "canvas-focus",
  "services-dashboard",
  "service-bar-gpt",
  "service-ibkr",
  "service-model-gateway",
  "service-news",
  "service-news-hypothesis",
  "service-qmd",
  "service-qmd-history",
  "service-reference",
  "service-sec",
  "service-text-embed",
  "service-text-intelligence",
] as const;

export type PageKey = (typeof PAGE_KEYS)[number];
export type TradingConfigurationSection =
  | "data_catalog"
  | "rule_sets"
  | "actions"
  | "strategy"
  | "discovery"
  | "assignments"
  | "portfolio"
  | "oms"
  | "accounts"
  | "revisions";
export type ServiceId =
  | "bar-gpt"
  | "ibkr"
  | "model-gateway"
  | "news"
  | "news-hypothesis"
  | "qmd"
  | "qmd-history"
  | "reference"
  | "sec"
  | "text-embed"
  | "text-intelligence";
export type ServicePageMode = "dashboard" | ServiceId;

export type NavigationIcon =
  | "accounts"
  | "backtest"
  | "canvas"
  | "data"
  | "debug"
  | "live"
  | "market-discovery"
  | "oms"
  | "portfolio"
  | "releases"
  | "replay"
  | "research"
  | "run-plans"
  | "services"
  | "strategy"
  | "trading-actions"
  | "typography";

export type NavigationGroup = {
  items: readonly { icon: NavigationIcon; key: PageKey; label: string }[];
  label: string;
};

export const NAVIGATION_GROUPS = [
  {
    label: "Trading Workspaces",
    items: [
      { key: "real-live-trading", label: "Live", icon: "live" },
      { key: "replay-trading", label: "Replay", icon: "replay" },
      { key: "backtest-trading", label: "Backtest", icon: "backtest" },
      { key: "backtest-debug", label: "Debug", icon: "debug" },
      { key: "research-workspace", label: "Research", icon: "research" },
    ],
  },
  {
    label: "Canvas Configuration",
    items: [{ key: "canvas-configuration", label: "Canvas", icon: "canvas" }],
  },
  {
    label: "Data Configuration",
    items: [
      { key: "data-catalog-configuration", label: "Data Catalog", icon: "data" },
      { key: "rule-set-configuration", label: "Rule Sets", icon: "data" },
      { key: "market-discovery-configuration", label: "Market Discovery", icon: "market-discovery" },
    ],
  },
  {
    label: "System Configuration",
    items: [
      { key: "trading-action-configuration", label: "Trading Actions", icon: "trading-actions" },
      { key: "strategy-configuration", label: "Strategy Studio", icon: "strategy" },
      { key: "assignment-configuration", label: "Run Plans", icon: "run-plans" },
      { key: "portfolio-configuration", label: "Portfolio & Risk", icon: "portfolio" },
      { key: "oms-configuration", label: "OMS & Protection", icon: "oms" },
      { key: "account-configuration", label: "Accounts & Sessions", icon: "accounts" },
      { key: "revision-configuration", label: "Test Candidates", icon: "releases" },
    ],
  },
  {
    label: "System",
    items: [{ key: "services-dashboard", label: "Service Health", icon: "services" }],
  },
  {
    label: "Typography System",
    items: [{ key: "typography-public-sans", label: "Public Sans Roles", icon: "typography" }],
  },
] as const satisfies readonly NavigationGroup[];

export const SERVICE_IDS: readonly ServiceId[] = [
  "qmd",
  "qmd-history",
  "bar-gpt",
  "news",
  "text-intelligence",
  "news-hypothesis",
  "model-gateway",
  "sec",
  "text-embed",
  "reference",
  "ibkr",
];

const PAGE_KEY_SET = new Set<string>(PAGE_KEYS);
const CONFIGURATION_SECTIONS: Partial<Record<PageKey, TradingConfigurationSection>> = {
  "data-catalog-configuration": "data_catalog",
  "rule-set-configuration": "rule_sets",
  "market-discovery-configuration": "discovery",
  "trading-action-configuration": "actions",
  "strategy-configuration": "strategy",
  "assignment-configuration": "assignments",
  "portfolio-configuration": "portfolio",
  "oms-configuration": "oms",
  "account-configuration": "accounts",
  "revision-configuration": "revisions",
};

export function isPageKey(value: string): value is PageKey {
  return PAGE_KEY_SET.has(value);
}

export function pageFromHash(hash: string): PageKey | null {
  const value = hash.replace(/^#/, "").split("?", 1)[0];
  return isPageKey(value) ? value : null;
}

export function configurationSectionForPage(page: PageKey): TradingConfigurationSection | null {
  return CONFIGURATION_SECTIONS[page] ?? null;
}

export function servicePageModeForPage(page: PageKey): ServicePageMode | null {
  if (page === "services-dashboard") return "dashboard";
  if (!page.startsWith("service-")) return null;
  const serviceId = page.slice("service-".length);
  return SERVICE_IDS.includes(serviceId as ServiceId) ? serviceId as ServiceId : null;
}

export function pageForServiceMode(mode: ServicePageMode): PageKey {
  return mode === "dashboard" ? "services-dashboard" : `service-${mode}`;
}

export function isCompactContentPage(page: PageKey) {
  return page === "canvas-configuration"
    || page === "replay-trading"
    || page === "backtest-trading"
    || page === "backtest-debug";
}

export function configurationToneForPage(page: PageKey) {
  if (page === "canvas-configuration") return "canvas";
  if (page === "data-catalog-configuration" || page === "rule-set-configuration" || page === "market-discovery-configuration" || page === "typography-public-sans") return "discovery";
  if (page === "trading-action-configuration" || page === "strategy-configuration") return "strategy";
  if (page === "assignment-configuration") return "assignments";
  if (page === "portfolio-configuration") return "portfolio";
  if (page === "oms-configuration") return "oms";
  if (page === "account-configuration") return "accounts";
  if (page === "revision-configuration") return "revisions";
  return undefined;
}
