import {
  Activity,
  BarChart3,
  Bell,
  BookOpenText,
  CheckCircle2,
  ChevronDown,
  Code2,
  Database,
  LayoutPanelLeft,
  Search,
  Table2,
  ToggleRight,
  Type,
} from "lucide-react";
import type { ReactNode } from "react";

const TYPE_ROLES = [
  ["Navigation", "Public Sans", "Sidebar groups, destinations, tabs, breadcrumbs"],
  ["Page hierarchy", "Public Sans", "Page titles, section headings, card headings"],
  ["Reading text", "Public Sans", "Descriptions, instructions, evidence, empty states"],
  ["Controls", "Public Sans", "Buttons, inputs, lookups, filters, menus"],
  ["Tables", "Public Sans", "Headers, symbols, companies, row values"],
  ["Financial numbers", "Public Sans", "Prices, quantities, percentages, timestamps"],
  ["Status and events", "Public Sans", "State labels, alerts, freshness, lifecycle messages"],
  ["Charts", "Public Sans", "Axes, legends, markers, tooltips"],
  ["Technical identifiers", "JetBrains Mono", "Canonical IDs, source fields, code, payload keys"],
] as const;

export function TypographyPublicSansPage() {
  return (
    <div className="typography-system-page">
      <header className="typography-system-header">
        <div>
          <span>Typography system · approval specimen</span>
          <h1>Public Sans role map</h1>
          <p>One human-facing family across the application. Monospace remains reserved for machine-facing identifiers.</p>
        </div>
        <div className="typography-family-summary" aria-label="Approved font families">
          <span><strong>Aa</strong><small>Interface</small><em>Public Sans</em></span>
          <span className="technical"><strong>01</strong><small>Technical</small><em>JetBrains Mono</em></span>
        </div>
      </header>

      <main className="typography-system-content">
        <section className="typography-role-map" aria-labelledby="role-map-heading">
          <header>
            <div><Type size={17} /><span><strong id="role-map-heading">Required typography roles</strong><small>The role selects the treatment; individual pages do not select fonts.</small></span></div>
            <em>9 roles · 2 families</em>
          </header>
          <div>
            {TYPE_ROLES.map(([role, family, usage]) => (
              <article className={family === "JetBrains Mono" ? "technical" : undefined} key={role}>
                <span>{role}</span><strong>{family}</strong><p>{usage}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="typography-mix-options" aria-labelledby="type-mix-heading">
          <header>
            <div><Type size={17} /><span><strong id="type-mix-heading">Purposeful font mixes</strong><small>Public Sans remains the interface and numeric authority in every option.</small></span></div>
            <em>Compare the role changes, not isolated alphabets</em>
          </header>
          <div>
            <TypeMix
              className="unified"
              description="The quietest and most consistent system. Best when operational density should dominate brand character."
              display="Public Sans"
              reading="Public Sans"
              title="Unified interface"
            />
            <TypeMix
              className="analytical"
              description="Adds a restrained geometric voice to page titles while preserving Public Sans everywhere users scan or act."
              display="Sora"
              reading="Public Sans"
              title="Analytical hierarchy"
            />
            <TypeMix
              className="editorial"
              description="Uses a serif only for long filings, research notes, and narrative evidence; operational UI remains unchanged."
              display="Public Sans"
              reading="Source Serif 4"
              title="Research reading"
            />
          </div>
        </section>

        <section className="typography-specimen-grid" aria-label="Typography role specimens">
          <Specimen icon={<LayoutPanelLeft size={17} />} title="Navigation" note="13 px · regular and medium">
            <div className="type-sidebar-sample">
              <small>DATA CONFIGURATION</small>
              <span><Database size={15} /> Data Catalog</span>
              <span className="active"><BookOpenText size={15} /> Rule Sets</span>
              <span><Activity size={15} /> Market Discovery</span>
            </div>
          </Specimen>

          <Specimen icon={<Type size={17} />} title="Page hierarchy" note="11–26 px · medium to semibold">
            <div className="type-heading-sample">
              <small>DATA CONFIGURATION · REUSABLE DECISIONS</small>
              <h2>Rule Set Library</h2>
              <p>Inspect atomic defaults and compose editable rule sets from registered data definitions.</p>
              <h3>Condition logic</h3>
            </div>
          </Specimen>

          <Specimen icon={<BookOpenText size={17} />} title="Reading text" note="13–15 px · regular">
            <div className="type-reading-sample">
              <strong>Why this field is available</strong>
              <p>Last price is the most recent eligible trade available at the selected market clock. Values remain unavailable when causal coverage is incomplete.</p>
              <small>Supporting copy remains quieter without becoming smaller than the readable floor.</small>
            </div>
          </Specimen>

          <Specimen icon={<ToggleRight size={17} />} title="Controls and lookups" note="13–14 px · regular and medium">
            <div className="type-control-sample">
              <button type="button"><span>Any sentiment</span><ChevronDown size={15} /></button>
              <button className="selected" type="button"><span>Last price</span><ChevronDown size={15} /></button>
              <label><Search size={15} /><input aria-label="Search definitions specimen" placeholder="Search data definitions" /></label>
            </div>
          </Specimen>

          <Specimen icon={<Table2 size={17} />} title="Tables and identities" note="11–14 px · aligned by meaning" wide>
            <div className="type-table-sample">
              <div className="head"><span>SYMBOL</span><span>LAST PRICE</span><span>CHANGE</span><span>VOLUME</span><span>MARKET CAP</span></div>
              <div><span><strong>AAPL</strong><small>APPLE INC</small></span><span>$231.42</span><span className="positive">+1.84%</span><span>42,817,306</span><span>$3.48T</span></div>
              <div><span><strong>NVDA</strong><small>NVIDIA CORP</small></span><span>$182.96</span><span className="negative">−0.72%</span><span>187,204,115</span><span>$4.46T</span></div>
            </div>
          </Specimen>

          <Specimen icon={<BarChart3 size={17} />} title="Financial numbers" note="13–18 px · tabular lining figures">
            <div className="type-metric-sample">
              <span><small>NET LIQUIDATION</small><strong>$102,438.42</strong></span>
              <span><small>UNREALIZED P&amp;L</small><strong className="positive">+$316.00</strong></span>
              <span><small>MARKET CLOCK</small><strong>09:45:12 ET</strong></span>
            </div>
          </Specimen>

          <Specimen icon={<Bell size={17} />} title="Status and events" note="11–13 px · medium with semantic color">
            <div className="type-status-sample">
              <span><CheckCircle2 size={14} /> Complete broker state</span>
              <p><i /> QMD Watchlist membership resolved <time>09:45:12 ET</time></p>
              <small>Fresh · updated 8 seconds ago</small>
            </div>
          </Specimen>

          <Specimen icon={<Activity size={17} />} title="Chart text" note="10–12 px · compact and tabular">
            <div className="type-chart-sample">
              <div><span>VWAP</span><strong>231.78</strong></div>
              <div><span>QMD SIGNAL</span><strong className="positive">0.84</strong></div>
              <p><span>09:30</span><span>09:35</span><span>09:40</span><span>09:45</span></p>
            </div>
          </Specimen>

          <Specimen icon={<Code2 size={17} />} title="Technical identifiers" note="12 px · JetBrains Mono only" wide technical>
            <div className="type-technical-sample">
              <span><small>Canonical field</small><code>market.last_price</code></span>
              <span><small>Producer contract</small><code>qmd.scanner.snapshot.v1</code></span>
              <span><small>Source column</small><code>market_sip_compact.events_YYYY.price</code></span>
            </div>
          </Specimen>
        </section>
      </main>
    </div>
  );
}

function Specimen({ children, icon, note, technical = false, title, wide = false }: { children: ReactNode; icon: ReactNode; note: string; technical?: boolean; title: string; wide?: boolean }) {
  return (
    <article className={`typography-specimen${wide ? " wide" : ""}${technical ? " technical" : ""}`}>
      <header><span>{icon}<strong>{title}</strong></span><small>{note}</small></header>
      <div>{children}</div>
    </article>
  );
}

function TypeMix({ className, description, display, reading, title }: { className: string; description: string; display: string; reading: string; title: string }) {
  return (
    <article className={`typography-mix-card ${className}`}>
      <header><span>{title}</span><em>{className === "unified" ? "Baseline" : "Mixed"}</em></header>
      <div className="typography-mix-sample">
        <small>QMD MARKET DISCOVERY</small>
        <h3>Intraday market structure</h3>
        <p>Price remains above the session VWAP while relative volume expands through the current discovery window.</p>
        <strong>+$12,438.42 <span>· 09:45:12 ET</span></strong>
        <code>qmd.structure.signal.v3</code>
      </div>
      <dl>
        <div><dt>Display</dt><dd>{display}</dd></div>
        <div><dt>Interface + numbers</dt><dd>Public Sans</dd></div>
        <div><dt>Long reading</dt><dd>{reading}</dd></div>
        <div><dt>Technical</dt><dd>JetBrains Mono</dd></div>
      </dl>
      <p>{description}</p>
    </article>
  );
}
