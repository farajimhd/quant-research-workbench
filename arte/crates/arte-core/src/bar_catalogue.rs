//! Completed compact bar batches for a pinned historical calculation generation.
//! Columnar selection and validation happen before a batch enters the replay clock.
use crate::{content_hash, coverage::Interval, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub const CONTRACT: &str = "arte.compact-bars.v1";
pub const BASE_INTERVAL_NS: u64 = 100_000_000;

fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Column {
    Open,
    High,
    Low,
    Close,
    Volume,
    Notional,
    Trades,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub provider: u16,
    pub instruments: Vec<u64>,
    pub session: u32,
    pub interval: Interval,
    pub timeframe_ns: u64,
    pub source_generation: String,
    pub calculation_hash: String,
    pub columns: BTreeSet<Column>,
    pub maximum_rows: usize,
}

/// Published only after the source certificates and derived bucket grid have
/// passed independent readback. Publication is the adapter's responsibility.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Coverage {
    pub provider: u16,
    pub session: u32,
    pub interval: Interval,
    pub timeframe_ns: u64,
    pub source_generation: String,
    pub calculation_hash: String,
    pub sources: BTreeMap<u64, Source>,
    pub published_at_ns: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub certificate_hash: String,
    pub price_scale: u8,
    pub size_scale: u8,
}

impl Coverage {
    pub fn hash(&self) -> Result<String> {
        self.interval.validate()?;
        if self.provider == 0
            || self.session == 0
            || self.timeframe_ns < BASE_INTERVAL_NS
            || !self.timeframe_ns.is_multiple_of(BASE_INTERVAL_NS)
            || !self.interval.start.is_multiple_of(self.timeframe_ns)
            || !self.interval.end.is_multiple_of(self.timeframe_ns)
            || !hash_valid(&self.source_generation)
            || !hash_valid(&self.calculation_hash)
            || self.sources.is_empty()
            || self.sources.len() > 100_000
            || self.sources.iter().any(|(instrument, source)| {
                *instrument == 0
                    || !hash_valid(&source.certificate_hash)
                    || source.price_scale > 9
                    || source.size_scale > 9
            })
            || self.published_at_ns == 0
        {
            return Err(Error::Invalid("compact bar coverage manifest".into()));
        }
        content_hash(&(CONTRACT, "coverage", self))
    }
    pub fn require(&self, request: &Request, source_as_of_ns: u64) -> Result<String> {
        request.validate()?;
        let hash = self.hash()?;
        if self.provider != request.provider
            || self.session != request.session
            || self.timeframe_ns != request.timeframe_ns
            || self.source_generation != request.source_generation
            || self.calculation_hash != request.calculation_hash
            || self.interval.start > request.interval.start
            || self.interval.end < request.interval.end
            || self.published_at_ns > source_as_of_ns
            || request
                .instruments
                .iter()
                .any(|id| !self.sources.contains_key(id))
        {
            return Err(Error::Unready(
                "compact bar coverage does not satisfy request".into(),
            ));
        }
        Ok(hash)
    }
}

impl Request {
    pub fn validate(&self) -> Result<()> {
        self.interval.validate()?;
        if self.provider == 0
            || self.session == 0
            || self.instruments.is_empty()
            || self.instruments.len() > 100_000
            || self.instruments.contains(&0)
            || self.instruments.windows(2).any(|w| w[0] >= w[1])
            || self.timeframe_ns < BASE_INTERVAL_NS
            || !self.timeframe_ns.is_multiple_of(BASE_INTERVAL_NS)
            || !self.interval.start.is_multiple_of(self.timeframe_ns)
            || !self.interval.end.is_multiple_of(self.timeframe_ns)
            || !hash_valid(&self.source_generation)
            || !hash_valid(&self.calculation_hash)
            || self.columns.is_empty()
            || self.maximum_rows == 0
            || self.maximum_rows > 2_000_000
        {
            return Err(Error::Invalid("compact bar request".into()));
        }
        let buckets = (self.interval.end - self.interval.start) / self.timeframe_ns;
        if buckets == 0
            || buckets > self.maximum_rows as u64
            || self.instruments.len() as u64 > self.maximum_rows as u64 / buckets
        {
            return Err(Error::Capacity("compact bar request rows".into()));
        }
        Ok(())
    }
    pub fn hash(&self) -> Result<String> {
        self.validate()?;
        content_hash(&(CONTRACT, self))
    }
}

