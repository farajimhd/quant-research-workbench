# Canvas XBRL financial quality

## Objective

The standalone XBRL container converts standardized facts from public SEC filings into an auditable, slow-moving view of operating quality. It answers:

1. How strong is the currently reported financial evidence?
2. Which categories explain that result?
3. Which exact facts and periods entered each category?
4. Is the evidence improving or deteriorating as new filings become public?

It is not a valuation model, an earnings forecast, or a short-term trade-entry signal. Every state is point-in-time and retains the source taxonomy tag, reporting period, filing availability time, and accession.

## Closed-form score

Every component is transformed to a bounded 0–100 score:

```text
higher-is-stronger = clamp((value - lower_bound) / (upper_bound - lower_bound) * 100)
lower-is-stronger  = 100 - clamp((value - lower_bound) / (upper_bound - lower_bound) * 100)
```

The category score is the weighted mean of available component scores. Missing evidence is not assigned a zero:

```text
category = sum(component_score * component_weight) / sum(available component weights)
category_coverage = available component weight / configured component weight
```

The overall score weights each category by both its configured importance and its evidence coverage:

```text
overall = sum(category_score * category_weight * category_coverage)
          / sum(category_weight * category_coverage)
```

A category score is withheld below 40% evidence coverage. The overall score is withheld below 50% coverage.

## Category definitions

| Category | Composite weight | Components and normalization ranges |
| --- | ---: | --- |
| Profitability | 30% | Gross margin 20% (10–60%); operating margin 30% (-5–25%); net margin 30% (-5–20%); return on positive equity 20% (-10–30%) |
| Growth | 20% | Comparable revenue growth 55% (-10–25%); comparable earnings growth 45% (-25–40%) |
| Cash quality | 20% | Free-cash-flow margin 60% (-5–20%); operating-cash conversion 40% (0.5–1.5x) |
| Balance sheet | 20% | Current ratio 40% (0.5–2x); inverse debt-to-positive-equity 35% (0–2x); interest coverage 25% (1–8x) |
| Capital discipline | 10% | Inverse basic-share growth 60% (-2–8%); inverse diluted-share spread 40% (0–10%) |

The service returns each raw input, unit, bounds, direction, normalized score, component weight, weighted points, category weight, effective weight, contribution points, coverage, and formula. Strategies can consume this contract without reimplementing the formulas.

## Evidence classes and history

Canonical facts are grouped into income statement, cash flow, balance sheet, operating investment, capital and dilution, and tax and financing. Each reported-evidence card contains:

- the latest comparable value and fiscal period;
- the change from the previous comparable period;
- every comparable causal observation filed from January 1, 2019 through the selected point-in-time clock;
- a semantic change tone only when higher or lower has a defensible interpretation;
- the filing date, taxonomy namespace and tag, accession, and directional rule.

Context-dependent fields such as capital expenditure, inventory, receivables, goodwill, intangibles, R&D, and deferred revenue remain direction-neutral. Their increase is not inherently good or bad.

## Causality

The quality trajectory is rebuilt at every filing-availability timestamp from January 1, 2019 through the selected clock. A historical state uses only facts that were public at that time. Later filings can alter the newest state but never repaint earlier category or overall scores. The time-proportional axis preserves gaps between filings instead of spacing every filing equally. Users can select the overall series or any category in the same gradient area chart.

The latest decision compares the newest causal composite with the preceding scored filing:

- strengthening: at least +5 score points;
- weakening: at most -5 score points;
- stable: inside that materiality band;
- insufficient: coverage does not support a score.

## UI hierarchy

- The header leads with overall quality, latest filing decision, and evidence clock.
- Large category cards expose score, composite weight, coverage, and contribution.
- Selecting a category reveals its closed-form inputs and normalization ranges.
- Derived financial signals show aligned ratios and formulas.
- Reported evidence uses readable metric cards with semantic change, gradient history, and expandable audit details.
- Main-chart Y labels occupy a dedicated gutter outside the plot; seven date ticks span the complete 2019-to-clock domain.
- Purple identifies analytical context whose direction is not inherently favorable or unfavorable. Green is reserved for favorable evidence and red for unfavorable evidence; purple does not mean bullish or bearish.
- The in-product Guide documents the objective, every formula and category, coverage rules, history, colors, audit fields, and limitations.

The container reads `GET /api/trading/ticker-facts/{symbol}?as_of=...` and returns analysis contract `sec_xbrl_decision_evidence_v2` backed by `sec_fundamental_strength_v2`.
