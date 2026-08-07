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
BAR_GPT_SOURCE_ALIAS_TICKERS: tuple[str, ...] = ("FB",)
BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE = "bar_gpt_1s_build_manifest_v1_identity_aliases"
BAR_GPT_IDENTITY_QUARANTINE: tuple[str, ...] = ("MOGO",)
BAR_GPT_TRAINING_TICKERS: tuple[str, ...] = tuple(
    ticker for ticker in BAR_GPT_COHORT_2TB if ticker not in BAR_GPT_IDENTITY_QUARANTINE
)
# These identities remain excluded from the 2019-2020 training population even
# though the chronological 2026 validation panel now covers the full cohort.
BAR_GPT_IDENTITY_HOLDOUT_TICKERS: tuple[str, ...] = (
    "SPY", "AAPL", "NVDA", "TSLA", "XBI", "GME", "XBIO", "LIQT",
)
# Fixed out-of-time panel. The loader chooses two deterministic pseudo-random
# blocks across all seven ticker-month shards for each eligible identity.
BAR_GPT_VALIDATION_SLICES_2026: tuple[tuple[str, str, str], ...] = tuple(
    (ticker, "2026-01-01", "2026-08-01") for ticker in BAR_GPT_TRAINING_TICKERS
)
BAR_GPT_SIP_DAILY_SESSION_TABLE = "daily_session_bars_by_symbol_time_v1"
BAR_GPT_SIP_DAILY_SESSION_MANIFEST_TABLE = "daily_session_bars_manifest_v1"