/// One bounded, dense bucket grid. `present=false` means certified no eligible
/// trade; it does not license a fabricated price. Missing source coverage is an
/// error before this batch can be published.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Batch {
    pub request_hash: String,
    pub coverage_hash: String,
    pub instrument: u64,
    pub first_start_ns: u64,
    pub count: u32,
    pub price_scale: u8,
    pub size_scale: u8,
    pub present: Vec<bool>,
    pub open: Option<Vec<i64>>,
    pub high: Option<Vec<i64>>,
    pub low: Option<Vec<i64>>,
    pub close: Option<Vec<i64>>,
    pub volume: Option<Vec<i64>>,
    /// Exact price-times-size atoms at the sum of the two scales.
    pub notional: Option<Vec<i128>>,
    pub trades: Option<Vec<u64>>,
}

/// Verifies a complete ordered read before a backtest treats any bar as ready.
/// The catalogue's independently verified coverage hash is required; a row's
/// self-reported hash alone does not certify source completeness.
pub struct Readback {
    request: Request,
    coverage_hash: String,
    sources: BTreeMap<u64, Source>,
    next_instrument: usize,
    next_start_ns: u64,
    rows: usize,
    batches: Vec<Batch>,
    failed: bool,
}

pub struct Complete {
    pub request_hash: String,
    pub coverage_hash: String,
    pub batches: Vec<Batch>,
}

