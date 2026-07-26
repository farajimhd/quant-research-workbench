use serde::Serialize;

/// QMD owns reusable, causal market observations. Strategies combine these
/// observations with indicators and non-market signals into trade setups.
#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalCategory {
    Flow,
    Activity,
    Vwap,
    Liquidity,
    Divergence,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalPriority {
    P0,
    P1,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalComputeMode {
    RealtimeTick,
    RealtimeBarClose,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalPersistencePolicy {
    DecisionSnapshotOnly,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignalStatus {
    Implemented,
}

#[derive(Clone, Copy, Debug, Serialize)]
pub struct SignalMethodEntry {
    pub key: &'static str,
    pub label: &'static str,
    pub signal_version: u16,
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
    pub input_basis: &'static str,
    pub calculation_windows: &'static [&'static str],
    pub evaluation_mode: &'static str,
    pub update_trigger: &'static str,
    pub publication_cadence: &'static str,
    pub publication_interval_ms: Option<u64>,
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
    pub rank_score_required: bool,
}

pub fn signal_taxonomy_catalog() -> Vec<SignalTaxonomyEntry<'static>> {
    signal_catalog()
        .iter()
        .map(|definition| SignalTaxonomyEntry {
            definition,
            domain: "market",
            producer: "qmd",
            score_required: true,
            rank_score_required: true,
        })
        .collect()
}

const EVENT_TFS: &[&str] = &["100ms"];
const CLOSED_BAR_TFS: &[&str] = &["1s", "10s", "30s", "1m"];
const EMPTY_TFS: &[&str] = &[];

const COMMON_EMITS: &[&str] = &[
    "schema_version",
    "signal_version",
    "engine_version",
    "event_id",
    "signal_id",
    "signal_key",
    "producer",
    "domain",
    "ticker",
    "working_timeframe",
    "clock",
    "observed_at",
    "effective_at",
    "state",
    "direction",
    "score",
    "rank_score",
    "confidence",
    "trigger_reason",
    "resolution_reason",
    "reference_price",
    "invalidation_price",
    "expires_at",
    "evidence",
];

const COMMON_SNAPSHOT: &[&str] = &[
    "close",
    "high",
    "low",
    "vwap",
    "return_1_bar",
    "volume_rate",
    "dollar_volume_rate",
    "trade_rate",
    "quote_rate",
    "tape_imbalance",
    "tape_imbalance_accel",
    "spread_bps",
    "liquidity_score",
    "depth_imbalance_proxy",
    "price_surprise",
    "activity_surprise",
    "flow_surprise",
    "liquidity_surprise",
];

const SIGNAL_CATALOG: &[SignalMethodEntry] = &[
    SignalMethodEntry {
        key: "directional_flow_acceleration",
        label: "Directional Flow Acceleration",
        signal_version: 1,
        category: SignalCategory::Flow,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::RealtimeTick,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: EVENT_TFS,
        confirmation_timeframes: EMPTY_TFS,
        required_bar_fields: &["tape_imbalance", "tape_imbalance_accel", "trade_count", "quote_count"],
        required_indicator_fields: EMPTY_TFS,
        required_reference_fields: EMPTY_TFS,
        trigger_rules: &[
            "directional tape pressure and its acceleration exceed their causal session baselines",
            "at least two eligible trades and one quote are present in the 100 ms observation window",
        ],
        confirmation_rules: &["flow direction remains stable while normalized strength is above threshold"],
        reject_rules: &["flow surprise falls below threshold or reverses"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Surfaces abrupt buyer- or seller-initiated flow without turning it into a breakout strategy.",
        input_basis: "event_native",
        calculation_windows: EVENT_TFS,
        evaluation_mode: "closed_only",
        update_trigger: "bar_close",
        publication_cadence: "interval",
        publication_interval_ms: Some(100),
    },
    SignalMethodEntry {
        key: "price_volume_expansion",
        label: "Price and Volume Expansion",
        signal_version: 1,
        category: SignalCategory::Activity,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: CLOSED_BAR_TFS,
        confirmation_timeframes: EMPTY_TFS,
        required_bar_fields: &["return_1_bar", "volume_rate", "dollar_volume_rate", "trade_rate"],
        required_indicator_fields: EMPTY_TFS,
        required_reference_fields: EMPTY_TFS,
        trigger_rules: &[
            "absolute price return is an exceptional move versus its causal session baseline",
            "volume or dollar-volume rate expands concurrently versus its causal session baseline",
        ],
        confirmation_rules: &["price and activity surprises remain directionally coherent at bar close"],
        reject_rules: &["either price or activity surprise falls below threshold"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Captures the sudden price-and-volume increase or decrease requested for scanner ranking.",
        input_basis: "bar_derived",
        calculation_windows: CLOSED_BAR_TFS,
        evaluation_mode: "closed_only",
        update_trigger: "bar_close",
        publication_cadence: "bar_close",
        publication_interval_ms: None,
    },
    SignalMethodEntry {
        key: "vwap_transition",
        label: "VWAP Transition",
        signal_version: 1,
        category: SignalCategory::Vwap,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::RealtimeBarClose,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: CLOSED_BAR_TFS,
        confirmation_timeframes: EMPTY_TFS,
        required_bar_fields: &["close", "vwap", "vwap_distance_pct", "mid_vwap_distance_pct", "tape_imbalance"],
        required_indicator_fields: EMPTY_TFS,
        required_reference_fields: EMPTY_TFS,
        trigger_rules: &[
            "the prior and current finalized closes causally cross session VWAP",
            "trade and quote-mid VWAP distances agree and tape does not oppose the transition",
        ],
        confirmation_rules: &["the finalized close remains on the transitioned side of VWAP"],
        reject_rules: &["price crosses back through VWAP or evidence becomes contradictory"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Reports a reusable transition around VWAP; a strategy decides whether it is a reclaim, rejection, or continuation setup.",
        input_basis: "bar_derived",
        calculation_windows: CLOSED_BAR_TFS,
        evaluation_mode: "closed_only",
        update_trigger: "bar_close",
        publication_cadence: "bar_close",
        publication_interval_ms: None,
    },
    SignalMethodEntry {
        key: "liquidity_dislocation",
        label: "Liquidity Dislocation",
        signal_version: 1,
        category: SignalCategory::Liquidity,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::RealtimeTick,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: EVENT_TFS,
        confirmation_timeframes: EMPTY_TFS,
        required_bar_fields: &["spread_bps_close", "liquidity_score", "quoted_bid_size_mean", "quoted_ask_size_mean", "quote_rate"],
        required_indicator_fields: EMPTY_TFS,
        required_reference_fields: EMPTY_TFS,
        trigger_rules: &[
            "spread widens exceptionally versus its causal session baseline",
            "displayed liquidity or quote activity deteriorates concurrently",
        ],
        confirmation_rules: &["the normalized liquidity shock remains above threshold"],
        reject_rules: &["spread and displayed liquidity return inside their baseline range"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Marks a routeability and execution-risk observation independently of directional strategy logic.",
        input_basis: "event_native",
        calculation_windows: EVENT_TFS,
        evaluation_mode: "closed_only",
        update_trigger: "bar_close",
        publication_cadence: "on_change",
        publication_interval_ms: None,
    },
    SignalMethodEntry {
        key: "liquidity_recovery",
        label: "Liquidity Recovery",
        signal_version: 1,
        category: SignalCategory::Liquidity,
        priority: SignalPriority::P1,
        compute_mode: SignalComputeMode::RealtimeTick,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: EVENT_TFS,
        confirmation_timeframes: EMPTY_TFS,
        required_bar_fields: &["spread_bps_close", "liquidity_score", "quote_rate", "quote_rate_accel"],
        required_indicator_fields: EMPTY_TFS,
        required_reference_fields: EMPTY_TFS,
        trigger_rules: &[
            "the prior observation was a normalized liquidity dislocation",
            "spread contracts while liquidity score or quote activity recovers",
        ],
        confirmation_rules: &["recovery magnitude remains above threshold"],
        reject_rules: &["spread widens again or liquidity recovery stalls"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Reports restoration of routeable liquidity after a measurable shock without asserting an entry.",
        input_basis: "event_native",
        calculation_windows: EVENT_TFS,
        evaluation_mode: "closed_only",
        update_trigger: "bar_close",
        publication_cadence: "on_change",
        publication_interval_ms: None,
    },
    SignalMethodEntry {
        key: "flow_price_divergence",
        label: "Flow and Price Divergence",
        signal_version: 1,
        category: SignalCategory::Divergence,
        priority: SignalPriority::P0,
        compute_mode: SignalComputeMode::RealtimeTick,
        persistence_policy: SignalPersistencePolicy::DecisionSnapshotOnly,
        status: SignalStatus::Implemented,
        working_timeframes: EVENT_TFS,
        confirmation_timeframes: EMPTY_TFS,
        required_bar_fields: &["return_1_bar", "tape_imbalance", "tape_imbalance_accel", "trade_count"],
        required_indicator_fields: EMPTY_TFS,
        required_reference_fields: EMPTY_TFS,
        trigger_rules: &[
            "directional flow is exceptional versus its causal session baseline",
            "price response is flat or opposite to the aggressive flow direction",
        ],
        confirmation_rules: &["the mismatch persists without price accepting in the flow direction"],
        reject_rules: &["price response aligns with flow or flow surprise dissipates"],
        emits: COMMON_EMITS,
        snapshot_fields: COMMON_SNAPSHOT,
        rationale: "Surfaces absorption-like evidence for strategies to combine with structure or level indicators.",
        input_basis: "event_native",
        calculation_windows: EVENT_TFS,
        evaluation_mode: "closed_only",
        update_trigger: "bar_close",
        publication_cadence: "interval",
        publication_interval_ms: Some(100),
    },
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_contains_only_the_six_implemented_market_observations() {
        let catalog = signal_taxonomy_catalog();
        assert_eq!(catalog.len(), 6);
        assert_eq!(
            catalog
                .iter()
                .map(|entry| entry.definition.key)
                .collect::<Vec<_>>(),
            vec![
                "directional_flow_acceleration",
                "price_volume_expansion",
                "vwap_transition",
                "liquidity_dislocation",
                "liquidity_recovery",
                "flow_price_divergence",
            ]
        );
        assert!(catalog.iter().all(|entry| {
            entry.domain == "market"
                && entry.producer == "qmd"
                && entry.score_required
                && entry.rank_score_required
        }));
    }

    #[test]
    fn event_and_bar_clocks_are_explicit() {
        let catalog = signal_catalog();
        let flow = catalog
            .iter()
            .find(|entry| entry.key == "directional_flow_acceleration")
            .unwrap();
        assert_eq!(flow.working_timeframes, &["100ms"]);
        assert_eq!(flow.input_basis, "event_native");
        assert_eq!(flow.publication_interval_ms, Some(100));

        let expansion = catalog
            .iter()
            .find(|entry| entry.key == "price_volume_expansion")
            .unwrap();
        assert_eq!(expansion.working_timeframes, &["1s", "10s", "30s", "1m"]);
        assert_eq!(expansion.publication_cadence, "bar_close");
    }
}
