//! Completed Boolean computation batches for signals, Watchlists and rules.
//! Unknown is never interpreted as false or as a trading permission.
use crate::{
    bar_catalogue::BASE_INTERVAL_NS,
    content_hash,
    coverage::Interval,
    execution_interval::{ExecutionContract, ExecutionInterval},
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub const VERSION: &str = "arte.boolean-catalogue.v1";
fn hash_ok(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub interval: Interval,
    pub definition: ExecutionContract,
    pub source_bar_request_hash: String,
    pub source_bar_coverage_hash: String,
    pub maximum_rows: usize,
}
impl Request {
    pub fn validate(&self) -> Result<()> {
        self.interval.validate()?;
        self.definition.hash()?;
        let count = (self.interval.end - self.interval.start) / BASE_INTERVAL_NS;
        if self.provider == 0
            || self.instrument == 0
            || !(19000101..=29991231).contains(&self.session)
            || !self.interval.start.is_multiple_of(BASE_INTERVAL_NS)
            || !self.interval.end.is_multiple_of(BASE_INTERVAL_NS)
            || !hash_ok(&self.source_bar_request_hash)
            || !hash_ok(&self.source_bar_coverage_hash)
            || self.maximum_rows == 0
            || self.maximum_rows > 2_000_000
            || count == 0
            || count > self.maximum_rows as u64
        {
            return Err(Error::Invalid("Boolean product request".into()));
        }
        Ok(())
    }
    pub fn hash(&self) -> Result<String> {
        self.validate()?;
        content_hash(&(VERSION, "request", self))
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Coverage {
    pub request_hash: String,
    pub source_bar_coverage_hash: String,
    pub producer_hash: String,
    pub published_at_ns: u64,
}
impl Coverage {
    pub fn hash(&self) -> Result<String> {
        if !hash_ok(&self.request_hash)
            || !hash_ok(&self.source_bar_coverage_hash)
            || !hash_ok(&self.producer_hash)
            || self.published_at_ns == 0
        {
            return Err(Error::Invalid("Boolean product coverage".into()));
        }
        content_hash(&(VERSION, "coverage", self))
    }
    pub fn require(&self, request: &Request, as_of_ns: u64) -> Result<String> {
        let hash = self.hash()?;
        if self.request_hash != request.hash()?
            || self.source_bar_coverage_hash != request.source_bar_coverage_hash
            || self.producer_hash != request.definition.implementation_hash
            || self.published_at_ns > as_of_ns
        {
            return Err(Error::Unready(
                "Boolean product coverage differs or is unavailable".into(),
            ));
        }
        Ok(hash)
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Batch {
    pub request_hash: String,
    pub coverage_hash: String,
    pub first_start_ns: u64,
    pub count: u32,
    pub evaluated: Vec<bool>,
    pub known: Vec<bool>,
    pub value: Vec<bool>,
}
pub struct Complete {
    request: Request,
    coverage_hash: String,
    batches: Vec<Batch>,
}
impl Complete {
    pub fn request(&self) -> &Request {
        &self.request
    }
    pub fn coverage_hash(&self) -> &str {
        &self.coverage_hash
    }
    pub fn batches(&self) -> &[Batch] {
        &self.batches
    }
}
pub struct Readback {
    request: Request,
    coverage_hash: String,
    next_start_ns: u64,
    state: Option<bool>,
    batches: Vec<Batch>,
    failed: bool,
}
impl Readback {
    pub fn new(request: Request, coverage: &Coverage, as_of_ns: u64) -> Result<Self> {
        let coverage_hash = coverage.require(&request, as_of_ns)?;
        Ok(Self {
            next_start_ns: request.interval.start,
            request,
            coverage_hash,
            state: None,
            batches: Vec::new(),
            failed: false,
        })
    }
    pub fn observe(&mut self, batch: Batch) -> Result<()> {
        if self.failed {
            return Err(Error::Unready(
                "Boolean product readback requires recovery".into(),
            ));
        }
        let result = self.advance(&batch);
        if result.is_err() {
            self.failed = true;
            return result;
        }
        self.batches.push(batch);
        Ok(())
    }
    fn advance(&mut self, batch: &Batch) -> Result<()> {
        let n = batch.count as usize;
        if batch.request_hash != self.request.hash()?
            || batch.coverage_hash != self.coverage_hash
            || batch.first_start_ns != self.next_start_ns
            || n == 0
            || batch.evaluated.len() != n
            || batch.known.len() != n
            || batch.value.len() != n
            || n > self.request.maximum_rows
            || self
                .next_start_ns
                .checked_add(n as u64 * BASE_INTERVAL_NS)
                .is_none_or(|end| end > self.request.interval.end)
        {
            return Err(Error::Conflict("Boolean product batch gap or shape".into()));
        }
        for i in 0..n {
            if batch.value[i] && !batch.known[i] {
                return Err(Error::Conflict("Boolean true without known operand".into()));
            }
            if let ExecutionInterval::Fixed(ns) = self.request.definition.interval {
                let end = batch.first_start_ns + (i as u64 + 1) * BASE_INTERVAL_NS;
                if batch.evaluated[i] != end.is_multiple_of(ns) {
                    return Err(Error::Conflict("Boolean evaluation cadence differs".into()));
                }
            }
            let next = batch.known[i].then_some(batch.value[i]);
            if !batch.evaluated[i] && next != self.state {
                return Err(Error::Conflict(
                    "Boolean value changed without evaluation".into(),
                ));
            }
            self.state = next;
        }
        self.next_start_ns += n as u64 * BASE_INTERVAL_NS;
        Ok(())
    }
    pub fn finish(self) -> Result<Complete> {
        if self.failed || self.next_start_ns != self.request.interval.end {
            return Err(Error::Unready("Boolean product readback incomplete".into()));
        }
        Ok(Complete {
            request: self.request,
            coverage_hash: self.coverage_hash,
            batches: self.batches,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::execution_interval::ExecutableKind;
    fn request() -> Request {
        Request {
            provider: 1,
            instrument: 10,
            session: 20260922,
            interval: Interval {
                start: 1_000_000_000,
                end: 1_400_000_000,
            },
            definition: ExecutionContract {
                kind: ExecutableKind::SignalStream,
                id: "squeeze".into(),
                implementation_hash: "a".repeat(64),
                interval: ExecutionInterval::Fixed(200_000_000),
            },
            source_bar_request_hash: "b".repeat(64),
            source_bar_coverage_hash: "c".repeat(64),
            maximum_rows: 4,
        }
    }
    fn coverage(r: &Request) -> Coverage {
        Coverage {
            request_hash: r.hash().unwrap(),
            source_bar_coverage_hash: r.source_bar_coverage_hash.clone(),
            producer_hash: r.definition.implementation_hash.clone(),
            published_at_ns: 2_000_000_000,
        }
    }
    fn batch(r: &Request, c: &Coverage) -> Batch {
        Batch {
            request_hash: r.hash().unwrap(),
            coverage_hash: c.hash().unwrap(),
            first_start_ns: r.interval.start,
            count: 4,
            evaluated: vec![false, true, false, true],
            known: vec![false, true, true, true],
            value: vec![false, true, true, false],
        }
    }
    #[test]
    fn fixed_cadence_carries_known_state_across_unevaluated_bucket() {
        let r = request();
        let c = coverage(&r);
        let b = batch(&r, &c);
        let mut readback = Readback::new(r, &c, 2_000_000_000).unwrap();
        readback.observe(b).unwrap();
        let complete = readback.finish().unwrap();
        assert_eq!(complete.batches()[0].known, vec![false, true, true, true]);
        assert_eq!(complete.batches()[0].value, vec![false, true, true, false]);
    }
    #[test]
    fn unknown_is_not_false_and_cadence_cannot_shift() {
        let r = request();
        let c = coverage(&r);
        let mut wrong = batch(&r, &c);
        wrong.value[0] = true;
        let mut readback = Readback::new(r, &c, 2_000_000_000).unwrap();
        assert!(readback.observe(wrong).is_err());
        assert!(readback.finish().is_err());
        let r = request();
        let c = coverage(&r);
        let mut wrong = batch(&r, &c);
        wrong.evaluated[0] = true;
        assert!(Readback::new(r, &c, 2_000_000_000)
            .unwrap()
            .observe(wrong)
            .is_err());
    }
    #[test]
    fn incomplete_or_future_coverage_never_becomes_complete() {
        let r = request();
        let c = coverage(&r);
        assert!(Readback::new(request(), &c, c.published_at_ns - 1).is_err());
        let mut partial = batch(&r, &c);
        partial.count = 2;
        partial.evaluated.truncate(2);
        partial.known.truncate(2);
        partial.value.truncate(2);
        let mut readback = Readback::new(r, &c, c.published_at_ns).unwrap();
        readback.observe(partial).unwrap();
        assert!(readback.finish().is_err());
    }
}