impl Readback {
    pub fn new(request: Request, coverage: &Coverage, source_as_of_ns: u64) -> Result<Self> {
        let coverage_hash = coverage.require(&request, source_as_of_ns)?;
        Ok(Self {
            next_start_ns: request.interval.start,
            request,
            coverage_hash,
            sources: coverage.sources.clone(),
            next_instrument: 0,
            rows: 0,
            batches: Vec::new(),
            failed: false,
        })
    }
    pub fn observe(&mut self, batch: Batch) -> Result<()> {
        if self.failed {
            return Err(Error::Unready("compact bar readback failed".into()));
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
        batch.validate(&self.request)?;
        if batch.coverage_hash != self.coverage_hash {
            return Err(Error::Conflict("compact bar coverage differs".into()));
        }
        if self.next_start_ns == self.request.interval.end {
            self.next_instrument += 1;
            self.next_start_ns = self.request.interval.start;
        }
        if self.request.instruments.get(self.next_instrument) != Some(&batch.instrument)
            || batch.first_start_ns != self.next_start_ns
            || self.sources.get(&batch.instrument).is_none_or(|source| {
                source.price_scale != batch.price_scale || source.size_scale != batch.size_scale
            })
        {
            return Err(Error::Conflict("compact bar readback gap or order".into()));
        }
        self.rows = self
            .rows
            .checked_add(batch.count as usize)
            .ok_or_else(|| Error::Capacity("compact bar readback rows".into()))?;
        if self.rows > self.request.maximum_rows {
            return Err(Error::Capacity("compact bar readback rows".into()));
        }
        self.next_start_ns = batch
            .first_start_ns
            .checked_add(batch.count as u64 * self.request.timeframe_ns)
            .ok_or_else(|| Error::Capacity("compact bar readback clock".into()))?;
        Ok(())
    }
    pub fn finish(mut self) -> Result<Complete> {
        if self.failed {
            return Err(Error::Unready("compact bar readback failed".into()));
        }
        if self.next_start_ns == self.request.interval.end {
            self.next_instrument += 1;
        }
        if self.next_instrument != self.request.instruments.len() {
            return Err(Error::Unready("compact bar readback incomplete".into()));
        }
        Ok(Complete {
            request_hash: self.request.hash()?,
            coverage_hash: self.coverage_hash,
            batches: self.batches,
        })
    }
}

impl Batch {
    pub fn validate(&self, request: &Request) -> Result<()> {
        request.validate()?;
        if self.request_hash != request.hash()?
            || !hash_valid(&self.coverage_hash)
            || request.instruments.binary_search(&self.instrument).is_err()
            || self.count == 0
            || self.count as usize > request.maximum_rows
            || self.present.len() != self.count as usize
            || self.price_scale > 9
            || self.size_scale > 9
            || self.first_start_ns < request.interval.start
            || !self.first_start_ns.is_multiple_of(request.timeframe_ns)
            || !(self.first_start_ns - request.interval.start).is_multiple_of(request.timeframe_ns)
            || (self.count as u64)
                .checked_mul(request.timeframe_ns)
                .and_then(|duration| self.first_start_ns.checked_add(duration))
                .is_none_or(|end| end > request.interval.end)
        {
            return Err(Error::Conflict(
                "compact bar batch identity or extent".into(),
            ));
        }
        let n = self.count as usize;
        let fields = [
            (Column::Open, self.open.as_deref()),
            (Column::High, self.high.as_deref()),
            (Column::Low, self.low.as_deref()),
            (Column::Close, self.close.as_deref()),
            (Column::Volume, self.volume.as_deref()),
        ];
        for (column, values) in fields {
            if request.columns.contains(&column) != values.is_some()
                || values.is_some_and(|v| v.len() != n)
            {
                return Err(Error::Invalid(
                    "compact bar column selection or length".into(),
                ));
            }
            if let Some(values) = values {
                for (i, &value) in values.iter().enumerate() {
                    if (self.present[i]
                        && match column {
                            Column::Open | Column::High | Column::Low | Column::Close => value <= 0,
                            _ => value < 0,
                        })
                        || (!self.present[i] && value != 0)
                    {
                        return Err(Error::Invalid("compact bar value or empty bucket".into()));
                    }
                }
            }
        }
        if request.columns.contains(&Column::Notional) != self.notional.is_some()
            || self.notional.as_ref().is_some_and(|v| v.len() != n)
            || self.notional.as_ref().is_some_and(|values| {
                values
                    .iter()
                    .enumerate()
                    .any(|(i, &v)| (self.present[i] && v < 0) || (!self.present[i] && v != 0))
            })
        {
            return Err(Error::Invalid(
                "compact bar notional selection or value".into(),
            ));
        }
        if request.columns.contains(&Column::Trades) != self.trades.is_some()
            || self.trades.as_ref().is_some_and(|v| v.len() != n)
        {
            return Err(Error::Invalid(
                "compact bar trades selection or length".into(),
            ));
        }
        if let Some(trades) = &self.trades {
            for (i, &count) in trades.iter().enumerate() {
                if self.present[i] == (count == 0) {
                    return Err(Error::Invalid("compact bar trade presence".into()));
                }
            }
        }
        if let (Some(open), Some(high), Some(low), Some(close)) =
            (&self.open, &self.high, &self.low, &self.close)
        {
            for i in 0..n {
                if self.present[i]
                    && (low[i] > open[i].min(close[i]) || high[i] < open[i].max(close[i]))
                {
                    return Err(Error::Invalid("compact bar OHLC geometry".into()));
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn request() -> Request {
        Request {
            provider: 1,
            instruments: vec![10, 20],
            session: 20260922,
            interval: Interval {
                start: 1_000_000_000,
                end: 2_000_000_000,
            },
            timeframe_ns: BASE_INTERVAL_NS,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: [
                Column::Open,
                Column::High,
                Column::Low,
                Column::Close,
                Column::Volume,
                Column::Trades,
            ]
            .into(),
            maximum_rows: 20,
        }
    }
    fn coverage(request: &Request) -> Coverage {
        Coverage {
            provider: request.provider,
            session: request.session,
            interval: request.interval,
            timeframe_ns: request.timeframe_ns,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            sources: [
                (
                    10,
                    Source {
                        certificate_hash: "d".repeat(64),
                        price_scale: 2,
                        size_scale: 2,
                    },
                ),
                (
                    20,
                    Source {
                        certificate_hash: "e".repeat(64),
                        price_scale: 2,
                        size_scale: 2,
                    },
                ),
            ]
            .into(),
            published_at_ns: 3_000_000_000,
        }
    }
    fn batch(request: &Request) -> Batch {
        Batch {
            request_hash: request.hash().unwrap(),
            coverage_hash: coverage(request).hash().unwrap(),
            instrument: 10,
            first_start_ns: 1_000_000_000,
            count: 2,
            price_scale: 2,
            size_scale: 2,
            present: vec![true, false],
            open: Some(vec![1000, 0]),
            high: Some(vec![1100, 0]),
            low: Some(vec![900, 0]),
            close: Some(vec![1050, 0]),
            volume: Some(vec![400, 0]),
            notional: None,
            trades: Some(vec![2, 0]),
        }
    }
    #[test]
    fn complete_selected_columns_and_explicit_empty_bucket() {
        let r = request();
        assert!(batch(&r).validate(&r).is_ok());
        let mut b = batch(&r);
        b.present[1] = true;
        assert!(b.validate(&r).is_err());
        let mut b = batch(&r);
        b.close.as_mut().unwrap()[1] = 1000;
        assert!(b.validate(&r).is_err());
        let mut b = batch(&r);
        b.high.as_mut().unwrap()[0] = 900;
        assert!(b.validate(&r).is_err());
    }
    #[test]
    fn bounds_and_generation_are_required() {
        let mut r = request();
        r.maximum_rows = 19;
        assert!(r.validate().is_err());
        let r = request();
        let mut b = batch(&r);
        b.request_hash = "d".repeat(64);
        assert!(b.validate(&r).is_err());
        let mut b = batch(&r);
        b.first_start_ns += 1;
        assert!(b.validate(&r).is_err());
    }
    #[test]
    fn exact_scaled_notional_and_scale_bounds() {
        let mut r = request();
        r.columns.insert(Column::Notional);
        let mut b = batch(&r);
        b.request_hash = r.hash().unwrap();
        b.notional = Some(vec![40_000, 0]);
        assert!(b.validate(&r).is_ok());
        b.price_scale = 10;
        assert!(b.validate(&r).is_err());
        b.price_scale = 2;
        b.notional.as_mut().unwrap()[1] = 1;
        assert!(b.validate(&r).is_err());
    }
    #[test]
    fn complete_readback_rejects_missing_or_reordered_buckets() {
        let r = request();
        let manifest = coverage(&r);
        let mut incomplete = Readback::new(r.clone(), &manifest, 3_000_000_000).unwrap();
        incomplete.observe(batch(&r)).unwrap();
        assert!(incomplete.finish().is_err());
        let mut readback = Readback::new(r.clone(), &manifest, 3_000_000_000).unwrap();
        let mut one = batch(&r);
        one.count = 10;
        one.present.resize(10, false);
        for values in [
            &mut one.open,
            &mut one.high,
            &mut one.low,
            &mut one.close,
            &mut one.volume,
        ] {
            if let Some(v) = values {
                v.resize(10, 0);
            }
        }
        one.trades.as_mut().unwrap().resize(10, 0);
        readback.observe(one.clone()).unwrap();
        assert!(readback.observe(one).is_err());
        assert!(readback.finish().is_err());
        let mut readback = Readback::new(r.clone(), &manifest, 3_000_000_000).unwrap();
        let mut two = batch(&r);
        two.count = 10;
        two.instrument = 20;
        two.present.resize(10, false);
        for values in [
            &mut two.open,
            &mut two.high,
            &mut two.low,
            &mut two.close,
            &mut two.volume,
        ] {
            if let Some(v) = values {
                v.resize(10, 0);
            }
        }
        two.trades.as_mut().unwrap().resize(10, 0);
        let mut first = two.clone();
        first.instrument = 10;
        readback.observe(first).unwrap();
        readback.observe(two).unwrap();
        assert_eq!(readback.finish().unwrap().batches.len(), 2);
    }
    #[test]
    fn coverage_requires_exact_generation_complete_tickers_and_source_knowledge() {
        let r = request();
        let manifest = coverage(&r);
        assert!(Readback::new(r.clone(), &manifest, 2_999_999_999).is_err());
        let mut missing = manifest.clone();
        missing.sources.remove(&20);
        assert!(Readback::new(r.clone(), &missing, 3_000_000_000).is_err());
        let mut changed = manifest.clone();
        changed.calculation_hash = "f".repeat(64);
        assert!(Readback::new(r, &changed, 3_000_000_000).is_err());
    }
}
