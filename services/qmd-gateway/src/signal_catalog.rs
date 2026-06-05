use serde::Serialize;

/// Signal methods are scanner/trading decision templates.
///
/// They are intentionally separate from indicators:
/// - indicators describe reusable computed state
/// - signal methods describe how that state is combined into a tradable event
///
/// Every method declares its working timeframe and confirmation timeframe so a
/// live detector, replay runner, and backtest simulator can use the same
/// contract without guessing which bars or tick windows are required.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalCategory {
    CrossTimeframe,
    Exhaustion,
    GapContinuation,
    HighOfDay,
    LiquidityRecovery,
    MeanReversion,
    NewsMomentum,
    OpeningRange,
    PullbackReversal,
    ShortSqueeze,
    TapeAcceleration,
    TrendContinuation,
    VolumeShock,
    Vwap,
}

/// P0 methods are the default live scanner candidates.
/// P1 methods are useful confirmations or secondary setup types.
/// P2 methods are strategy/research candidates that should be enabled
/// deliberately after validation.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalPriority {
    P0,
    P1,
    P2,
}

/// How the detector should run in the gateway.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalComputeMode {
    /// Evaluated from short rolling tick windows as quotes/trades arrive.
    RealtimeTick,
    /// Evaluated when a bar closes.
    RealtimeBarClose,
    /// Uses both tick windows and closed bars. This is the default for fast movers.
    HybridTickAndBar,
    /// Requires alignment across multiple bar timeframes.
    CrossTimeframe,
}

/// Signal persistence is decision-snapshot oriented.
///
/// We persist raw quotes/trades and bars continuously. Signal methods should
/// write the exact evidence used when a candidate is emitted, rejected, routed,
/// or later replayed. This avoids creating a wide publication table for every
/// possible intermediate field.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalPersistencePolicy {
    DecisionSnapshotOnly,
    DisabledByDefault,
    ResearchOnly,
}

/// `Cataloged` means the method contract is defined and can be implemented by a
/// detector. `Implemented` should be used only after a live detector writes
/// signal decisions using this contract.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalStatus {
    Cataloged,
    Implemented,
    Planned,
}

#[derive(Clone, Copy, Debug, Serialize)]
pub struct SignalMethodEntry {
    pub key: &'static str,
    pub label: &'static str,
    pub category: SignalCategory,
    pub priority: SignalPriority,
    pub compute_mode: SignalComputeMode,
    pub persistence_policy: SignalPersistencePolicy,
    pub status: SignalStatus,
    pub working_timeframes: &'static [&'static str],
    pub confirmation_timeframes: &'static [&'static str],
    pub required_bar_fields: &'static [&'static str],
    pub required_indicator_fields: &'static [&'static str],
    pub required_reference_fields: &'static [&'static str],
    pub trigger_rules: &'static [&'static str],
    pub confirmation_rules: &'static [&'static str],
    pub reject_rules: &'static [&'static str],
    pub emits: &'static [&'static str],
    pub snapshot_fields: &'static [&'static str],
    pub rationale: &'static str,
}

pub fn signal_catalog() -> &'static [SignalMethodEntry] {
    SIGNAL_CATALOG
}

const TICK_TFS: &[&str] = &["1s", "10s", "30s"];
const FAST_BAR_TFS: &[&str] = &["10s", "30s", "1m"];
const INTRADAY_TFS: &[&str] = &["1m", "5m"];
const HIGHER_TFS: &[&str] = &["5m", "1h"];
const EMPTY_TFS: &[&str] = &[];

const COMMON_EMITS: &[&str] = &[
    "signal_id",
    "signal_key",
    "ticker",
    "working_timeframe",
    "confirmation_timeframe",
    "side",
    "signal_strength",
    "scanner_score",
    "entry_bias",
    "reject_reason",
];

const COMMON_SNAPSHOT: &[&str] = &[
    "last_price",
    "spread_bps",
    "trade_rate_10s",
    "trade_rate_60s",
    "tape_imbalance_60s",
    "volume",
    "dollar_volume",
    "vwap",
    "price_vs_vwap_pct",
    "rvol_1m",
    "float_bucket",
    "short_pressure_label",
];

