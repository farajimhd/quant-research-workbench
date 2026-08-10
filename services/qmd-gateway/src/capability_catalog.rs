use crate::indicator_catalog::{indicator_catalog, ImplementationStatus, IndicatorPriority};
use crate::signal_catalog::signal_catalog;
use serde::Serialize;

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionScope {
    UniversalIngest,
    CoreScan,
    Watchlist,
    StrategyRun,
    Request,
    Offline,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConfigurationPolicy {
    Locked,
    Configurable,
    Generated,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CostClass {
    Minimal,
    Low,
    Medium,
    High,
    Offline,
}

#[derive(Clone, Debug, Serialize)]
pub struct ComputationCapability<'a> {
    pub key: &'a str,
    pub label: &'a str,
    pub producer: &'static str,
    pub kind: &'static str,
    pub execution_scope: ExecutionScope,
    pub allowed_scopes: Vec<ExecutionScope>,
    pub configuration_policy: ConfigurationPolicy,
    pub implementation_status: &'static str,
    pub operational_status: &'static str,
    pub cost_class: CostClass,
    pub stateful: bool,
    pub inputs: &'a [&'a str],
    pub outputs: &'a [&'a str],
}

const UNIVERSAL_PRIMITIVES: &[(&str, &str, &[&str], &[&str])] = &[
    (
        "event_validation_encoding",
        "Canonical event validation and encoding",
        &[
            "source quote/trade event",
            "condition and exchange references",
        ],
        &["canonical compact event", "rejection reason"],
    ),
    (
        "point_in_time_source_identity",
        "Point-in-time source identity",
        &["source ticker", "event timestamp", "identity intervals"],
        &["stable source identity", "identity validity evidence"],
    ),
    (
        "event_order_sequence",
        "Event ordering and sequencing",
        &["source sequence", "SIP timestamp", "arrival timestamp"],
        &["ordered event", "sequence gap state", "continuation cursor"],
    ),
    (
        "nbbo_trade_state",
        "Current NBBO and eligible-trade state",
        &["canonical quotes", "canonical trades", "aggregation rules"],
        &[
            "current NBBO",
            "last eligible trade",
            "market state revision",
        ],
    ),
    (
        "freshness_quality",
        "Freshness and market-data quality",
        &["ordered canonical events", "market clock"],
        &["freshness", "quality flags", "degradation reason"],
    ),
    (
        "compact_persistence_fanout",
        "Compact persistence and bounded fanout",
        &["accepted compact event", "coverage checkpoint"],
        &[
            "q_live event row",
            "coverage update",
            "live event notification",
        ],
    ),
];

const CORE_FAMILIES: &[&str] = &[
    "core_bars",
    "quote_mid_spread_bars",
    "tape_rates",
    "nbbo_liquidity",
    "reference_context",
];

fn indicator_scope(key: &str, status: ImplementationStatus) -> ExecutionScope {
    if CORE_FAMILIES.contains(&key) {
        ExecutionScope::CoreScan
    } else {
        match status {
            ImplementationStatus::OfflineOnly => ExecutionScope::Offline,
            ImplementationStatus::StrategySpecific => ExecutionScope::StrategyRun,
            _ => ExecutionScope::Watchlist,
        }
    }
}

fn allowed_scopes(scope: ExecutionScope) -> Vec<ExecutionScope> {
    match scope {
        ExecutionScope::UniversalIngest => vec![ExecutionScope::UniversalIngest],
        ExecutionScope::CoreScan => vec![
            ExecutionScope::CoreScan,
            ExecutionScope::Watchlist,
            ExecutionScope::StrategyRun,
            ExecutionScope::Request,
            ExecutionScope::Offline,
        ],
        ExecutionScope::Watchlist => vec![
            ExecutionScope::Watchlist,
            ExecutionScope::StrategyRun,
            ExecutionScope::Request,
            ExecutionScope::Offline,
        ],
        ExecutionScope::StrategyRun => vec![
            ExecutionScope::StrategyRun,
            ExecutionScope::Request,
            ExecutionScope::Offline,
        ],
        ExecutionScope::Request => vec![ExecutionScope::Request, ExecutionScope::Offline],
        ExecutionScope::Offline => vec![ExecutionScope::Offline],
    }
}

fn implementation_status(status: ImplementationStatus) -> &'static str {
    match status {
        ImplementationStatus::Implemented => "implemented",
        ImplementationStatus::PlannedRealtime => "planned_realtime",
        ImplementationStatus::StrategySpecific => "strategy_specific",
        ImplementationStatus::OfflineOnly => "offline_only",
        ImplementationStatus::ReferenceOnly => "reference_only",
    }
}

