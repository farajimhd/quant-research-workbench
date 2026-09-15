//! In-process audited feed to ordered market/V7 calculations. No service startup.
use crate::live_decode::AuditedEvent;
use arte_core::{
    events::EventKind, exposure::Check, market::Series, market_structure::Ordered,
    v7_stream::Level, Error, Result,
};
use std::collections::BTreeMap;
/// Both channels must progress. This conservative frontier can delay a quiet
/// instrument. It is an explicit lateness assumption, not provider completeness.
pub struct Lane {
    market: Ordered,
    high: BTreeMap<EventKind, u64>,
    allowed_lateness_ns: u64,
    failed: bool,
}
impl Lane {
    pub fn new(market: Ordered, allowed_lateness_ns: u64) -> Result<Self> {
        if allowed_lateness_ns == 0 || allowed_lateness_ns > 1_000_000_000 {
            return Err(Error::Invalid(
                "live ordering allowance must be in (0, 1 second]".into(),
            ));
        }
        Ok(Self {
            market,
            high: BTreeMap::new(),
            allowed_lateness_ns,
            failed: false,
        })
    }
    fn available(&self) -> Result<()> {
        if self.failed {
            return Err(Error::Unready("live market lane requires recovery".into()));
        }
        Ok(())
    }
    /// Persist/audit every observation independently, including duplicate/rejected
    /// input. Eligibility comes from the pinned trade-condition policy, not health.
    pub fn ingest(&mut self, event: &AuditedEvent, eligible: bool) -> Result<()> {
        self.available()?;
        let result = (|| {
            let observation = &event.observation;
            observation.validate()?;
            let scope = self.market.scope();
            if observation.key.provider != scope.provider
                || observation.key.instrument != scope.instrument
                || observation.key.session != scope.session
                || observation.receipt.is_none()
            {
                return Err(Error::Conflict(
                    "live market source scope or receipt missing".into(),
                ));
            }
            if observation.key.kind == EventKind::Trade {
                self.market.enqueue(observation, eligible)?;
            }
            if event.exposure_permitted {
                self.high
                    .entry(observation.key.kind)
                    .and_modify(|at| *at = (*at).max(observation.sip.ns))
                    .or_insert(observation.sip.ns);
            }
            Ok(())
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    /// Borrow the current shared feed gate at release time. No cached permission.
    pub fn advance(&mut self, check: Check<'_>, processed_at_ns: u64) -> Result<usize> {
        self.available()?;
        check.require(self.market.scope().instrument)?;
        let next = frontier(
            &self.high,
            self.allowed_lateness_ns,
            self.market.watermark_ns(),
        )?;
        let result = self.market.advance(next, processed_at_ns);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    pub fn transport_lost(&mut self) {
        self.high.clear();
        self.failed = true;
    }
    pub fn market(&self) -> Result<&Series> {
        self.available()?;
        self.market.market()
    }
    pub fn levels(&self) -> Result<impl Iterator<Item = &Level>> {
        self.available()?;
        self.market.levels()
    }
}
fn frontier(high: &BTreeMap<EventKind, u64>, allowance: u64, previous: u64) -> Result<u64> {
    let trade = high
        .get(&EventKind::Trade)
        .ok_or_else(|| Error::Unready("live trade frontier missing".into()))?;
    let quote = high
        .get(&EventKind::Quote)
        .ok_or_else(|| Error::Unready("live quote frontier missing".into()))?;
    let next = (*trade)
        .min(*quote)
        .checked_sub(allowance)
        .ok_or_else(|| Error::Invalid("live frontier precedes epoch".into()))?;
    Ok(previous.max(next))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn frontier_needs_both_channels_and_never_advances_from_silence() {
        let mut high = BTreeMap::new();
        high.insert(EventKind::Trade, 100);
        assert!(frontier(&high, 10, 0).is_err());
        high.insert(EventKind::Quote, 120);
        assert_eq!(frontier(&high, 10, 0).unwrap(), 90);
        assert_eq!(frontier(&high, 10, 95).unwrap(), 95);
        high.insert(EventKind::Trade, 150);
        assert_eq!(frontier(&high, 10, 95).unwrap(), 110);
    }
}
