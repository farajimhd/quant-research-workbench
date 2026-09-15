use crate::latency::LatencyPolicy;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareProfile {
    pub name: String,
    pub approved: bool,
    pub live_workers: usize,
    pub backtest_workers: usize,
    pub maintenance_workers: usize,
    pub live_memory_bytes: u64,
    pub maintenance_memory_bytes: u64,
    pub backtest_memory_bytes: u64,
    pub reserve_memory_bytes: u64,
    pub event_capacity: usize,
    pub latency: LatencyPolicy,
}
impl HardwareProfile {
    pub fn validate(
        &self,
        cpu_count: usize,
        total_memory_bytes: u64,
        requested_live_bytes: u64,
    ) -> Result<()> {
        self.latency.validate()?;
        if !matches!(self.name.as_str(), "laptop" | "workstation")
            || self.live_workers == 0
            || self.backtest_workers == 0
            || self.maintenance_workers == 0
            || self.event_capacity == 0
        {
            return Err(Error::Invalid("invalid hardware profile".into()));
        }
        let workers = self
            .live_workers
            .checked_add(self.backtest_workers)
            .and_then(|n| n.checked_add(self.maintenance_workers))
            .ok_or_else(|| Error::Invalid("worker budget overflow".into()))?;
        let memory = [
            self.live_memory_bytes,
            self.maintenance_memory_bytes,
            self.backtest_memory_bytes,
            self.reserve_memory_bytes,
        ]
        .iter()
        .try_fold(0_u64, |a, b| a.checked_add(*b))
        .ok_or_else(|| Error::Invalid("memory budget overflow".into()))?;
        if workers > cpu_count
            || memory > total_memory_bytes
            || requested_live_bytes > self.live_memory_bytes
        {
            return Err(Error::Capacity(
                "profile exceeds device or live working set".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum Acceptance {
    RepositoryExtracted,
    SourceIdentity,
    EventStorage,
    V7Parity,
    StrategyParity,
    BrokerProtection,
    Durability,
    ResourceBudgets,
    LatencyThresholds,
}
pub fn live_blockers(profile: &HardwareProfile, passed: &BTreeSet<Acceptance>) -> Vec<String> {
    let mut blockers: Vec<_> = [
        Acceptance::RepositoryExtracted,
        Acceptance::SourceIdentity,
        Acceptance::EventStorage,
        Acceptance::V7Parity,
        Acceptance::StrategyParity,
        Acceptance::BrokerProtection,
        Acceptance::Durability,
        Acceptance::ResourceBudgets,
        Acceptance::LatencyThresholds,
    ]
    .into_iter()
    .filter(|g| !passed.contains(g))
    .map(|g| format!("acceptance missing: {g:?}"))
    .collect();
    if !profile.approved {
        blockers.push("hardware profile not approved".into());
    }
    blockers
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn unapproved_profile_cannot_arm() {
        let p = HardwareProfile {
            name: "laptop".into(),
            approved: false,
            live_workers: 1,
            backtest_workers: 1,
            maintenance_workers: 1,
            live_memory_bytes: 10,
            maintenance_memory_bytes: 10,
            backtest_memory_bytes: 10,
            reserve_memory_bytes: 10,
            event_capacity: 100,
            latency: LatencyPolicy {
                warn_ns: 1,
                block_ns: 2,
                max_clock_uncertainty_ns: 1,
                recovery_samples: 1,
                repeat_ns: 10,
            },
        };
        assert!(!live_blockers(&p, &BTreeSet::new()).is_empty());
        assert!(p.validate(2, 40, 10).is_err());
    }
}
