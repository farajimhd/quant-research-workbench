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
impl State {
    fn observe(
        &mut self,
        bucket: Option<(i64, i64, i64)>,
        thresholds: Thresholds,
    ) -> (bool, bool, i64) {
        let prior_high = self.high;
        let late_before = self.late;
        let possible = bucket.is_some_and(|(_, high, low)| {
            thresholds.prior_close > 0
                && thresholds.prior_close < thresholds.ceiling
                && high >= thresholds.floor
                && (!late_before
                    || high > prior_high
                    || (prior_high > 0
                        && low < prior_high
                        && i128::from(high) * 10_000
                            >= i128::from(prior_high) * i128::from(thresholds.hod_floor_bps)))
        });
        if let Some((open, high, _)) = bucket {
            if self.open == 0 {
                self.open = open;
            }
            self.high = self.high.max(high);
            self.late |= i128::from(self.high) * 10_000
                >= i128::from(self.open) * (10_000 + i128::from(thresholds.late_gain_bps));
        }
        (possible, late_before, prior_high)
    }
}
#[derive(Clone, Copy)]
struct Thresholds {
    prior_close: i64,
    ceiling: i64,
    floor: i64,
    late_gain_bps: u32,
    hod_floor_bps: u32,
}
fn thresholds(
    config: &Config,
    fact: &PriceFact,
    price_scale: u8,
    start_ns: u64,
) -> Result<Thresholds> {
    if fact.source_hash != config.prior_close_source_hash
        || fact.available_at_ns == 0
        || fact.available_at_ns > start_ns
    {
        return Err(Error::Unready(
            "Strategy 350 prior close not causally pinned".into(),
        ));
    }
    let prior_close = fact.value.atoms_at_scale(price_scale)?;
    let ceiling = config.prior_close_max.atoms_at_scale(price_scale)?;
    let floor = config.purchase_min.atoms_at_scale(price_scale)?;
    if ceiling <= floor || floor <= 0 {
        return Err(Error::Invalid(
            "Strategy 350 screen price thresholds".into(),
        ));
    }
    Ok(Thresholds {
        prior_close,
        ceiling,
        floor,
        late_gain_bps: config.late_gain_bps,
        hod_floor_bps: config.hod_floor_bps,
    })
}

/// One completed 100 ms boundary. A selected bucket still requires exact
/// event/quote refinement and is never an entry authorization.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ScreenPoint {
    pub scope: crate::event_order::Scope,
    pub start_ns: u64,
    pub source: crate::exact_bars::Mode,
    pub needs_refinement: bool,
    pub late_before: bool,
    pub prior_high_atoms: i64,
}

