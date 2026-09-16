//! Bounded exact occupancy index built only from fully verified trade batches.
//! An empty span means no recorded trades in this certified revision, not an
//! absolute guarantee of provider completeness or a fabricated live watermark.
use super::{Certificate, VerifiedCertificate, Verifier};
use crate::{
    content_hash, coverage::Interval, event_order::Scope, event_storage::Batch, events::EventKind,
    Error, Result,
};
const SECOND: u64 = 1_000_000_000;
pub struct Builder {
    verifier: Verifier,
    scope: Scope,
    interval: Interval,
    certificate_id: String,
    published_at_ns: u64,
    occupied: Vec<bool>,
    failed: bool,
}
pub struct Index {
    scope: Scope,
    interval: Interval,
    certificate_id: String,
    published_at_ns: u64,
    occupied: Vec<bool>,
}
/// Not deserializable or publicly constructible. Persist its fingerprint with
/// the source certificate; rebuild authority from batch verification on restore.
pub struct EmptySpan {
    scope: Scope,
    interval: Interval,
    certificate_id: String,
    published_at_ns: u64,
    fingerprint: String,
}
impl Builder {
    pub fn new(certificate: Certificate, scope: Scope, maximum_seconds: usize) -> Result<Self> {
        certificate.interval.validate()?;
        let seconds = (certificate.interval.end - certificate.interval.start) / SECOND;
        if maximum_seconds == 0
            || maximum_seconds > 172_800
            || seconds == 0
            || seconds > maximum_seconds as u64
            || !certificate.interval.start.is_multiple_of(SECOND)
            || !certificate.interval.end.is_multiple_of(SECOND)
            || certificate.authority.provider != scope.provider
            || certificate.authority.instrument != scope.instrument
            || certificate.authority.kind != EventKind::Trade
            || !(19000101..=29991231).contains(&scope.session)
        {
            return Err(Error::Invalid(
                "trade occupancy scope, alignment or budget".into(),
            ));
        }
        let interval = certificate.interval;
        let certificate_id = certificate.id()?;
        let published_at_ns = certificate.published_at_ns;
        Ok(Self {
            verifier: Verifier::new(certificate)?,
            scope,
            interval,
            certificate_id,
            published_at_ns,
            occupied: vec![false; seconds as usize],
            failed: false,
        })
    }
    pub fn observe(&mut self, batch: &Batch) -> Result<()> {
        if self.failed {
            return Err(Error::Unready("trade occupancy verification failed".into()));
        }
        let result = (|| {
            self.verifier.observe(batch)?;
            for event in batch.observations() {
                if event.key.session != self.scope.session {
                    return Err(Error::Conflict("trade occupancy session differs".into()));
                }
                let second = (event.sip.ns - self.interval.start) / SECOND;
                self.occupied[second as usize] = true;
            }
            Ok(())
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    pub fn finish(self) -> Result<(VerifiedCertificate, Index)> {
        if self.failed {
            return Err(Error::Unready("trade occupancy verification failed".into()));
        }
        let certificate = self.verifier.finish()?;
        Ok((
            certificate,
            Index {
                scope: self.scope,
                interval: self.interval,
                certificate_id: self.certificate_id,
                published_at_ns: self.published_at_ns,
                occupied: self.occupied,
            },
        ))
    }
}
impl Index {
    /// No live-silence inference, eligibility filtering, or retroactive knowledge.
    pub fn prove_empty(&self, interval: Interval, as_of_ns: u64) -> Result<EmptySpan> {
        interval.validate()?;
        if interval.start < self.interval.start
            || interval.end > self.interval.end
            || !interval.start.is_multiple_of(SECOND)
            || !interval.end.is_multiple_of(SECOND)
            || self.published_at_ns > as_of_ns
        {
            return Err(Error::Unready(
                "empty trade interval outside known certified domain".into(),
            ));
        }
        let start = ((interval.start - self.interval.start) / SECOND) as usize;
        let end = ((interval.end - self.interval.start) / SECOND) as usize;
        if self.occupied[start..end].iter().any(|v| *v) {
            return Err(Error::Conflict(
                "requested empty interval contains recorded trades".into(),
            ));
        }
        let fingerprint = content_hash(&(
            "arte.certified-empty-trade-seconds.v1",
            &self.certificate_id,
            (
                self.scope.provider,
                self.scope.instrument,
                self.scope.session,
            ),
            interval,
            self.published_at_ns,
        ))?;
        Ok(EmptySpan {
            scope: self.scope,
            interval,
            certificate_id: self.certificate_id.clone(),
            published_at_ns: self.published_at_ns,
            fingerprint,
        })
    }
}
impl EmptySpan {
    pub fn fingerprint(&self) -> &str {
        &self.fingerprint
    }
    pub fn certificate_id(&self) -> &str {
        &self.certificate_id
    }
    pub fn published_at_ns(&self) -> u64 {
        self.published_at_ns
    }
    pub fn interval(&self) -> Interval {
        self.interval
    }
    pub fn require(&self, scope: Scope, interval: Interval, as_of_ns: u64) -> Result<()> {
        if self.scope != scope || self.interval != interval || self.published_at_ns > as_of_ns {
            return Err(Error::Conflict(
                "empty trade evidence scope, interval or knowledge time differs".into(),
            ));
        }
        Ok(())
    }
}
#[cfg(test)]
mod tests;
