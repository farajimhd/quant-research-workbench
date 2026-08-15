import { ChevronDown, Copy, LockKeyhole, Plus, Search, Trash2 } from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";

export type TypographyComparisonFont = "noto-sans" | "open-sans" | "lato" | "public-sans";

const FONT_OPTIONS: Record<TypographyComparisonFont, { family: string; label: string; note: string }> = {
  "noto-sans": { family: '"Noto Sans", sans-serif', label: "Noto Sans", note: "Balanced multilingual text forms with steady readability across interface sizes." },
  "open-sans": { family: '"Open Sans", sans-serif', label: "Open Sans", note: "Humanist proportions and open counters designed for dense screen text." },
  lato: { family: '"Lato", sans-serif', label: "Lato", note: "Warm, distinctive text shapes with restrained medium and semibold weights." },
  "public-sans": { family: '"Public Sans", sans-serif', label: "Public Sans", note: "Neutral interface typography with generous, readable forms." },
};

const FONT_NAV_LABELS: Record<TypographyComparisonFont, string> = {
  "noto-sans": "Noto Sans",
  "open-sans": "Open Sans",
  lato: "Lato",
  "public-sans": "Public Sans",
};

const RULES = [
  ["Price or Volume Squeeze", "Passes when price expands at least 5% or aligned relative volume reaches 3×."],
  ["VWAP Breakout", "Requires last price to trade at least 5 basis points above current VWAP."],
  ["Bullish News Sentiment", "Requires a validated news label and a positive sentiment score of at least 0.35."],
  ["Bearish News Sentiment", "Requires a validated news label and a negative sentiment score of −0.35 or lower."],
  ["Fundamental Bullish", "Requires reliable SEC evidence and a trajectory score of at least 65."],
  ["Stock Split Window", "Retains symbols from 10 days before through 5 days after a published split execution date."],
] as const;

const FIELD_GROUPS = [
  { label: "Market data", items: [["Last price", "Current eligible trade price · USD"], ["Change %", "Previous-close return · percent"], ["Relative volume", "Session volume ÷ aligned baseline"]] },
  { label: "Technical analysis", items: [["VWAP", "Session volume-weighted average price"], ["RSI (14)", "Relative Strength Index · 14 bars"], ["ATR (14)", "Average True Range · 14 bars"]] },
  { label: "Company & security", items: [["Market cap", "Point-in-time equity value · USD"], ["Public float", "Tradable public shares"], ["Short % of float", "Reported short interest ÷ public float"]] },
] as const;

