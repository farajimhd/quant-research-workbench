use serde::Serialize;

/// Signal methods are reusable market-signal templates.
///
/// They are intentionally separate from indicators:
/// - indicators describe reusable computed state
/// - signal methods describe how that state is combined into a causal market event
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

/// Signal persistence is lifecycle-event oriented.
///
/// We persist raw quotes/trades and bars continuously. Signal methods should
/// write the exact evidence used when a lifecycle is triggered, updated, or
/// resolved. A strategy may consume that event, but QMD does not own strategy
/// decisions or order intent.
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

#[derive(Serialize)]
pub struct SignalTaxonomyEntry<'a> {
    #[serde(flatten)]
    pub definition: &'a SignalMethodEntry,
    pub domain: &'static str,
    pub producer: &'static str,
    pub score_required: bool,
    pub input_basis: &'static str,
    pub calculation_windows: &'a [&'a str],
    pub evaluation_mode: &'static str,
    pub update_trigger: &'static str,
    pub publication_cadence: &'static str,
}

pub fn signal_taxonomy_catalog() -> Vec<SignalTaxonomyEntry<'static>> {
    signal_catalog()
        .iter()
        .map(|definition| {
            let (input_basis, evaluation_mode, update_trigger, publication_cadence) =
                match definition.compute_mode {
                    SignalComputeMode::RealtimeTick => {
                        ("event_native", "developing", "market_event", "on_change")
                    }
                    SignalComputeMode::RealtimeBarClose | SignalComputeMode::CrossTimeframe => {
                        ("bar_derived", "closed_only", "bar_close", "bar_close")
                    }
                    SignalComputeMode::HybridTickAndBar => {
                        ("event_native", "closed_only", "bar_close", "bar_close")
                    }
                };
            SignalTaxonomyEntry {
                definition,
                domain: "market",
                producer: "qmd",
                score_required: true,
                input_basis,
                calculation_windows: definition.working_timeframes,
                evaluation_mode,
                update_trigger,
                publication_cadence,
            }
        })
        .collect()
}

#[cfg(test)]
mod taxonomy_tests {
    use super::*;

    #[test]
    fn qmd_produces_rankable_market_signals() {
        let catalog = signal_taxonomy_catalog();
        assert!(!catalog.is_empty());
        assert!(catalog.iter().all(|entry| {
            entry.domain == "market" && entry.producer == "qmd" && entry.score_required
        }));
    }
}

const TICK_TFS: &[&str] = &["1s", "10s", "30s"];
const FAST_BAR_TFS: &[&str] = &["10s", "30s", "1m"];
const INTRADAY_TFS: &[&str] = &["1m", "5m"];
const HIGHER_TFS: &[&str] = &["5m", "1h"];
const EMPTY_TFS: &[&str] = &[];

const COMMON_EMITS: &[&str] = &[
    "schema_version",
    "engine_version",
    "event_id",
    "signal_id",
    "signal_key",
    "producer",
    "ticker",
    "working_timeframe",
    "confirmation_timeframe",
    "observed_at",
    "effective_at",
    "state",
    "direction",
    "score",
    "confidence",
    "trigger_reason",
    "resolution_reason",
    "reference_price",
    "invalidation_price",
    "expires_at",
    "evidence",
];

const COMMON_SNAPSHOT: &[&str] = &[
    "last_price",
    "spread_bps",
    "trade_rate_10s",
    "trade_rate_60s",
    "trade_accel_10s_60s",
    "quote_rate_10s",
    "quote_rate_60s",
    "quote_accel_10s_60s",
    "tape_imbalance_60s",
    "liquidity_score",
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
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: TICK_TFS,
        confirmation_timeframes: &["1m"],
        required_bar_fields: &["close", "high", "low", "trade_count_accel", "tape_imbalance", "spread_bps_close"],
        required_indicator_fields: &[],
        required_reference_fields: &[],
        trigger_rules: &[
            "absolute trade_count_accel exceeds 10",
            "absolute tape_imbalance exceeds 0.15 and determines direction",
            "spread_bps_close remains below 80",
        ],
        confirmation_rules: &["the finalized working-timeframe bar carries all required evidence"],
        reject_rules: &["any trigger condition no longer holds"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Primary early-move detector for live tape because acceleration often appears before clean multi-minute bars.",
    },
    SignalMethodEntry {
        key: "volume_shock_momentum",
        label: "Volume Shock Momentum",
        category: SignalCategory::VolumeShock,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: FAST_BAR_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "low", "dollar_volume_accel", "price_change_pct", "trade_rate"],
        required_indicator_fields: &[],
        required_reference_fields: &[],
        trigger_rules: &[
            "absolute dollar_volume_accel exceeds 250000",
            "absolute price_change_pct exceeds 0.25",
            "price_change_pct determines direction",
        ],
        confirmation_rules: &["the finalized working-timeframe bar carries all required evidence"],
        reject_rules: &["any trigger condition no longer holds"],
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
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: FAST_BAR_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "low", "vwap", "vwap_distance_pct", "mid_vwap_distance_pct", "tape_imbalance"],
        required_indicator_fields: &[],
        required_reference_fields: &[],
        trigger_rules: &[
            "previous and current closes causally cross VWAP",
            "vwap_distance_pct and mid_vwap_distance_pct agree with the cross",
            "tape_imbalance agrees with the cross direction",
        ],
        confirmation_rules: &["the current working-timeframe close remains on the reclaimed side of VWAP"],
        reject_rules: &["the causal cross or directional agreement no longer holds"],
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
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: FAST_BAR_TFS,
        confirmation_timeframes: &["5m"],
        required_bar_fields: &["close", "high", "low", "vwap", "tape_imbalance", "trade_rate"],
        required_indicator_fields: &[],
        required_reference_fields: &[],
        trigger_rules: &[
            "high and close exceed the prior finalized session high",
            "close is above VWAP and within 0.5 percent of the bar high",
            "tape_imbalance is positive and trade_rate exceeds 0.5",
        ],
        confirmation_rules: &["the finalized working-timeframe bar carries all required evidence"],
        reject_rules: &["price or tape confirmation no longer holds"],
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
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: TICK_TFS,
        confirmation_timeframes: &["1m"],
        required_bar_fields: &["close", "high", "low", "spread_bps_close", "quote_rate_accel", "liquidity_score", "tape_imbalance"],
        required_indicator_fields: &[],
        required_reference_fields: &[],
        trigger_rules: &[
            "previous spread_bps_close is at least 1.5 times the current spread",
            "quote_rate_accel is positive and liquidity_score improves",
            "absolute tape_imbalance exceeds 0.15 and determines direction",
        ],
        confirmation_rules: &["the finalized working-timeframe bar confirms the recovery"],
        reject_rules: &["the recovery conditions no longer hold"],
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
