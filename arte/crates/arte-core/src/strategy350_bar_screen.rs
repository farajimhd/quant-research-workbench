//! Conservative columnar 100 ms Strategy 350 screening. True means only that
//! event/quote refinement is required; it never means an entry is permitted.
use crate::{
    bar_catalogue::{Column, Complete, BASE_INTERVAL_NS},
    content_hash,
    events::Decimal,
    execution_interval::ExecutionInterval,
    strategy350_price_gate::PriceFact,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const VERSION: &str = "arte.strategy-350-bar-screen.v2";
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub prior_close_source_hash: String,
    pub prior_close_max: Decimal,
    pub purchase_min: Decimal,
    pub late_gain_bps: u32,
    pub hod_floor_bps: u32,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        self.execution_interval.validate()?;
        if self.execution_interval != ExecutionInterval::Fixed(BASE_INTERVAL_NS)
            || self.prior_close_source_hash.len() != 64
            || !self
                .prior_close_source_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || !self.prior_close_max.positive()
            || !self.purchase_min.positive()
            || self.late_gain_bps == 0
            || self.hod_floor_bps == 0
            || self.hod_floor_bps >= 10_000
        {
            return Err(Error::Invalid(
                "Strategy 350 bar screen configuration".into(),
            ));
        }
        content_hash(&(VERSION, self))
    }
}
pub struct ScreenBatch {
    pub instrument: u64,
    pub first_start_ns: u64,
    /// One flag per 100 ms bucket; false for certified empty buckets.
    pub needs_refinement: Vec<bool>,
    pub late_before: Vec<bool>,
    /// Prior completed-bar HOD; zero only before the first nonempty bucket.
    pub prior_high_atoms: Vec<i64>,
}
#[derive(Default)]
struct State {
    open: i64,
    high: i64,
    late: bool,
}

