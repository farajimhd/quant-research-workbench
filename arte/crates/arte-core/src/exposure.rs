//! Market-data prerequisite for exposure increases, rechecked at submission.
use crate::events::EventKind;
use crate::{Error, Result};
use std::collections::BTreeMap;
#[derive(Debug, Clone)]
struct Sample {
    at_ns: u64,
    permitted: bool,
}
#[derive(Debug, Clone)]
pub struct Gate {
    transport_ready: bool,
    maximum_age_ns: u64,
    maximum_lanes: usize,
    samples: BTreeMap<(u64, EventKind), Sample>,
}
#[derive(Debug, Clone, Copy)]
pub struct Check<'a> {
    gate: &'a Gate,
    now_ns: u64,
}
impl Gate {
    pub fn new(maximum_age_ns: u64, maximum_lanes: usize) -> Result<Self> {
        if maximum_age_ns == 0 || maximum_lanes == 0 {
            return Err(Error::Invalid("invalid exposure gate budget".into()));
        }
        Ok(Self {
            transport_ready: false,
            maximum_age_ns,
            maximum_lanes,
            samples: BTreeMap::new(),
        })
    }
    pub fn transport(&mut self, ready: bool) {
        self.transport_ready = ready;
        if !ready {
            self.samples.clear();
        }
    }
    pub fn update(
        &mut self,
        instrument: u64,
        kind: EventKind,
        at_ns: u64,
        permitted: bool,
    ) -> Result<()> {
        if instrument == 0 {
            return Err(Error::Invalid("exposure instrument is missing".into()));
        }
        let key = (instrument, kind);
        if let Some(previous) = self.samples.get(&key) {
            if at_ns < previous.at_ns {
                return Err(Error::Invalid("exposure evidence clock rewind".into()));
            }
        } else if self.samples.len() >= self.maximum_lanes {
            return Err(Error::Capacity("exposure lane budget exceeded".into()));
        }
        self.samples.insert(key, Sample { at_ns, permitted });
        Ok(())
    }
    pub fn at(&self, now_monotonic_ns: u64) -> Check<'_> {
        Check {
            gate: self,
            now_ns: now_monotonic_ns,
        }
    }
}
impl Check<'_> {
    pub fn require(&self, instrument: u64) -> Result<()> {
        if !self.gate.transport_ready {
            return Err(Error::Unready("market transport not ready".into()));
        }
        for kind in [EventKind::Trade, EventKind::Quote] {
            let sample = self
                .gate
                .samples
                .get(&(instrument, kind))
                .ok_or_else(|| Error::Unready("trade or quote readiness missing".into()))?;
            if !sample.permitted
                || self.now_ns < sample.at_ns
                || self.now_ns - sample.at_ns >= self.gate.maximum_age_ns
            {
                return Err(Error::Unready(
                    "trade or quote readiness stale or blocked".into(),
                ));
            }
        }
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn needs_both_channels_and_reconnect_requires_new_evidence() {
        let mut g = Gate::new(100, 4).unwrap();
        g.transport(true);
        g.update(1, EventKind::Trade, 1, true).unwrap();
        assert!(g.at(1).require(1).is_err());
        g.update(1, EventKind::Quote, 1, true).unwrap();
        g.at(100).require(1).unwrap();
        assert!(g.at(101).require(1).is_err());
        g.transport(false);
        g.transport(true);
        assert!(g.at(101).require(1).is_err());
    }
}
