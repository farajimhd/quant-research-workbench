from __future__ import annotations

import hashlib


# Versioned materialization authority sized from 2019-2026 ClickHouse event
# coverage and observed BarGPT row compression. The order groups macro and
# sector instruments, liquid equities, extreme regimes, lifecycle names, and
# persistently illiquid equities; identity comparisons use the sorted hash.
BAR_GPT_COHORT_2TB_ID = "bar_gpt_2tb_100_v1"
BAR_GPT_COHORT_2TB: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "HYG", "LQD", "GLD", "USO",
    "XLF", "XLK", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLRE", "XLC", "SMH",
    "XBI", "KRE", "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AMD",
    "INTC", "AVGO", "MU", "JPM", "BAC", "WFC", "XOM", "CVX", "JNJ", "UNH", "ABBV",
    "LLY", "WMT", "HD", "CAT", "BA", "GE", "F", "NFLX", "DIS", "T", "ORCL", "CRM",
    "PYPL", "TQQQ", "SOXL", "UVXY", "PLTR", "COIN", "RIVN", "HOOD", "ARM", "CAVA",
    "SOFI", "LCID", "UBER", "LYFT", "ABNB", "DASH", "MRNA", "GME", "AMC", "XBIO",
    "IMMP", "CPSH", "DOGZ", "AVAL", "NWFL", "TSBK", "IGC", "INMB", "WEYS", "LWAY",
    "CMT", "MOGO", "ATNM", "RGCO", "RVP", "UONE", "FONR", "LIQT", "FLNT", "PVL",
    "INOD", "MRAM", "GHG", "GENC", "ATOS",
)
BAR_GPT_COHORT_2TB_SHA256 = hashlib.sha256(
    "\n".join(sorted(BAR_GPT_COHORT_2TB)).encode("utf-8")
).hexdigest()
BAR_GPT_COHORT_2TB_TABLE = "bar_gpt_1s_bars_v1_cohort_2tb"
BAR_GPT_COHORT_2TB_MANIFEST_TABLE = "bar_gpt_1s_build_manifest_v1_cohort_2tb"
BAR_GPT_DAILY_BOOTSTRAP_TABLE = "bar_gpt_daily_context_v1_massive_unadjusted"
BAR_GPT_DAILY_BOOTSTRAP_MANIFEST_TABLE = "bar_gpt_daily_context_manifest_v1_massive_unadjusted"

# Split-adjusted v2 authorities.  The v1 tables remain immutable evidence; these
# names deliberately require an explicit cutover after certification.
BAR_GPT_ADJUSTED_DAILY_TABLE = "bar_gpt_daily_sessions_v2_massive_adjusted"
BAR_GPT_ADJUSTED_DAILY_MANIFEST_TABLE = "bar_gpt_daily_sessions_manifest_v2_massive_adjusted"
BAR_GPT_ADJUSTED_1S_TABLE = "bar_gpt_1s_bars_v2_cohort_2tb_split_adjusted"
BAR_GPT_ADJUSTED_1S_MANIFEST_TABLE = "bar_gpt_1s_adjustment_manifest_v2_cohort_2tb"
BAR_GPT_SPLIT_FACTOR_TABLE = "bar_gpt_split_factors_v2_cohort_2tb"
