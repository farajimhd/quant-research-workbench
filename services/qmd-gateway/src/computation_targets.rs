use crate::capability_catalog::{computation_capability_catalog, CostClass, ExecutionScope};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::{Arc, RwLock};
use std::time::{Duration as StdDuration, Instant};

#[derive(Clone, Debug, Deserialize)]
pub struct ComputationTargetRequest {
    pub target_id: String,
    pub owner: String,
    pub scope: ExecutionScope,
    pub tickers: Vec<String>,
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub timeframes: Vec<String>,
    #[serde(default)]
    pub parameter_hash: String,
    #[serde(default)]
    pub anchor: String,
    #[serde(default)]
    pub source_revision: String,
    pub ttl_seconds: Option<u64>,
    #[serde(default)]
    pub correlation_id: String,
    #[serde(default)]
    pub causation_id: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct ComputationTargetLease {
    pub target_id: String,
    pub owner: String,
    pub scope: ExecutionScope,
    pub tickers: Vec<String>,
    pub capabilities: Vec<String>,
    pub timeframes: Vec<String>,
    pub parameter_hash: String,
    pub anchor: String,
    pub source_revision: String,
    pub expires_at: Option<DateTime<Utc>>,
    pub updated_at: DateTime<Utc>,
    pub correlation_id: String,
    pub causation_id: String,
    pub event_driven: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct ComputationTargetSnapshot {
    pub schema_version: u16,
    pub as_of: DateTime<Utc>,
    pub active_target_count: usize,
    pub active_symbol_count: usize,
    pub active_requirement_count: usize,
    pub targets: Vec<ComputationTargetLease>,
    pub symbol_ref_counts: BTreeMap<String, usize>,
    pub capability_ref_counts: BTreeMap<String, usize>,
    pub estimated_demand_units: u64,
    pub requested_demand_units: u64,
    pub deduplicated_demand_units_saved: u64,
    pub requirement_ref_counts: BTreeMap<String, usize>,
    pub requirements: Vec<EffectiveComputationRequirement>,
    pub scope_capability_counts: BTreeMap<String, usize>,
    pub scope_estimated_demand_units: BTreeMap<String, u64>,
    pub scope_symbol_counts: BTreeMap<String, usize>,
    pub scope_target_counts: BTreeMap<String, usize>,
    pub target_estimated_demand_units: BTreeMap<String, u64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ComputationTargetSummary {
    pub schema_version: u16,
    pub as_of: DateTime<Utc>,
    pub active_target_count: usize,
    pub active_symbol_count: usize,
    pub active_requirement_count: usize,
    pub capability_ref_counts: BTreeMap<String, usize>,
    pub estimated_demand_units: u64,
    pub requested_demand_units: u64,
    pub deduplicated_demand_units_saved: u64,
    pub scope_capability_counts: BTreeMap<String, usize>,
    pub scope_estimated_demand_units: BTreeMap<String, u64>,
    pub scope_symbol_counts: BTreeMap<String, usize>,
    pub scope_target_counts: BTreeMap<String, usize>,
    pub target_estimated_demand_units: BTreeMap<String, u64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct EffectiveComputationRequirement {
    pub requirement_id: String,
    pub ticker: String,
    pub capability: String,
    pub timeframe: String,
    pub implementation_version: u16,
    pub parameter_hash: String,
    pub anchor: String,
    pub source_revision: String,
    pub ref_count: usize,
    pub demand_units: u64,
    pub scopes: BTreeSet<String>,
}

#[derive(Default)]
struct ComputationTargets {
    targets: HashMap<String, ComputationTargetLease>,
}

#[derive(Clone)]
struct CachedComputationTargetSummary {
    expires_at: Instant,
    value: ComputationTargetSummary,
}

#[derive(Clone, Default)]
pub struct SharedComputationTargets {
    inner: Arc<RwLock<ComputationTargets>>,
    summary_cache: Arc<RwLock<Option<CachedComputationTargetSummary>>>,
}

impl SharedComputationTargets {
    pub fn replace(
        &self,
        request: ComputationTargetRequest,
    ) -> Result<ComputationTargetLease, String> {
        let now = Utc::now();
        let target_id = request.target_id.trim().to_string();
        let owner = request.owner.trim().to_string();
        if target_id.is_empty() {
            return Err("target_id is required".to_string());
        }
        if owner.is_empty() {
            return Err("owner is required".to_string());
        }
        if matches!(
            request.scope,
            ExecutionScope::UniversalIngest | ExecutionScope::CoreScan
        ) {
            return Err(
                "leased computation targets must use watchlist, strategy_run, request, or offline scope"
                    .to_string(),
            );
        }
        let tickers = normalize_values(request.tickers, true);
        if tickers.is_empty() {
            return Err("at least one ticker is required".to_string());
        }
        let capabilities = normalize_values(request.capabilities, false);
        if capabilities.is_empty() {
            return Err("at least one capability is required".to_string());
        }
        validate_capabilities(&capabilities, request.scope)?;
        let timeframes = normalize_values(request.timeframes, false)
            .into_iter()
            .map(|value| value.to_ascii_lowercase())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        let parameter_hash = normalize_requirement_component(
            &request.parameter_hash,
            "implementation_default",
            "parameter_hash",
        )?;
        let anchor = normalize_requirement_component(&request.anchor, "event_time", "anchor")?;
        let source_revision = normalize_requirement_component(
            &request.source_revision,
            "advancing_live",
            "source_revision",
        )?;
        let expires_at = match request.ttl_seconds {
            Some(0) => return Err("ttl_seconds must be positive when provided".to_string()),
            Some(seconds) => Some(
                now + Duration::seconds(
                    i64::try_from(seconds.min(i64::MAX as u64)).unwrap_or(i64::MAX),
                ),
            ),
            None => None,
        };
        let event_driven = capabilities_require_events(&capabilities);
        let lease = ComputationTargetLease {
            target_id: target_id.clone(),
            owner,
            scope: request.scope,
            tickers,
            capabilities,
            timeframes,
            parameter_hash,
            anchor,
            source_revision,
            expires_at,
            updated_at: now,
            correlation_id: lineage_identity(&request.correlation_id, "lease", &target_id),
            causation_id: lineage_identity(&request.causation_id, "target", &target_id),
            event_driven,
        };
        let mut state = self
            .inner
            .write()
            .expect("computation target lock poisoned");
        prune_expired(&mut state.targets, now);
        state.targets.insert(target_id, lease.clone());
        *self
            .summary_cache
            .write()
            .expect("computation summary cache lock poisoned") = None;
        Ok(lease)
    }

    pub fn remove(&self, target_id: &str) -> bool {
        let removed = self
            .inner
            .write()
            .expect("computation target lock poisoned")
            .targets
            .remove(target_id.trim())
            .is_some();
        if removed {
            *self
                .summary_cache
                .write()
                .expect("computation summary cache lock poisoned") = None;
        }
        removed
    }

    pub fn requires_focused_computation(&self, ticker: &str) -> bool {
        let now = Utc::now();
        let normalized = ticker.trim().to_ascii_uppercase();
        let state = self.inner.read().expect("computation target lock poisoned");
        state.targets.values().any(|target| {
            target.expires_at.map(|expiry| expiry > now).unwrap_or(true)
                && target.tickers.binary_search(&normalized).is_ok()
        })
    }

    pub fn requires_event_computation(&self, ticker: &str) -> bool {
        let now = Utc::now();
        let normalized = ticker.trim().to_ascii_uppercase();
        let state = self.inner.read().expect("computation target lock poisoned");
        state.targets.values().any(|target| {
            target.event_driven
                && target.expires_at.map(|expiry| expiry > now).unwrap_or(true)
                && target.tickers.binary_search(&normalized).is_ok()
        })
    }

    pub fn requires_bar_computation(&self, ticker: &str, timeframe: &str) -> bool {
        let now = Utc::now();
        let normalized_ticker = ticker.trim().to_ascii_uppercase();
        let normalized_timeframe = timeframe.trim().to_ascii_lowercase();
        let state = self.inner.read().expect("computation target lock poisoned");
        state.targets.values().any(|target| {
            target.expires_at.map(|expiry| expiry > now).unwrap_or(true)
                && target.tickers.binary_search(&normalized_ticker).is_ok()
                && (target.timeframes.is_empty()
                    || normalized_timeframe == "100ms"
                    || target
                        .timeframes
                        .binary_search(&normalized_timeframe)
                        .is_ok())
        })
    }

    pub fn snapshot(&self) -> ComputationTargetSnapshot {
        let now = Utc::now();
        let mut state = self
            .inner
            .write()
            .expect("computation target lock poisoned");
        prune_expired(&mut state.targets, now);
        let mut targets = state.targets.values().cloned().collect::<Vec<_>>();
        targets.sort_by(|left, right| left.target_id.cmp(&right.target_id));
        let mut symbol_ref_counts = BTreeMap::new();
        let mut capability_ref_counts = BTreeMap::new();
        let mut scope_symbols = BTreeMap::<String, BTreeSet<String>>::new();
        let mut scope_capabilities = BTreeMap::<String, BTreeSet<String>>::new();
        let mut scope_target_counts = BTreeMap::new();
        let mut scope_estimated_demand_units = BTreeMap::new();
        let mut target_estimated_demand_units = BTreeMap::new();
        let mut requirement_ref_counts = BTreeMap::<String, usize>::new();
        let mut effective_requirement_costs = BTreeMap::<String, u64>::new();
        let mut effective_requirements = BTreeMap::<String, EffectiveComputationRequirement>::new();
        let mut scope_requirement_costs = BTreeMap::<String, BTreeMap<String, u64>>::new();
        let catalog = computation_capability_catalog();
        for target in &targets {
            let scope = scope_key(target.scope).to_string();
            *scope_target_counts.entry(scope.clone()).or_insert(0) += 1;
            for ticker in &target.tickers {
                *symbol_ref_counts.entry(ticker.clone()).or_insert(0) += 1;
                scope_symbols
                    .entry(scope.clone())
                    .or_default()
                    .insert(ticker.clone());
            }
            for capability in &target.capabilities {
                *capability_ref_counts.entry(capability.clone()).or_insert(0) += 1;
                scope_capabilities
                    .entry(scope.clone())
                    .or_default()
                    .insert(capability.clone());
            }
            let mut timeframes = if target.timeframes.is_empty() {
                vec!["*".to_string()]
            } else {
                target.timeframes.clone()
            };
            if !timeframes.iter().any(|value| value == "100ms") {
                timeframes.push("100ms".to_string());
            }
            timeframes.sort();
            timeframes.dedup();
            let mut target_requirements = BTreeMap::<String, u64>::new();
            for ticker in &target.tickers {
                for capability in &target.capabilities {
                    let Some(definition) = catalog.iter().find(|row| row.key == capability) else {
                        continue;
                    };
                    let weight = cost_weight(definition.cost_class);
                    for timeframe in &timeframes {
                        let requirement = format!(
                            "{ticker}|{capability}|{timeframe}|v{}|p:{}|a:{}|r:{}",
                            definition.implementation_version,
                            target.parameter_hash,
                            target.anchor,
                            target.source_revision,
                        );
                        target_requirements.insert(requirement.clone(), weight);
                        *requirement_ref_counts
                            .entry(requirement.clone())
                            .or_insert(0) += 1;
                        let effective = effective_requirements
                            .entry(requirement.clone())
                            .or_insert_with(|| EffectiveComputationRequirement {
                                requirement_id: requirement.clone(),
                                ticker: ticker.clone(),
                                capability: capability.clone(),
                                timeframe: timeframe.clone(),
                                implementation_version: definition.implementation_version,
                                parameter_hash: target.parameter_hash.clone(),
                                anchor: target.anchor.clone(),
                                source_revision: target.source_revision.clone(),
                                ref_count: 0,
                                demand_units: weight,
                                scopes: BTreeSet::new(),
                            });
                        effective.ref_count += 1;
                        effective.scopes.insert(scope.clone());
                        effective_requirement_costs.insert(requirement.clone(), weight);
                        scope_requirement_costs
                            .entry(scope.clone())
                            .or_default()
                            .insert(requirement, weight);
                    }
                }
            }
            let demand = target_requirements
                .values()
                .copied()
                .fold(0_u64, u64::saturating_add);
            target_estimated_demand_units.insert(target.target_id.clone(), demand);
        }
        let scope_symbol_counts = scope_symbols
            .into_iter()
            .map(|(scope, symbols)| (scope, symbols.len()))
            .collect();
        let scope_capability_counts = scope_capabilities
            .into_iter()
            .map(|(scope, capabilities)| (scope, capabilities.len()))
            .collect();
        for (scope, requirements) in scope_requirement_costs {
            scope_estimated_demand_units.insert(
                scope,
                requirements
                    .values()
                    .copied()
                    .fold(0_u64, u64::saturating_add),
            );
        }
        let requested_demand_units = target_estimated_demand_units
            .values()
            .copied()
            .fold(0_u64, u64::saturating_add);
        let estimated_demand_units = effective_requirement_costs
            .values()
            .copied()
            .fold(0_u64, u64::saturating_add);
        ComputationTargetSnapshot {
            schema_version: 5,
            as_of: now,
            active_target_count: targets.len(),
            active_symbol_count: symbol_ref_counts.len(),
            active_requirement_count: requirement_ref_counts.len(),
            targets,
            symbol_ref_counts,
            capability_ref_counts,
            estimated_demand_units,
            requested_demand_units,
            deduplicated_demand_units_saved: requested_demand_units
                .saturating_sub(estimated_demand_units),
            requirement_ref_counts,
            requirements: effective_requirements.into_values().collect(),
            scope_capability_counts,
            scope_estimated_demand_units,
            scope_symbol_counts,
            scope_target_counts,
            target_estimated_demand_units,
        }
    }

    pub fn summary(&self) -> ComputationTargetSummary {
        let now = Instant::now();
        if let Some(cached) = self
            .summary_cache
            .read()
            .expect("computation summary cache lock poisoned")
            .as_ref()
            .filter(|cached| cached.expires_at > now)
        {
            return cached.value.clone();
        }
        let snapshot = self.snapshot();
        let summary = ComputationTargetSummary {
            schema_version: snapshot.schema_version,
            as_of: snapshot.as_of,
            active_target_count: snapshot.active_target_count,
            active_symbol_count: snapshot.active_symbol_count,
            active_requirement_count: snapshot.active_requirement_count,
            capability_ref_counts: snapshot.capability_ref_counts,
            estimated_demand_units: snapshot.estimated_demand_units,
            requested_demand_units: snapshot.requested_demand_units,
            deduplicated_demand_units_saved: snapshot.deduplicated_demand_units_saved,
            scope_capability_counts: snapshot.scope_capability_counts,
            scope_estimated_demand_units: snapshot.scope_estimated_demand_units,
            scope_symbol_counts: snapshot.scope_symbol_counts,
            scope_target_counts: snapshot.scope_target_counts,
            target_estimated_demand_units: snapshot.target_estimated_demand_units,
        };
        *self
            .summary_cache
            .write()
            .expect("computation summary cache lock poisoned") =
            Some(CachedComputationTargetSummary {
                expires_at: now + StdDuration::from_secs(1),
                value: summary.clone(),
            });
        summary
    }
}

fn scope_key(scope: ExecutionScope) -> &'static str {
    match scope {
        ExecutionScope::UniversalIngest => "universal_ingest",
        ExecutionScope::CoreScan => "core_scan",
        ExecutionScope::Watchlist => "watchlist",
        ExecutionScope::StrategyRun => "strategy_run",
        ExecutionScope::Request => "request",
        ExecutionScope::Offline => "offline",
    }
}

fn cost_weight(cost: CostClass) -> u64 {
    match cost {
        CostClass::Minimal => 1,
        CostClass::Low => 2,
        CostClass::Medium => 4,
        CostClass::High => 8,
        CostClass::Offline => 16,
    }
}

fn normalize_values(values: Vec<String>, uppercase: bool) -> Vec<String> {
    values
        .into_iter()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .map(|value| {
            if uppercase {
                value.to_ascii_uppercase()
            } else {
                value
            }
        })
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn normalize_requirement_component(
    value: &str,
    fallback: &str,
    field: &str,
) -> Result<String, String> {
    let normalized = value.trim();
    let normalized = if normalized.is_empty() {
        fallback
    } else {
        normalized
    };
    if normalized.len() > 256
        || normalized.contains('|')
        || normalized.chars().any(char::is_control)
    {
        return Err(format!(
            "{field} must be at most 256 characters and cannot contain control characters or |"
        ));
    }
    Ok(normalized.to_string())
}

fn lineage_identity(requested: &str, fallback_prefix: &str, target_id: &str) -> String {
    let candidate = requested.trim();
    if valid_lineage_identity(candidate) {
        return candidate.to_string();
    }
    let fallback = format!("{fallback_prefix}:{target_id}");
    if valid_lineage_identity(&fallback) {
        fallback
    } else {
        format!("{fallback_prefix}:unattributed")
    }
}

fn valid_lineage_identity(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'))
}

fn validate_capabilities(requested: &[String], scope: ExecutionScope) -> Result<(), String> {
    let catalog = computation_capability_catalog();
    for key in requested {
        let Some(capability) = catalog.iter().find(|row| row.key == key) else {
            return Err(format!("unknown computation capability: {key}"));
        };
        if !capability.allowed_scopes.contains(&scope) {
            return Err(format!(
                "capability {key} is not allowed in the requested execution scope"
            ));
        }
    }
    Ok(())
}

fn capabilities_require_events(capabilities: &[String]) -> bool {
    let catalog = computation_capability_catalog();
    capabilities.iter().any(|key| {
        catalog
            .iter()
            .find(|row| row.key == key)
            .map(|row| {
                row.cadence == "on_event_or_state_change"
                    || (row.kind == "market_observation"
                        && row.timeframes.iter().any(|timeframe| *timeframe == "100ms"))
            })
            .unwrap_or(false)
    })
}

fn prune_expired(targets: &mut HashMap<String, ComputationTargetLease>, now: DateTime<Utc>) {
    targets.retain(|_, target| target.expires_at.map(|expiry| expiry > now).unwrap_or(true));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request(target_id: &str, scope: ExecutionScope) -> ComputationTargetRequest {
        ComputationTargetRequest {
            target_id: target_id.to_string(),
            owner: "test".to_string(),
            scope,
            tickers: vec!["aapl".to_string(), "MSFT".to_string(), "AAPL".to_string()],
            capabilities: vec!["opening_range".to_string()],
            timeframes: vec!["1m".to_string()],
            parameter_hash: "params-v1".to_string(),
            anchor: "new_york_session".to_string(),
            source_revision: "advancing_live".to_string(),
            ttl_seconds: None,
            correlation_id: String::new(),
            causation_id: String::new(),
        }
    }

    #[test]
    fn unions_and_reference_counts_focused_targets() {
        let targets = SharedComputationTargets::default();
        targets
            .replace(request("watchlist:one", ExecutionScope::Watchlist))
            .unwrap();
        let mut second = request("request:chart", ExecutionScope::Request);
        second.tickers = vec!["AAPL".to_string()];
        targets.replace(second).unwrap();

        let snapshot = targets.snapshot();
        assert_eq!(snapshot.schema_version, 5);
        assert_eq!(snapshot.active_target_count, 2);
        assert_eq!(snapshot.active_symbol_count, 2);
        assert_eq!(snapshot.symbol_ref_counts.get("AAPL"), Some(&2));
        assert_eq!(snapshot.scope_target_counts.get("watchlist"), Some(&1));
        assert_eq!(snapshot.scope_target_counts.get("request"), Some(&1));
        assert_eq!(snapshot.scope_symbol_counts.get("watchlist"), Some(&2));
        assert_eq!(snapshot.targets[0].correlation_id, "lease:request:chart");
        assert_eq!(snapshot.targets[0].causation_id, "target:request:chart");
        assert!(snapshot.estimated_demand_units > 0);
        assert!(snapshot.requested_demand_units > snapshot.estimated_demand_units);
        assert!(snapshot.deduplicated_demand_units_saved > 0);
        assert_eq!(
            snapshot.active_requirement_count,
            snapshot.requirement_ref_counts.len()
        );
        assert_eq!(
            snapshot
                .requirement_ref_counts
                .get("AAPL|opening_range|100ms|v1|p:params-v1|a:new_york_session|r:advancing_live"),
            Some(&2)
        );
        assert!(targets.requires_focused_computation("aapl"));
        let aapl = snapshot
            .requirements
            .iter()
            .find(|row| {
                row.ticker == "AAPL"
                    && row.capability == "opening_range"
                    && row.timeframe == "100ms"
            })
            .unwrap();
        assert_eq!(aapl.ref_count, 2);
        assert_eq!(
            aapl.scopes,
            BTreeSet::from(["request".to_string(), "watchlist".to_string()])
        );
        assert_eq!(aapl.parameter_hash, "params-v1");
        assert_eq!(aapl.anchor, "new_york_session");
        assert_eq!(aapl.source_revision, "advancing_live");
        assert!(targets.remove("request:chart"));
        assert_eq!(targets.snapshot().symbol_ref_counts.get("AAPL"), Some(&1));
    }

    #[test]
    fn summary_cache_is_invalidated_by_target_mutations() {
        let targets = SharedComputationTargets::default();
        targets
            .replace(request("watchlist:one", ExecutionScope::Watchlist))
            .unwrap();
        let first = targets.summary();
        let cached = targets.summary();
        assert_eq!(first.active_target_count, 1);
        assert_eq!(cached.as_of, first.as_of);

        let mut second = request("request:chart", ExecutionScope::Request);
        second.tickers = vec!["NVDA".to_string()];
        targets.replace(second).unwrap();
        let after_replace = targets.summary();
        assert_eq!(after_replace.active_target_count, 2);
        assert_eq!(after_replace.active_symbol_count, 3);

        assert!(targets.remove("request:chart"));
        let after_remove = targets.summary();
        assert_eq!(after_remove.active_target_count, 1);
        assert_eq!(after_remove.active_symbol_count, 2);
    }

    #[test]
    fn does_not_deduplicate_distinct_parameters_anchors_or_revisions() {
        let targets = SharedComputationTargets::default();
        targets
            .replace(request("watchlist:one", ExecutionScope::Watchlist))
            .unwrap();
        let mut different = request("watchlist:two", ExecutionScope::Watchlist);
        different.tickers = vec!["AAPL".to_string()];
        different.parameter_hash = "params-v2".to_string();
        different.anchor = "utc_day".to_string();
        different.source_revision = "pinned-revision-17".to_string();
        targets.replace(different).unwrap();

        let snapshot = targets.snapshot();
        assert_eq!(snapshot.active_requirement_count, 6);
        assert_eq!(snapshot.deduplicated_demand_units_saved, 0);
        assert!(snapshot
            .requirement_ref_counts
            .keys()
            .any(|key| { key.contains("p:params-v2|a:utc_day|r:pinned-revision-17") }));
    }

    #[test]
    fn rejects_ambiguous_requirement_components() {
        let targets = SharedComputationTargets::default();
        let mut invalid = request("watchlist:invalid", ExecutionScope::Watchlist);
        invalid.parameter_hash = "left|right".to_string();
        assert!(targets
            .replace(invalid)
            .unwrap_err()
            .contains("parameter_hash"));
    }

    #[test]
    fn rejects_scope_broadening_and_unknown_capabilities() {
        let targets = SharedComputationTargets::default();
        assert!(targets
            .replace(request("core", ExecutionScope::CoreScan))
            .unwrap_err()
            .contains("leased computation targets"));
        let mut invalid = request("invalid", ExecutionScope::Watchlist);
        invalid.capabilities = vec!["not-real".to_string()];
        assert!(targets.replace(invalid).unwrap_err().contains("unknown"));
    }

    #[test]
    fn zero_ttl_is_rejected() {
        let targets = SharedComputationTargets::default();
        let mut invalid = request("expired", ExecutionScope::Watchlist);
        invalid.ttl_seconds = Some(0);
        assert!(targets.replace(invalid).unwrap_err().contains("positive"));
    }

    #[test]
    fn preserves_explicit_autonomous_lineage() {
        let targets = SharedComputationTargets::default();
        let mut explicit = request("watchlist:lineage", ExecutionScope::Watchlist);
        explicit.correlation_id = "run:watchlist-small".to_string();
        explicit.causation_id = "event:membership-17".to_string();

        let lease = targets.replace(explicit).unwrap();

        assert_eq!(lease.correlation_id, "run:watchlist-small");
        assert_eq!(lease.causation_id, "event:membership-17");
    }

    #[test]
    fn focused_bar_routing_honors_timeframes_and_keeps_canonical_base_dependency() {
        let targets = SharedComputationTargets::default();
        let mut focused = request("chart", ExecutionScope::Request);
        focused.tickers = vec!["aapl".to_string()];
        focused.timeframes = vec!["1M".to_string()];
        targets.replace(focused).unwrap();

        assert!(targets.requires_bar_computation("AAPL", "1m"));
        assert!(targets.requires_bar_computation("aapl", "100ms"));
        assert!(!targets.requires_bar_computation("AAPL", "5m"));
        assert!(!targets.requires_bar_computation("MSFT", "1m"));
    }

    #[test]
    fn bar_only_leases_do_not_enable_tick_event_processing() {
        let targets = SharedComputationTargets::default();
        let mut bar_only = request("chart", ExecutionScope::Request);
        bar_only.tickers = vec!["AAPL".to_string()];
        targets.replace(bar_only).unwrap();

        assert!(targets.requires_focused_computation("AAPL"));
        assert!(!targets.requires_event_computation("AAPL"));

        let mut tick = request("tape", ExecutionScope::Watchlist);
        tick.tickers = vec!["AAPL".to_string()];
        tick.capabilities = vec!["tape_rates".to_string()];
        targets.replace(tick).unwrap();
        assert!(targets.requires_event_computation("AAPL"));
        targets.remove("tape");
        assert!(!targets.requires_event_computation("AAPL"));

        let mut event_signal = request("signal", ExecutionScope::Watchlist);
        event_signal.tickers = vec!["AAPL".to_string()];
        event_signal.capabilities = vec!["flow_structure_alignment".to_string()];
        event_signal.timeframes = vec!["100ms".to_string()];
        targets.replace(event_signal).unwrap();
        assert!(targets.requires_event_computation("AAPL"));
    }
}
