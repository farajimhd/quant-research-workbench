# Canvas XBRL financial evidence

## Objective

The standalone XBRL container is a filing-evidence analysis surface, not a second raw-facts table and not a short-term price forecast. It answers three questions at the active point-in-time clock:

1. What did the issuer most recently report?
2. Which financial dimensions are strong, mixed, or weak?
3. Did the evidence improve or deteriorate when the latest filing became public?

Every result retains its taxonomy tag, period, filing availability time, and accession so a trader can audit the derived conclusion.

## Evidence classes

Canonical facts are grouped into decision-oriented classes:

| Class | Representative evidence | Decision use |
| --- | --- | --- |
| Income statement | Revenue, gross profit, operating income, net income, diluted EPS | Profitability and growth |
| Cash flow | Operating cash flow, capital expenditure, free cash flow | Cash conversion and self-funding capacity |
| Balance sheet | Cash, current assets and liabilities, debt, equity | Liquidity and balance-sheet resilience |
| Operating investment | R&D and SG&A | Investment intensity and operating discipline |
| Capital and dilution | Shares outstanding, basic and diluted weighted shares, stock compensation, issuance | Share-base pressure and capital discipline |
| Tax and financing | Interest expense, taxes, debt issuance and repayment | Financing burden and tax context |

## Derived analysis

The backend reuses the Stock Facts fundamental authority for aligned ratios and changes, then projects a deeper XBRL-specific analysis:

- **Filing evidence score**: evidence-weighted composite on a 0–100 scale.
- **Profitability**: margins and earnings evidence.
- **Growth**: comparable revenue, earnings, and share-count changes.
- **Cash quality**: operating cash generation, free cash flow, and cash conversion.
- **Balance sheet**: working capital, current-ratio, leverage, cash, and debt evidence.
- **Capital discipline**: dilution, issuance, weighted-share changes, and stock compensation.

Coverage is explicit. Missing facts reduce evidence coverage; they are never converted to zero. The actionable metric cards show aligned ratios such as margins, return on assets/equity, working capital, and free cash flow rather than asking the user to compare raw dimensional values manually.

## Causality and change through time

The trajectory is rebuilt at each filing-availability timestamp. A historical state may use only facts public at that timestamp. A later filing can change the newest score, but it cannot repaint an earlier score. The latest decision label compares the newest causal state with the preceding scored filing:

- strengthening: score increased materially;
- weakening: score decreased materially;
- stable: change stayed inside the materiality band;
- insufficient: evidence coverage is too low for a responsible comparison.

This design makes the result suitable for replay and strategy feature extraction. Strategies should persist the score, facet values, coverage, filing availability time, and analysis version used at the decision clock.

## UI contract

- The headline score and filing-to-filing decision are the most salient elements.
- A causal area chart shows the score only after each filing became public.
- Facet cards expose score, label, and evidence coverage.
- Derived metric cards explain calculation and period.
- Class tabs keep the full reported evidence inspectable without flattening unrelated facts into one table.
- Raw taxonomy tags and accession identifiers remain available for reconciliation.
- The Guide explains interpretation, provenance, causality, and limitations in the same container.

The container uses `GET /api/trading/ticker-facts/{symbol}?as_of=...`; it does not depend on the broad Canvas preview request.
