"""Deterministic supplement universe from the frozen historical ordinal summary.

These names are coverage requests, not claims about historical index membership.
Every included symbol must exist in the source plan and have a liquid ticker-year.
The activity ranking is average canonical events per source day, not profitability.
"""

from .shards.phase1_contract import seed_for

FAMOUS = tuple("""
SPY QQQ IWM DIA VTI VOO IVV XLK XLF XLE XLV XLY XLP XLI XLU XLB XLRE XLC
SMH SOXX ARKK EEM EFA GLD SLV TLT HYG LQD
AAPL MSFT NVDA AMZN GOOGL GOOG META FB TSLA AMD INTC AVGO QCOM MU AMAT LRCX
KLAC ASML TSM ARM ADBE CRM ORCL CSCO AMGN GILD REGN VRTX BIIB ISRG INTU ADP
PAYX NOW SNPS CDNS PANW CRWD FTNT ZS NET DDOG MDB SNOW PLTR SHOP UBER ABNB DASH
NFLX DIS PYPL SQ XYZ COIN HOOD RBLX ROKU ZM DOCU DELL HPQ IBM SOFI
JPM BAC C WFC GS MS V MA AXP SCHW BLK BRK.B WMT COST TGT HD LOW KO PEP MCD
SBUX NKE LULU UNH JNJ PFE MRK LLY ABBV BMY XOM CVX COP BA CAT GE GM F FDX UPS
DE NOC LMT RTX CCL RCL DAL UAL AAL BABA BIDU JD PDD NIO LI XPEV GME AMC
""".split())


def select_universe(parent, top=512, seed=20261108):
    """Return full-history jobs, plus explicit coverage/selection evidence.

    Full history stays attached for causal quote seeds and cross-session context.
    Only samples ending in an originally liquid ticker-year may be published.
    """
    eligible = {
        j["ticker"]: j for j in parent["jobs"] if any(r["liquid"] for r in j["years"])
    }
    scores = {
        name: sum(r["events"] for r in j["years"]) / sum(r["days"] for r in j["years"])
        for name, j in eligible.items()
    }
    ranked = sorted(eligible, key=lambda name: (-scores[name], name))[:top]
    requested = set(FAMOUS)
    names = set(ranked) | (requested & eligible.keys())
    jobs = sorted(
        (eligible[name] for name in names), key=lambda j: seed_for(seed, j["ticker"])
    )
    evidence = dict(
        top_activity_count=top,
        selected=len(jobs),
        requested=list(FAMOUS),
        requested_present=sorted(requested & names),
        requested_unavailable=sorted(requested - names),
        tickers=sorted(names),
        policy="top historical activity plus pinned famous names; originally liquid end-years only",
    )
    for required in ("AAPL", "NVDA", "SPY", "QQQ"):
        if required not in names:
            raise ValueError(
                f"Required supplement symbol {required} lacks eligible canonical history"
            )
    return jobs, evidence