fn cost_class(scope: ExecutionScope, priority: IndicatorPriority) -> CostClass {
    match scope {
        ExecutionScope::UniversalIngest => CostClass::Minimal,
        ExecutionScope::CoreScan => CostClass::Low,
        ExecutionScope::Offline => CostClass::Offline,
        _ => match priority {
            IndicatorPriority::P0 | IndicatorPriority::P1 => CostClass::Medium,
            IndicatorPriority::P2 | IndicatorPriority::P3 => CostClass::High,
        },
    }
}

/// Canonical runtime computation vocabulary shared by QMD live and QMD History.
///
/// A capability may be moved only to a scope listed in `allowed_scopes`. This
/// enforces the computational funnel: expensive work can move down toward a
/// smaller population, but cannot silently move into the universal hot path.
pub fn computation_capability_catalog() -> Vec<ComputationCapability<'static>> {
    let mut rows = Vec::new();
    for (key, label, inputs, outputs) in UNIVERSAL_PRIMITIVES {
        rows.push(ComputationCapability {
            key,
            label,
            producer: "qmd",
            kind: "primitive",
            execution_scope: ExecutionScope::UniversalIngest,
            allowed_scopes: allowed_scopes(ExecutionScope::UniversalIngest),
            configuration_policy: ConfigurationPolicy::Locked,
            implementation_status: "implemented",
            operational_status: "ready",
            cost_class: CostClass::Minimal,
            stateful: true,
            inputs,
            outputs,
        });
    }

    rows.extend(indicator_catalog().iter().map(|definition| {
        let scope = indicator_scope(definition.key, definition.status);
        let configuration_policy = if matches!(scope, ExecutionScope::CoreScan)
            && matches!(definition.priority, IndicatorPriority::P0)
        {
            ConfigurationPolicy::Locked
        } else if matches!(scope, ExecutionScope::Offline) {
            ConfigurationPolicy::Generated
        } else {
            ConfigurationPolicy::Configurable
        };
        ComputationCapability {
            key: definition.key,
            label: definition.label,
            producer: "qmd",
            kind: "indicator_family",
            execution_scope: scope,
            allowed_scopes: allowed_scopes(scope),
            configuration_policy,
            implementation_status: implementation_status(definition.status),
            operational_status: "ready",
            cost_class: cost_class(scope, definition.priority),
            stateful: true,
            inputs: definition.inputs,
            outputs: definition.fields,
        }
    }));

    rows.extend(
        signal_catalog()
            .iter()
            .map(|definition| ComputationCapability {
                key: definition.key,
                label: definition.label,
                producer: "qmd",
                kind: "market_observation",
                execution_scope: ExecutionScope::Watchlist,
                allowed_scopes: allowed_scopes(ExecutionScope::Watchlist),
                configuration_policy: ConfigurationPolicy::Configurable,
                implementation_status: "implemented",
                operational_status: "ready",
                cost_class: CostClass::Medium,
                stateful: true,
                inputs: definition.required_indicator_fields,
                outputs: definition.emits,
            }),
    );
    rows
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn catalog_keys_are_unique_and_universal_scope_is_locked() {
        let catalog = computation_capability_catalog();
        let mut keys = HashSet::new();
        for row in &catalog {
            assert!(keys.insert(row.key), "duplicate capability key {}", row.key);
            if matches!(row.execution_scope, ExecutionScope::UniversalIngest) {
                assert!(matches!(
                    row.configuration_policy,
                    ConfigurationPolicy::Locked
                ));
                assert_eq!(row.allowed_scopes.len(), 1);
                assert!(matches!(
                    row.allowed_scopes[0],
                    ExecutionScope::UniversalIngest
                ));
            }
        }
        assert_eq!(
            catalog
                .iter()
                .filter(|row| matches!(row.execution_scope, ExecutionScope::UniversalIngest))
                .count(),
            6
        );
    }

    #[test]
    fn expensive_families_do_not_enter_the_all_market_scope() {
        let catalog = computation_capability_catalog();
        let opening_range = catalog
            .iter()
            .find(|row| row.key == "opening_range")
            .unwrap();
        assert!(matches!(
            opening_range.execution_scope,
            ExecutionScope::Watchlist
        ));
        let statistics = catalog.iter().find(|row| row.key == "statistics").unwrap();
        assert!(matches!(
            statistics.execution_scope,
            ExecutionScope::Offline
        ));
    }
}
