use crate::capability_catalog::{computation_capability_catalog, ExecutionScope};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::{Arc, RwLock};

#[derive(Clone, Debug, Deserialize)]
pub struct ComputationTargetRequest {
    pub target_id: String,
    pub owner: String,
    pub scope: ExecutionScope,
    pub tickers: Vec<String>,
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub timeframes: Vec<String>,
    pub ttl_seconds: Option<u64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ComputationTargetLease {
    pub target_id: String,
    pub owner: String,
    pub scope: ExecutionScope,
    pub tickers: Vec<String>,
    pub capabilities: Vec<String>,
    pub timeframes: Vec<String>,
    pub expires_at: Option<DateTime<Utc>>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ComputationTargetSnapshot {
    pub as_of: DateTime<Utc>,
    pub active_target_count: usize,
    pub active_symbol_count: usize,
    pub targets: Vec<ComputationTargetLease>,
    pub symbol_ref_counts: BTreeMap<String, usize>,
    pub capability_ref_counts: BTreeMap<String, usize>,
}

#[derive(Default)]
struct ComputationTargets {
    targets: HashMap<String, ComputationTargetLease>,
}

#[derive(Clone, Default)]
pub struct SharedComputationTargets {
    inner: Arc<RwLock<ComputationTargets>>,
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
        let expires_at = match request.ttl_seconds {
            Some(0) => return Err("ttl_seconds must be positive when provided".to_string()),
            Some(seconds) => Some(
                now + Duration::seconds(
                    i64::try_from(seconds.min(i64::MAX as u64)).unwrap_or(i64::MAX),
                ),
            ),
            None => None,
        };
        let lease = ComputationTargetLease {
            target_id: target_id.clone(),
            owner,
            scope: request.scope,
            tickers,
            capabilities,
            timeframes,
            expires_at,
            updated_at: now,
        };
        let mut state = self
            .inner
            .write()
            .expect("computation target lock poisoned");
        prune_expired(&mut state.targets, now);
        state.targets.insert(target_id, lease.clone());
        Ok(lease)
    }

    pub fn remove(&self, target_id: &str) -> bool {
        self.inner
            .write()
            .expect("computation target lock poisoned")
            .targets
            .remove(target_id.trim())
            .is_some()
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
        for target in &targets {
            for ticker in &target.tickers {
                *symbol_ref_counts.entry(ticker.clone()).or_insert(0) += 1;
            }
            for capability in &target.capabilities {
                *capability_ref_counts.entry(capability.clone()).or_insert(0) += 1;
            }
        }
        ComputationTargetSnapshot {
            as_of: now,
            active_target_count: targets.len(),
            active_symbol_count: symbol_ref_counts.len(),
            targets,
            symbol_ref_counts,
            capability_ref_counts,
        }
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
            ttl_seconds: None,
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
        assert_eq!(snapshot.active_target_count, 2);
        assert_eq!(snapshot.active_symbol_count, 2);
        assert_eq!(snapshot.symbol_ref_counts.get("AAPL"), Some(&2));
        assert!(targets.requires_focused_computation("aapl"));
        assert!(targets.remove("request:chart"));
        assert_eq!(targets.snapshot().symbol_ref_counts.get("AAPL"), Some(&1));
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
}