/// Live and historical bars use the same price-state transition as the
/// columnar batch projector. This state has no database or broker capability.
pub struct StreamingScreen {
    scope: crate::event_order::Scope,
    next_start_ns: u64,
    price_scale: u8,
    source: crate::exact_bars::Mode,
    thresholds: Thresholds,
    state: State,
}
impl StreamingScreen {
    pub fn new(
        scope: crate::event_order::Scope,
        session_start_ns: u64,
        price_scale: u8,
        source: crate::exact_bars::Mode,
        config: &Config,
        expected_config_hash: &str,
        prior_close: &PriceFact,
    ) -> Result<Self> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || session_start_ns == 0
            || !session_start_ns.is_multiple_of(BASE_INTERVAL_NS)
            || price_scale > 9
            || config.hash()? != expected_config_hash
        {
            return Err(Error::Invalid(
                "Strategy 350 streaming screen identity".into(),
            ));
        }
        Ok(Self {
            scope,
            next_start_ns: session_start_ns,
            price_scale,
            source,
            thresholds: thresholds(config, prior_close, price_scale, session_start_ns)?,
            state: State::default(),
        })
    }
    pub fn observe(
        &mut self,
        start_ns: u64,
        bar: Option<&crate::exact_bars::Bar>,
    ) -> Result<ScreenPoint> {
        if start_ns != self.next_start_ns {
            return Err(Error::Conflict(
                "Strategy 350 screen bucket gap or repeat".into(),
            ));
        }
        let bucket = if let Some(bar) = bar {
            if bar.start_ns != start_ns
                || bar.end_ns.checked_sub(bar.start_ns) != Some(BASE_INTERVAL_NS)
                || bar.price_scale != self.price_scale
                || (self.source == crate::exact_bars::Mode::Live)
                    != bar.last_trade_live_receipt_ns.is_some()
                || bar.open <= 0
                || bar.trades == 0
                || bar.volume <= 0
                || bar.notional <= 0
                || bar.last_trade_source_ns < start_ns
                || bar.last_trade_source_ns >= bar.end_ns
                || bar.last_trade_live_receipt_ns == Some(0)
                || bar.high < bar.open.max(bar.close)
                || bar.low <= 0
                || bar.low > bar.open.min(bar.close)
            {
                return Err(Error::Conflict(
                    "Strategy 350 completed bar source or geometry".into(),
                ));
            }
            Some((bar.open, bar.high, bar.low))
        } else {
            None
        };
        let next = start_ns
            .checked_add(BASE_INTERVAL_NS)
            .ok_or_else(|| Error::Capacity("Strategy 350 screen clock".into()))?;
        let (needs_refinement, late_before, prior_high_atoms) =
            self.state.observe(bucket, self.thresholds);
        self.next_start_ns = next;
        Ok(ScreenPoint {
            scope: self.scope,
            start_ns,
            source: self.source,
            needs_refinement,
            late_before,
            prior_high_atoms,
        })
    }
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
        let thresholds = thresholds(config, fact, batch.price_scale, request.interval.start)?;
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
            let (possible, late_before, prior_high) =
                ticker.observe(present.then_some((bar_open, bar_high, bar_low)), thresholds);
            out.prior_high_atoms.push(prior_high);
            out.late_before.push(late_before);
            out.needs_refinement.push(possible);
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
    #[test]
    fn streaming_and_batch_use_the_same_completed_bucket_rule() {
        let product = fixture();
        let batch = &product.batches()[0];
        let expected = project(&product, &config(), &closes("19.99")).unwrap();
        let hash = config().hash().unwrap();
        for source in [
            crate::exact_bars::Mode::Historical,
            crate::exact_bars::Mode::Live,
        ] {
            let mut stream = StreamingScreen::new(
                crate::event_order::Scope {
                    provider: 1,
                    instrument: 10,
                    session: 20260922,
                },
                S,
                2,
                source,
                &config(),
                &hash,
                &closes("19.99")[&10],
            )
            .unwrap();
            for index in 0..batch.count as usize {
                let start = S + index as u64 * BASE_INTERVAL_NS;
                let bar = batch.present[index].then(|| crate::exact_bars::Bar {
                    start_ns: start,
                    end_ns: start + BASE_INTERVAL_NS,
                    price_scale: 2,
                    size_scale: 0,
                    open: batch.open.as_ref().unwrap()[index],
                    high: batch.high.as_ref().unwrap()[index],
                    low: batch.low.as_ref().unwrap()[index],
                    close: batch.open.as_ref().unwrap()[index],
                    volume: 1,
                    notional: i128::from(batch.open.as_ref().unwrap()[index]),
                    trades: 1,
                    last_trade_source_ns: start + 1,
                    last_trade_live_receipt_ns: (source == crate::exact_bars::Mode::Live)
                        .then_some(start + 2),
                });
                let point = stream.observe(start, bar.as_ref()).unwrap();
                assert_eq!(point.source, source);
                assert_eq!(point.needs_refinement, expected[0].needs_refinement[index]);
                assert_eq!(point.late_before, expected[0].late_before[index]);
                assert_eq!(point.prior_high_atoms, expected[0].prior_high_atoms[index]);
            }
            assert!(stream.observe(S + 5 * BASE_INTERVAL_NS, None).is_err());
        }
    }
    #[test]
    fn live_stream_rejects_historical_bar_without_advancing() {
        let hash = config().hash().unwrap();
        let mut stream = StreamingScreen::new(
            crate::event_order::Scope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            S,
            2,
            crate::exact_bars::Mode::Live,
            &config(),
            &hash,
            &closes("19")[&10],
        )
        .unwrap();
        let historical = crate::exact_bars::Bar {
            start_ns: S,
            end_ns: S + BASE_INTERVAL_NS,
            price_scale: 2,
            size_scale: 0,
            open: 1000,
            high: 1000,
            low: 1000,
            close: 1000,
            volume: 1,
            notional: 1000,
            trades: 1,
            last_trade_source_ns: S + 1,
            last_trade_live_receipt_ns: None,
        };
        assert!(stream.observe(S, Some(&historical)).is_err());
        let mut live = historical;
        live.last_trade_live_receipt_ns = Some(S + 2);
        assert!(stream.observe(S, Some(&live)).is_ok());
    }
}
