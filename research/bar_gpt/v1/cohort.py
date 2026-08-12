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
BAR_GPT_COHORT_2TB_TABLE = "bar_gpt_1s_bars_v3_cohort_2tb"
BAR_GPT_COHORT_2TB_MANIFEST_TABLE = "bar_gpt_1s_build_manifest_v3_cohort_2tb"
BAR_GPT_EVENTS_TABLE = "bar_gpt_events_v2"
BAR_GPT_EVENTS_MANIFEST_TABLE = "bar_gpt_events_build_manifest_v2"
BAR_GPT_EVENTS_CONTINUITY_TABLE = "bar_gpt_events_ordinal_continuity_v2"
BAR_GPT_EVENTS_TRAIN_INDEX_TABLE = "bar_gpt_events_train_index_v2"
BAR_GPT_EVENTS_VALIDATION_INDEX_TABLE = "bar_gpt_events_validation_index_v2"
BAR_GPT_SOURCE_ALIAS_TICKERS: tuple[str, ...] = ("FB",)
BAR_GPT_SOURCE_ALIAS_MANIFEST_TABLE = "bar_gpt_1s_build_manifest_v3_identity_aliases"
BAR_GPT_IDENTITY_QUARANTINE: tuple[str, ...] = ("MOGO",)
BAR_GPT_MATERIALIZED_TICKERS_2TB: tuple[str, ...] = tuple(
    ticker for ticker in BAR_GPT_COHORT_2TB if ticker not in BAR_GPT_IDENTITY_QUARANTINE
)

# Immutable direct-event cohort calibrated on 2026-08-11 against the certified
# v11 shard catalog and the 2019-01-01..2026-08-01 event-day index.  It keeps
# all 99 usable members of the original diverse cohort, then adds the 201
# highest predicted-output current mapped identities with at least 1,000
# covered event days.  The resulting v12 output estimate is approximately
# 5.2 decimal TB.  Selection uses only compact event indexes and point-in-time
# identity intervals; raw events are not scanned during cohort construction.
BAR_GPT_COHORT_5TB_300_ID = "bar_gpt_direct_events_5tb_300_v1"
BAR_GPT_COHORT_5TB_300_ADDITIONS: tuple[str, ...] = (
    "GOOG", "C", "BABA", "OXY", "TSM", "FCX", "SLB", "NIO", "CSCO", "DVN",
    "PFE", "KO", "CNQ", "DAL", "GM", "QCOM", "USB", "MS", "CMCSA", "SHOP",
    "SU", "AAL", "JD", "HAL", "AMAT", "UAL", "BP", "VZ", "NEM", "COP",
    "SNAP", "STM", "SBUX", "TD", "BMY", "RIOT", "PLUG", "NKE", "MRK", "V",
    "XYZ", "SCHW", "CFG", "RIO", "FITB", "ON", "PG", "MARA", "TXN", "NEE",
    "PINS", "PEP", "MDLZ", "LUV", "CVS", "CLF", "MCHP", "MO", "BNS", "PDD",
    "TFC", "ENB", "MGM", "HPQ", "BHP", "PAAS", "RF", "M", "SO", "MT",
    "BKR", "GILD", "MRVL", "CSX", "WMB", "TECK", "BNY", "BSX", "EXC", "SYF",
    "KEY", "RUN", "MET", "NVO", "EQT", "EBAY", "HPE", "CVE", "KHC", "VALE",
    "GAP", "MPC", "AEM", "DOW", "ABT", "RY", "ROKU", "GLW", "KMI", "HSBC",
    "IBM", "CCJ", "O", "TJX", "XEL", "PBR", "WPM", "AG", "MSTR", "WDC",
    "CL", "LVS", "EOG", "CLSK", "MOS", "KR", "AA", "BMO", "AR", "D",
    "TTD", "HBAN", "GFI", "ADBE", "CM", "ADI", "AXP", "TRP", "TGT", "BTI",
    "MFC", "KGC", "GIS", "KIM", "AIG", "FE", "SAP", "EQNR", "PPL", "ALLY",
    "NVS", "FAST", "FHN", "PBA", "ZM", "BILI", "KDP", "MCD", "TS", "RRC",
    "U", "KSS", "DUK", "APA", "FTNT", "HST", "VICI", "PTON", "VLO", "PM",
    "COF", "UAA", "BIDU", "UPS", "ENPH", "CAG", "NI", "MMM", "CDE", "BCE",
    "OKE", "PCG", "MA", "AEP", "CRWD", "CVNA", "RBLX", "TMUS", "WY", "JBLU",
    "CNP", "TDOC", "GS", "LOW", "AFL", "PSX", "CTSH", "AGI", "VFC", "INVH",
    "EMR", "PENN", "NTR", "COST", "TPR", "NOW", "RTX", "DELL", "BEN", "IP",
    "BUD",
)
BAR_GPT_COHORT_5TB_300: tuple[str, ...] = (
    *BAR_GPT_MATERIALIZED_TICKERS_2TB,
    *BAR_GPT_COHORT_5TB_300_ADDITIONS,
)
BAR_GPT_COHORT_5TB_300_SHA256 = hashlib.sha256(
    "\n".join(sorted(BAR_GPT_COHORT_5TB_300)).encode("utf-8")
).hexdigest()
if len(BAR_GPT_COHORT_5TB_300) != 300 or len(set(BAR_GPT_COHORT_5TB_300)) != 300:
    raise RuntimeError("BarGPT 5 TB direct-event cohort must contain exactly 300 unique tickers")
if BAR_GPT_COHORT_5TB_300_SHA256 != "069d7b781ffe6d7dfa4d4168f7fde7791cf79d9a115418cb77820e2eae07651d":
    raise RuntimeError("BarGPT 5 TB direct-event cohort fingerprint changed")

BAR_GPT_TRAINING_TICKERS: tuple[str, ...] = BAR_GPT_COHORT_5TB_300
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
BAR_GPT_SIP_DAILY_SESSION_TABLE = "bar_gpt_daily_session_bars_v3"
BAR_GPT_SIP_DAILY_SESSION_MANIFEST_TABLE = "bar_gpt_daily_session_bars_manifest_v3"