export function TypographyComparisonPage({ font }: { font: TypographyComparisonFont }) {
  const definition = FONT_OPTIONS[font];
  const [selectedRule, setSelectedRule] = useState(1);
  const [lookupOpen, setLookupOpen] = useState(false);
  const [fieldQuery, setFieldQuery] = useState("");
  const [selectedField, setSelectedField] = useState("Last price");
  const visibleGroups = useMemo(() => {
    const needle = fieldQuery.trim().toLowerCase();
    return FIELD_GROUPS.map((group) => ({ ...group, items: group.items.filter(([label, description]) => !needle || `${label} ${description}`.toLowerCase().includes(needle)) })).filter((group) => group.items.length);
  }, [fieldQuery]);

  return (
    <div className={`typography-comparison-page typography-${font}`} style={{ "--comparison-font": definition.family } as CSSProperties}>
      <header className="typography-comparison-header">
        <div><span>Typography comparison · isolated preview</span><h1>Rule Set Library</h1><p>The same product surface rendered in {definition.label}. {definition.note}</p></div>
        <nav aria-label="Typography comparison pages">
          {(Object.entries(FONT_OPTIONS) as [TypographyComparisonFont, typeof definition][]).map(([key]) => <a aria-current={key === font ? "page" : undefined} href={`#typography-${key}`} key={key}>{FONT_NAV_LABELS[key]}</a>)}
        </nav>
      </header>

      <div className="data-library-workbench rule-set-library typography-rule-set-preview">
        <aside className="data-library-catalog">
          <header><span>Registered rule sets</span><strong>42 of 42</strong><p>Compare navigation density, descriptions, control labels, and numeric forms without changing saved configuration.</p></header>
          <label className="data-library-search"><Search size={15} /><input aria-label="Search sample rule sets" placeholder="Search rule sets" type="search" /></label>
          <button className="data-library-create" type="button"><Plus size={14} /> Create rule set</button>
          <div className="data-library-tree">
            <details open><summary><span>Built-in defaults</span><em>41</em></summary><details className="data-library-subgroup" open><summary><span>Atomic definitions</span><em>41</em></summary><div>{RULES.map(([name, description], index) => <button aria-current={selectedRule === index ? "true" : undefined} key={name} onClick={() => setSelectedRule(index)} type="button"><span><strong>{name}</strong><small>{description}</small></span><LockKeyhole size={12} /></button>)}</div></details></details>
            <details open><summary><span>Custom drafts</span><em>1</em></summary><details className="data-library-subgroup" open><summary><span>User definitions</span><em>1</em></summary><div><button aria-current={selectedRule === RULES.length ? "true" : undefined} onClick={() => setSelectedRule(RULES.length)} type="button"><span><strong>Intraday Momentum Quality</strong><small>Editable composite of price, volume, and liquidity evidence.</small></span></button></div></details></details>
          </div>
        </aside>

        <main className="data-library-detail">
          <article className="rule-set-document">
            <header><span>Editable rule set · revision 1</span><input aria-label="Sample rule set name" value={selectedRule < RULES.length ? RULES[selectedRule][0] : "Intraday Momentum Quality"} readOnly /><textarea aria-label="Sample rule set description" value={selectedRule < RULES.length ? RULES[selectedRule][1] : "Qualifies liquid momentum candidates using aligned price, volume, and spread evidence."} readOnly /><div><code>intraday-momentum-quality</code><button type="button"><Copy size={13} /> Duplicate as custom</button></div></header>
            <section className="rule-set-logic"><label><span>Condition logic</span><select defaultValue="all"><option value="all">All conditions</option><option value="any">Any condition</option><option value="score">Required score</option></select></label><span>3 conditions</span></section>
            <section className="rule-condition-list">
              <div className="rule-condition-row rule-condition-editable typography-condition-with-lookup">
                <span>1</span>
                <div className="rule-condition-definition"><small>Data definition</small><button aria-expanded={lookupOpen} className="typography-lookup-trigger" onClick={() => setLookupOpen((value) => !value)} type="button"><span>{selectedField}</span><ChevronDown size={15} /></button><em>market.last_price · session</em>
                  {lookupOpen ? <div className="typography-lookup-menu" role="dialog" aria-label="Sample data definition lookup"><label><Search size={14} /><input autoFocus onChange={(event) => setFieldQuery(event.target.value)} placeholder="Search data definitions…" value={fieldQuery} /></label><div>{visibleGroups.map((group) => <section key={group.label}><header><span>{group.label}</span><em>{group.items.length}</em></header>{group.items.map(([label, description]) => <button aria-selected={selectedField === label} key={label} onClick={() => { setSelectedField(label); setLookupOpen(false); }} role="option" type="button"><strong>{label}</strong><small>{description}</small></button>)}</section>)}</div></div> : null}
                </div>
                <label><small>Comparison</small><select defaultValue="greater_or_equal"><option value="greater_or_equal">is at least</option><option>is greater than</option><option>is less than</option></select></label>
                <label><small>Threshold</small><input className="typography-number" defaultValue="12.50" type="number" /></label>
                <button aria-label="Remove first condition" type="button"><Trash2 size={13} /></button>
              </div>
              <ComparisonCondition index={2} label="Relative volume" meta="market.relative_volume · session" relation="is at least" value="3.00×" />
              <ComparisonCondition index={3} label="Quoted spread" meta="market.spread_bps · 10 seconds" relation="is at most" value="35 bps" />
            </section>
            <button className="data-library-add-condition" type="button"><Plus size={14} /> Add condition</button>
            <section className="typography-number-specimen" aria-label="Financial number typography">
              <header><span>Financial number sample</span><small>Tabular figures enabled</small></header>
              <div><Metric label="Last price" value="$123.45" /><Metric label="Change" tone="positive" value="+12.50%" /><Metric label="Volume" value="2,407,816" /><Metric label="Market cap" value="$18.72B" /><Metric label="Spread" value="7.5 bps" /><Metric label="Timestamp" value="09:45:12 ET" /></div>
            </section>
          </article>
        </main>
      </div>
    </div>
  );
}

function ComparisonCondition({ index, label, meta, relation, value }: { index: number; label: string; meta: string; relation: string; value: string }) {
  return <div className="rule-condition-row rule-condition-readonly"><span>{index}</span><div className="rule-condition-operand"><strong>{label}</strong><small>{meta}</small></div><em>{relation}</em><div className="rule-condition-operand rule-condition-target"><strong className="typography-number">{value}</strong><small>Fixed value</small></div></div>;
}

function Metric({ label, tone, value }: { label: string; tone?: "positive"; value: string }) {
  return <div><span>{label}</span><strong className="typography-number" data-tone={tone}>{value}</strong></div>;
}