const SIGNAL_CATALOG: &[SignalMethodEntry] = &[
    SignalMethodEntry {
        key: "tape_acceleration_breakout",
        label: "Tape Acceleration Breakout",
        category: SignalCategory::TapeAcceleration,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: TICK_TFS,
        confirmation_timeframes: &["1m"],
        required_bar_fields: &["close", "high", "volume", "vwap", "price_change_pct"],
        required_indicator_fields: &[
            "trade_rate_10s",
            "trade_rate_60s",
            "quote_rate_10s",
            "quote_rate_60s",
            "trade_accel_10s_60s",
            "tape_imbalance_60s",
            "spread_bps",
        ],
        required_reference_fields: &["float_bucket", "short_pressure_label"],
        trigger_rules: &[
            "trade_rate_10s materially exceeds trade_rate_60s",
            "price breaks recent high or range boundary on positive tape imbalance",
            "spread_bps remains inside the configured tradability limit",
        ],
        confirmation_rules: &["1m close holds above breakout level", "volume or dollar_volume expands versus recent bars"],
        reject_rules: &["spread shock widens after trigger", "price immediately loses breakout level", "halt_or_ssr_risk blocks the route"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Primary early-move detector for live tape because acceleration often appears before clean multi-minute bars.",
    },
    SignalMethodEntry {
        key: "volume_shock_momentum",
        label: "Volume Shock Momentum",
        category: SignalCategory::VolumeShock,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: FAST_BAR_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "volume", "dollar_volume", "vwap", "price_change_pct"],
        required_indicator_fields: &["rvol_1m", "volume_zscore", "dollar_volume_zscore", "trade_rate_zscore", "price_vs_vwap_pct"],
        required_reference_fields: &["market_cap_bucket", "float_bucket"],
        trigger_rules: &[
            "volume_zscore or dollar_volume_zscore exceeds configured shock threshold",
            "price_change_pct is positive and close is above vwap",
            "trade activity confirms the bar-level volume shock",
        ],
        confirmation_rules: &["5m trend is not down", "spread_bps and liquidity_score stay tradable"],
        reject_rules: &["volume shock occurs into day high rejection", "close returns below vwap", "quoted liquidity disappears"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Catches liquid symbols that suddenly become active enough for scanner attention.",
    },
    SignalMethodEntry {
        key: "opening_range_breakout",
        label: "Opening Range Breakout",
        category: SignalCategory::OpeningRange,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: INTRADAY_TFS,
        confirmation_timeframes: HIGHER_TFS,
        required_bar_fields: &["open", "high", "low", "close", "volume", "vwap"],
        required_indicator_fields: &["opening_range_high", "opening_range_low", "opening_range_breakout", "rvol_1m", "trend_score"],
        required_reference_fields: &["float_bucket", "news_flag"],
        trigger_rules: &[
            "close breaks opening_range_high for long or opening_range_low for short",
            "breakout bar expands range and volume versus the opening baseline",
            "close location is near the favorable side of the bar",
        ],
        confirmation_rules: &["5m trend_score agrees with breakout direction", "price is not extended beyond configured ATR multiple"],
        reject_rules: &["breakout has low relative volume", "spread_bps is above route limit", "price falls back inside opening range"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Keeps the older ORB workflow but moves it onto live quote/trade-derived bars.",
    },
    SignalMethodEntry {
        key: "vwap_reclaim_momentum",
        label: "VWAP Reclaim Momentum",
        category: SignalCategory::Vwap,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: FAST_BAR_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "low", "volume", "vwap"],
        required_indicator_fields: &["price_vs_vwap_pct", "vwap_reclaim", "tape_imbalance_60s", "ema_9", "ema_20"],
        required_reference_fields: &["float_bucket"],
        trigger_rules: &[
            "price crosses from below vwap to above vwap",
            "positive tape imbalance appears during the reclaim",
            "close holds above vwap by the end of the working timeframe",
        ],
        confirmation_rules: &["ema_9 is above or curling toward ema_20", "5m price action is not making lower lows"],
        reject_rules: &["reclaim happens on declining volume", "spread widens through the reclaim", "price loses vwap within the next bar"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Useful for intraday reversals where tape confirms institutional-style reclaim behavior.",
    },
    SignalMethodEntry {
        key: "liquidity_pullback_reversal",
        label: "Liquidity Pullback Reversal",
        category: SignalCategory::PullbackReversal,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: &["30s", "1m"],
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "low", "high", "volume", "vwap"],
        required_indicator_fields: &["ema_9", "ema_20", "atr_14", "tape_imbalance_60s", "spread_bps", "liquidity_score"],
        required_reference_fields: &["float_bucket", "market_cap_bucket"],
        trigger_rules: &[
            "pullback holds above vwap, ema_20, or prior breakout level",
            "selling pressure fades and tape_imbalance_60s turns favorable",
            "liquidity_score recovers after the pullback",
        ],
        confirmation_rules: &["5m trend remains aligned", "new working-timeframe high confirms reversal"],
        reject_rules: &["pullback breaks structural support", "liquidity stays poor", "ATR distance makes stop too wide"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Converts fast movers into tradable second entries instead of chasing the first spike.",
    },
    SignalMethodEntry {
        key: "gap_and_go_continuation",
        label: "Gap And Go Continuation",
        category: SignalCategory::GapContinuation,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: INTRADAY_TFS,
        confirmation_timeframes: HIGHER_TFS,
        required_bar_fields: &["close", "high", "volume", "vwap", "price_change_pct"],
        required_indicator_fields: &["gap_from_previous_close_pct", "rvol_1m", "trend_score", "opening_range_breakout"],
        required_reference_fields: &["news_flag", "float_bucket", "short_pressure_label"],
        trigger_rules: &[
            "gap_from_previous_close_pct exceeds configured minimum",
            "price holds above day_open or vwap after the opening test",
            "volume remains elevated versus session baseline",
        ],
        confirmation_rules: &["5m bar confirms higher high", "1h context does not show immediate resistance"],
        reject_rules: &["gap fades below day_open", "volume dries up after trigger", "spread is too wide for configured order type"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Core day-trading setup for news or catalyst names using live bars instead of prebuilt one-minute bars.",
    },
    SignalMethodEntry {
        key: "short_squeeze_pressure",
        label: "Short Squeeze Pressure",
        category: SignalCategory::ShortSqueeze,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: FAST_BAR_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "volume", "dollar_volume", "vwap"],
        required_indicator_fields: &["trade_accel_10s_60s", "tape_imbalance_60s", "volume_zscore", "day_high_break", "spread_bps"],
        required_reference_fields: &["float_bucket", "short_pressure_label", "short_squeeze_likelihood", "short_interest_date"],
        trigger_rules: &[
            "short_pressure_label indicates crowded short or squeeze-prone context",
            "price breaks intraday resistance with high trade acceleration",
            "tape pressure remains favorable after the break",
        ],
        confirmation_rules: &["5m close confirms above resistance", "large_trade_activity supports continuation"],
        reject_rules: &["reference short data is stale beyond configured limit", "price rejects at resistance", "liquidity is too thin to route"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Combines reference short context with live tape acceleration instead of treating short interest alone as a signal.",
    },
    SignalMethodEntry {
        key: "high_of_day_break",
        label: "High Of Day Break",
        category: SignalCategory::HighOfDay,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: FAST_BAR_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "volume", "vwap"],
        required_indicator_fields: &["day_high_break", "rvol_1m", "tape_imbalance_60s", "spread_bps", "trend_score"],
        required_reference_fields: &["float_bucket"],
        trigger_rules: &[
            "price breaks current session high",
            "break has favorable tape imbalance and elevated relative volume",
            "break occurs above vwap",
        ],
        confirmation_rules: &["5m trend_score is positive", "working timeframe closes near high"],
        reject_rules: &["breakout is late and overextended by ATR", "immediate failed breakout wick appears", "spread exceeds max route threshold"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "A clean but common continuation method; lower priority than tape acceleration because it often triggers later.",
    },
    SignalMethodEntry {
        key: "trend_continuation",
        label: "Trend Continuation",
        category: SignalCategory::TrendContinuation,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::CrossTimeframe,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: INTRADAY_TFS,
        confirmation_timeframes: &["1h"],
        required_bar_fields: &["close", "high", "low", "volume", "vwap"],
        required_indicator_fields: &["ema_9", "ema_20", "ema_50", "trend_score", "adx", "price_vs_vwap_pct"],
        required_reference_fields: &["market_cap_bucket"],
        trigger_rules: &[
            "ema stack and trend_score show aligned direction",
            "pullback resolves in the trend direction",
            "price remains above vwap for long or below vwap for short",
        ],
        confirmation_rules: &["1h trend does not conflict", "volume confirms continuation rather than exhaustion"],
        reject_rules: &["trend is extended beyond configured ATR multiple", "ADX/trend strength falls below threshold", "liquidity score deteriorates"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Covers slower continuation trades after the initial scanner event has matured.",
    },
    SignalMethodEntry {
        key: "cross_timeframe_trend_alignment",
        label: "Cross-Timeframe Trend Alignment",
        category: SignalCategory::CrossTimeframe,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::CrossTimeframe,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: &["1m", "5m", "1h"],
        confirmation_timeframes: EMPTY_TFS,
        required_bar_fields: &["close", "vwap", "volume"],
        required_indicator_fields: &["trend_alignment_1m_5m", "trend_alignment_5m_1h", "ema_stack_alignment", "trend_score"],
        required_reference_fields: &[],
        trigger_rules: &[
            "1m, 5m, and 1h directional states align",
            "price is on the favorable side of VWAP",
            "lower timeframe acceleration agrees with higher timeframe trend",
        ],
        confirmation_rules: &["alignment persists for configured minimum bars"],
        reject_rules: &["higher timeframe is flat or opposite", "lower timeframe acceleration reverses", "spread/liquidity fails route filter"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Reusable confirmation signal that can rank scanner candidates or gate other methods.",
    },
    SignalMethodEntry {
        key: "failed_breakout_exhaustion",
        label: "Failed Breakout Exhaustion",
        category: SignalCategory::Exhaustion,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: &["30s", "1m"],
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "low", "volume", "vwap"],
        required_indicator_fields: &["day_high_break", "upper_wick_pct", "tape_imbalance_60s", "volume_zscore", "atr_14"],
        required_reference_fields: &["float_bucket"],
        trigger_rules: &[
            "price breaks resistance and quickly closes back below it",
            "upper wick or adverse close location shows rejection",
            "tape imbalance flips against the breakout direction",
        ],
        confirmation_rules: &["5m bar confirms failed hold", "volume spike indicates exhaustion rather than quiet drift"],
        reject_rules: &["higher timeframe trend is still strongly favorable", "liquidity is too thin for reversal route", "reclaim occurs before confirmation"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Important for avoiding bad long entries and, when enabled, for reversal strategies.",
    },
    SignalMethodEntry {
        key: "mean_reversion_to_vwap",
        label: "Mean Reversion To VWAP",
        category: SignalCategory::MeanReversion,
        priority: SignalPriority::P2,
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DisabledByDefault,
        status: SignalStatus::Cataloged,
        working_timeframes: INTRADAY_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "low", "volume", "vwap"],
        required_indicator_fields: &["price_vs_vwap_pct", "atr_14", "rsi_14", "bollinger_upper_20", "bollinger_lower_20"],
        required_reference_fields: &["market_cap_bucket"],
        trigger_rules: &[
            "price is extended from vwap by configured ATR or percent threshold",
            "momentum oscillator reaches exhaustion zone",
            "price action shows rejection away from the extension side",
        ],
        confirmation_rules: &["first close back toward vwap occurs", "spread/liquidity supports controlled entry"],
        reject_rules: &["news or squeeze context favors continuation", "trend_score is too strong", "stop distance is not acceptable"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Useful but dangerous in momentum names, so it should stay opt-in until validated.",
    },
    SignalMethodEntry {
        key: "liquidity_recovery_after_spread_shock",
        label: "Liquidity Recovery After Spread Shock",
        category: SignalCategory::LiquidityRecovery,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::RealtimeTick,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: TICK_TFS,
        confirmation_timeframes: &["1m"],
        required_bar_fields: &["close", "volume", "vwap"],
        required_indicator_fields: &["spread_zscore", "spread_bps", "liquidity_score", "quote_rate_10s", "quote_pressure"],
        required_reference_fields: &[],
        trigger_rules: &[
            "spread shock normalizes back under the tradability threshold",
            "quote rate recovers without locked/crossed quotes dominating",
            "price remains directionally stable during recovery",
        ],
        confirmation_rules: &["1m close confirms recovery direction", "liquidity_score remains stable for configured dwell time"],
        reject_rules: &["spread widens again", "quote feed appears unstable", "price jumps beyond routeable range"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Prevents entering during bad NBBO conditions while allowing the scanner to re-enable a symbol quickly.",
    },
    SignalMethodEntry {
        key: "premarket_leader_continuation",
        label: "Premarket Leader Continuation",
        category: SignalCategory::GapContinuation,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: INTRADAY_TFS,
        confirmation_timeframes: HIGHER_TFS,
        required_bar_fields: &["close", "high", "low", "volume", "vwap", "dollar_volume"],
        required_indicator_fields: &["session_phase", "day_high", "gap_from_previous_close_pct", "rvol_1m", "trend_score"],
        required_reference_fields: &["news_flag", "float_bucket", "short_pressure_label"],
        trigger_rules: &[
            "symbol is a premarket relative-volume leader",
            "price holds premarket structure after regular session opens",
            "trade acceleration returns during the continuation attempt",
        ],
        confirmation_rules: &["5m bar confirms above premarket support", "higher timeframe context does not show immediate resistance"],
        reject_rules: &["regular-session open fades below premarket support", "volume dries up", "spread remains too wide after open"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Separates premarket activity from regular-session tradability and continuation quality.",
    },
    SignalMethodEntry {
        key: "news_volume_breakout",
        label: "News Volume Breakout",
        category: SignalCategory::NewsMomentum,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::HybridTickAndBar,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: FAST_BAR_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "volume", "dollar_volume", "vwap"],
        required_indicator_fields: &["volume_zscore", "trade_accel_10s_60s", "price_volume_shock", "trend_score"],
        required_reference_fields: &["news_flag", "news_recency", "float_bucket"],
        trigger_rules: &[
            "recent news flag is present",
            "volume and trade acceleration confirm immediate market attention",
            "price breaks the post-news consolidation range",
        ],
        confirmation_rules: &["5m trend confirms continuation", "spread and liquidity are routeable"],
        reject_rules: &["news is stale for configured strategy window", "breakout fails range hold", "liquidity is too thin for selected order type"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Allows news context to boost scanner priority only when live tape and price confirm.",
    },
    SignalMethodEntry {
        key: "range_compression_expansion",
        label: "Range Compression Expansion",
        category: SignalCategory::VolumeShock,
        priority: SignalPriority::P2,
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::ResearchOnly,
        status: SignalStatus::Cataloged,
        working_timeframes: INTRADAY_TFS,
        confirmation_timeframes: &["5m", "1h"],
        required_bar_fields: &["close", "high", "low", "volume", "vwap"],
        required_indicator_fields: &["range_compression", "range_expansion", "bollinger_std_20", "volume_zscore", "trend_score"],
        required_reference_fields: &[],
        trigger_rules: &[
            "several bars compress range or volatility",
            "expansion bar breaks compression boundary",
            "volume confirms expansion",
        ],
        confirmation_rules: &["higher timeframe direction is neutral or aligned", "price remains outside the compression range"],
        reject_rules: &["expansion occurs on weak volume", "false break returns inside range", "spread/liquidity fails route filter"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Useful for research and later strategy expansion, but not a default P0 scanner event.",
    },
];
