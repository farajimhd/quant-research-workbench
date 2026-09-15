use crate::{Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LatencyPolicy {
    pub warn_ns: u64,
    pub block_ns: u64,
    pub max_clock_uncertainty_ns: u64,
    pub recovery_samples: u32,
    pub repeat_ns: u64,
}
impl LatencyPolicy {
    pub fn validate(&self) -> Result<()> {
        if self.warn_ns == 0
            || self.warn_ns >= self.block_ns
            || self.recovery_samples == 0
            || self.repeat_ns == 0
        {
            Err(Error::Invalid("invalid latency policy".into()))
        } else {
            Ok(())
        }
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Health {
    Healthy,
    Warning,
    ExposureBlocked,
    Recovering,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LatencyMonitor {
    policy: LatencyPolicy,
    pub state: Health,
    good: u32,
    last_alert: Option<u64>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Assessment {
    pub state: Health,
    pub notify: bool,
    pub lower_age_ns: u64,
    pub upper_age_ns: u64,
}
impl LatencyMonitor {
    pub fn new(policy: LatencyPolicy) -> Result<Self> {
        policy.validate()?;
        Ok(Self {
            policy,
            state: Health::ExposureBlocked,
            good: 0,
            last_alert: None,
        })
    }
    pub fn observe(
        &mut self,
        sip_ns: u64,
        receive_ns: u64,
        clock_uncertainty_ns: u64,
        local_queue_ns: u64,
        now_mono: u64,
    ) -> Assessment {
        let age = receive_ns.saturating_sub(sip_ns);
        let upper = age
            .saturating_add(clock_uncertainty_ns)
            .saturating_add(local_queue_ns);
        let bad = clock_uncertainty_ns > self.policy.max_clock_uncertainty_ns
            || sip_ns > receive_ns.saturating_add(clock_uncertainty_ns)
            || upper >= self.policy.block_ns;
        let previous = self.state;
        if bad {
            self.good = 0;
            self.state = Health::ExposureBlocked;
        } else if upper >= self.policy.warn_ns {
            self.good = 0;
            self.state = if matches!(previous, Health::ExposureBlocked | Health::Recovering) {
                Health::Recovering
            } else {
                Health::Warning
            };
        } else {
            self.good = self.good.saturating_add(1);
            self.state = if matches!(previous, Health::ExposureBlocked | Health::Recovering)
                && self.good < self.policy.recovery_samples
            {
                Health::Recovering
            } else {
                Health::Healthy
            };
        }
        let notify = self.state != previous
            || self.state != Health::Healthy
                && self
                    .last_alert
                    .is_none_or(|last| now_mono.saturating_sub(last) >= self.policy.repeat_ns);
        if notify {
            self.last_alert = Some(now_mono);
        }
        Assessment {
            state: self.state,
            notify,
            lower_age_ns: age.saturating_sub(clock_uncertainty_ns),
            upper_age_ns: upper,
        }
    }
    pub fn permits_exposure(&self) -> bool {
        matches!(self.state, Health::Healthy | Health::Warning)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn monitor() -> LatencyMonitor {
        LatencyMonitor::new(LatencyPolicy {
            warn_ns: 100,
            block_ns: 200,
            max_clock_uncertainty_ns: 10,
            recovery_samples: 2,
            repeat_ns: 1000,
        })
        .unwrap()
    }
    #[test]
    fn recovery_hysteresis() {
        let mut m = monitor();
        assert_eq!(m.observe(100, 110, 0, 0, 1).state, Health::Recovering);
        assert_eq!(m.observe(100, 110, 0, 0, 2).state, Health::Healthy);
        assert_eq!(m.observe(100, 500, 0, 0, 3).state, Health::ExposureBlocked);
        assert!(!m.permits_exposure());
    }
    #[test]
    fn repeats_unresolved_incident() {
        let mut m = monitor();
        assert!(m.observe(100, 500, 0, 0, 1).notify);
        assert!(!m.observe(100, 500, 0, 0, 2).notify);
        assert!(m.observe(100, 500, 0, 0, 1001).notify);
    }
}