pub fn project(
    product: &Complete,
    config: &Config,
    prior_closes: &BTreeMap<u64, PriceFact>,
) -> Result<Vec<ScreenBatch>> {
    config.hash()?;
    let request = product.request();
    if request.timeframe_ns != BASE_INTERVAL_NS
        || ![Column::Open, Column::High, Column::Low]
            .into_iter()
            .all(|column| request.columns.contains(&column))
    {
        return Err(Error::Unready(
            "Strategy 350 100 ms OHLC screen product".into(),
        ));
    }
    let mut state = BTreeMap::<u64, State>::new();
    let mut result = Vec::with_capacity(product.batches().len());
    for batch in product.batches() {
        let fact = prior_closes
            .get(&batch.instrument)
            .ok_or_else(|| Error::Unready("Strategy 350 prior close missing".into()))?;
        if fact.source_hash != config.prior_close_source_hash
            || fact.available_at_ns == 0
            || fact.available_at_ns > request.interval.start
        {
            return Err(Error::Unready(
                "Strategy 350 prior close not causally pinned".into(),
            ));
        }
        let prior_close = fact.value.atoms_at_scale(batch.price_scale)?;
        let ceiling = config.prior_close_max.atoms_at_scale(batch.price_scale)?;
        let floor = config.purchase_min.atoms_at_scale(batch.price_scale)?;
        if ceiling <= floor || floor <= 0 {
            return Err(Error::Invalid(
                "Strategy 350 screen price thresholds".into(),
            ));
        }
        let open = batch
            .open
            .as_ref()
            .ok_or_else(|| Error::Unready("screen open column".into()))?;
        let high = batch
            .high
            .as_ref()
            .ok_or_else(|| Error::Unready("screen high column".into()))?;
        let low = batch
            .low
            .as_ref()
            .ok_or_else(|| Error::Unready("screen low column".into()))?;
        let ticker = state.entry(batch.instrument).or_default();
        let mut out = ScreenBatch {
            instrument: batch.instrument,
            first_start_ns: batch.first_start_ns,
            needs_refinement: Vec::with_capacity(batch.count as usize),
            late_before: Vec::with_capacity(batch.count as usize),
            prior_high_atoms: Vec::with_capacity(batch.count as usize),
        };
        // Dense contiguous columns enable bounded batch processing. The prefix
        // max is sequential within a ticker; independent tickers can run in
        // parallel without changing market-time decision order.
        for (((&present, &bar_open), &bar_high), &bar_low) in
            batch.present.iter().zip(open).zip(high).zip(low)
        {
            let prior_high = ticker.high;
            let late_before = ticker.late;
            out.prior_high_atoms.push(prior_high);
            out.late_before.push(late_before);
            let possible = present
                && prior_close > 0
                && prior_close < ceiling
                && bar_high >= floor
                && (!late_before
                    || bar_high > prior_high
                    || (prior_high > 0
                        && bar_low < prior_high
                        && i128::from(bar_high) * 10_000
                            >= i128::from(prior_high) * i128::from(config.hod_floor_bps)));
            out.needs_refinement.push(possible);
            if present {
                if ticker.open == 0 {
                    ticker.open = bar_open;
                }
                ticker.high = ticker.high.max(bar_high);
                ticker.late |= i128::from(ticker.high) * 10_000
                    >= i128::from(ticker.open) * (10_000 + i128::from(config.late_gain_bps));
            }
        }
        result.push(out);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        bar_catalogue::{Batch, Coverage, Readback, Request, Source},
        coverage::Interval,
    };
    use std::collections::BTreeSet;
    const S: u64 = 1_000_000_000;
    fn fixture() -> Complete {
        let interval = Interval {
            start: S,
            end: S + 6 * BASE_INTERVAL_NS,
        };
        let request = Request {
            provider: 1,
            instruments: vec![10],
            session: 20260922,
            interval,
            timeframe_ns: BASE_INTERVAL_NS,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: BTreeSet::from([Column::Open, Column::High, Column::Low]),
            maximum_rows: 6,
        };
        let coverage = Coverage {
            provider: 1,
            session: 20260922,
            interval,
            timeframe_ns: BASE_INTERVAL_NS,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            sources: BTreeMap::from([(
                10,
                Source {
                    certificate_hash: "c".repeat(64),
                    price_scale: 2,
                    size_scale: 2,
                },
            )]),
            published_at_ns: 2 * S,
        };
        let mut readback = Readback::new(request.clone(), &coverage, 2 * S).unwrap();
        readback
            .observe(Batch {
                request_hash: request.hash().unwrap(),
                coverage_hash: coverage.hash().unwrap(),
                instrument: 10,
                first_start_ns: S,
                count: 6,
                price_scale: 2,
                size_scale: 2,
                present: vec![true, true, true, true, true, false],
                open: Some(vec![1000, 1150, 1200, 1050, 700, 0]),
                high: Some(vec![1000, 1150, 1200, 1050, 700, 0]),
                low: Some(vec![1000, 1150, 1200, 1050, 700, 0]),
                close: None,
                volume: None,
                notional: None,
                trades: None,
            })
            .unwrap();
        readback.finish().unwrap()
    }
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Fixed(BASE_INTERVAL_NS),
            prior_close_source_hash: "d".repeat(64),
            prior_close_max: Decimal::parse("20").unwrap(),
            purchase_min: Decimal::parse("1").unwrap(),
            late_gain_bps: 1500,
            hod_floor_bps: 7000,
        }
    }
    fn closes(price: &str) -> BTreeMap<u64, PriceFact> {
        BTreeMap::from([(
            10,
            PriceFact {
                value: Decimal::parse(price).unwrap(),
                available_at_ns: S - 1,
                source_hash: "d".repeat(64),
                source_order: None,
            },
        )])
    }
    #[test]
    fn late_trigger_bucket_is_conservative_and_empty_bucket_is_not_trade() {
        let screen = project(&fixture(), &config(), &closes("19.99")).unwrap();
        assert_eq!(screen.len(), 1);
        assert_eq!(
            screen[0].needs_refinement,
            vec![true, true, true, true, false, false]
        );
        assert_eq!(
            screen[0].late_before,
            vec![false, false, true, true, true, true]
        );
        assert_eq!(
            screen[0].prior_high_atoms,
            vec![0, 1000, 1150, 1200, 1200, 1200]
        );
    }
    #[test]
    fn missing_or_too_high_prior_close_fails_or_rejects() {
        assert!(project(&fixture(), &config(), &BTreeMap::new()).is_err());
        let screen = project(&fixture(), &config(), &closes("20")).unwrap();
        assert!(screen[0].needs_refinement.iter().all(|selected| !*selected));
        for interval in [
            ExecutionInterval::Events,
            ExecutionInterval::Fixed(200_000_000),
        ] {
            let mut wrong = config();
            wrong.execution_interval = interval;
            assert!(project(&fixture(), &wrong, &closes("19")).is_err());
        }
    }
}
