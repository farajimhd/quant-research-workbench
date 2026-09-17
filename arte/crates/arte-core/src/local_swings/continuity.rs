//! Verified empty seconds preserve candle sequence, not elapsed-time sequence.
use super::*;
use crate::{acquisition::trade_seconds::EmptySpan, coverage::Interval, event_order::Scope};
use chrono::{DateTime, Datelike, Utc};
const SECOND: u64 = 1_000_000_000;
const MAX_GAPS: usize = 43_200;
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Gap {
    pub interval: Interval,
    pub evidence_hash: String,
}
fn date(ns: u64) -> Result<u32> {
    let t = DateTime::<Utc>::from_timestamp((ns / SECOND) as i64, (ns % SECOND) as u32)
        .ok_or_else(|| Error::Invalid("swing continuity timestamp".into()))?
        .with_timezone(&chrono_tz::America::New_York);
    Ok(t.year() as u32 * 10000 + t.month() * 100 + t.day())
}
impl State {
    /// `as_of_ns` is the factual source-knowledge cutoff. Historical callers must
    /// separately validate their modeled availability before invoking this path.
    pub fn observe_with_empty_span(
        &mut self,
        bar: &Bar,
        scope: Scope,
        proof: &EmptySpan,
        as_of_ns: u64,
    ) -> Result<()> {
        self.snapshot()?;
        let result = self.continue_empty_span(bar, scope, proof, as_of_ns);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn continue_empty_span(
        &mut self,
        bar: &Bar,
        scope: Scope,
        proof: &EmptySpan,
        as_of_ns: u64,
    ) -> Result<()> {
        let previous = self.previous.as_ref().ok_or_else(|| {
            Error::Unready("empty-span continuity requires previous candle".into())
        })?;
        let interval = Interval {
            start: previous.end_ns,
            end: bar.start_ns,
        };
        interval.validate()?;
        proof.require(scope, interval, as_of_ns)?;
        if scope.instrument != self.instrument
            || scope.session != self.session
            || interval.end - interval.start > 30 * SECOND
            || date(previous.start_ns)? != self.session
            || date(bar.end_ns.saturating_sub(1))? != self.session
            || self.gaps.len() >= MAX_GAPS
        {
            return Err(Error::Conflict(
                "local swing empty-span scope or duration".into(),
            ));
        }
        self.gaps.push(Gap {
            interval,
            evidence_hash: proof.fingerprint().into(),
        });
        self.update(bar, true)
    }
    pub(super) fn gap_duration(&self, last_end: u64) -> Result<u64> {
        if self.gaps.len() > MAX_GAPS {
            return Err(Error::Capacity("local swing recovery gap count".into()));
        }
        let mut total = 0u64;
        let mut prior_end = 0;
        for gap in &self.gaps {
            let i = gap.interval;
            i.validate()?;
            if i.start < self.generation
                || i.end >= last_end
                || (prior_end != 0 && i.start <= prior_end)
                || !i.start.is_multiple_of(SECOND)
                || !i.end.is_multiple_of(SECOND)
                || i.end - i.start > 30 * SECOND
                || date(i.start.saturating_sub(1))? != self.session
                || date(i.end)? != self.session
                || gap.evidence_hash.len() != 64
                || !gap
                    .evidence_hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return Err(Error::Invalid("local swing recovery gap evidence".into()));
            }
            total = total
                .checked_add(i.end - i.start)
                .ok_or_else(|| Error::Capacity("local swing gap duration".into()))?;
            prior_end = i.end;
        }
        Ok(total)
    }
}
