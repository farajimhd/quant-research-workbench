//! Conservative Strategy 350 bar and signal screen with explicit Watchlist policy.
//! The output schedules event/quote refinement; it never authorizes an order.
use crate::{
    bar_catalogue, boolean_catalogue, content_hash,
    execution_interval::ExecutableKind,
    strategy350_bar_screen::{self, ScreenBatch},
    strategy350_catalogue::{WatchlistPolicy, SIGNAL, WATCHLIST},
    strategy350_price_gate::PriceFact,
    Error, Result,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub struct SelectedBatch {
    pub instrument: u64,
    pub first_start_ns: u64,
    pub refine: Vec<bool>,
    /// Exact verified inputs and this batch's refinement mask, not an order proof.
    pub evidence_hash: String,
}

struct BooleanCursor<'a> {
    batches: &'a [boolean_catalogue::Batch],
    batch: usize,
    row: usize,
}
impl<'a> BooleanCursor<'a> {
    fn new(product: &'a boolean_catalogue::Complete) -> Self {
        Self {
            batches: product.batches(),
            batch: 0,
            row: 0,
        }
    }
    fn next(&mut self) -> Result<(bool, bool, bool)> {
        while self.batch < self.batches.len() && self.row == self.batches[self.batch].count as usize
        {
            self.batch += 1;
            self.row = 0;
        }
        let batch = self
            .batches
            .get(self.batch)
            .ok_or_else(|| Error::Conflict("Strategy 350 Boolean grid exhausted".into()))?;
        let row = self.row;
        self.row += 1;
        Ok((batch.evaluated[row], batch.known[row], batch.value[row]))
    }
    fn finished(mut self) -> bool {
        while self.batch < self.batches.len() && self.row == self.batches[self.batch].count as usize
        {
            self.batch += 1;
            self.row = 0;
        }
        self.batch == self.batches.len()
    }
}

fn aligned(
    product: &boolean_catalogue::Complete,
    bar: &bar_catalogue::Complete,
    kind: fn(&ExecutableKind) -> bool,
    id: &str,
) -> Result<()> {
    let r = product.request();
    let b = bar.request();
    if !kind(&r.definition.kind)
        || r.definition.id != id
        || r.provider != b.provider
        || r.instrument != b.instruments[0]
        || r.session != b.session
        || r.interval != b.interval
        || r.source_bar_request_hash != b.hash()?
        || r.source_bar_coverage_hash != bar.coverage_hash()
    {
        return Err(Error::Conflict(
            "Strategy 350 Boolean product alignment".into(),
        ));
    }
    Ok(())
}
pub fn select(
    bar: &bar_catalogue::Complete,
    config: &strategy350_bar_screen::Config,
    prior_close: &PriceFact,
    signal: &boolean_catalogue::Complete,
    watchlist_policy: WatchlistPolicy,
    watchlist: Option<&boolean_catalogue::Complete>,
) -> Result<Vec<SelectedBatch>> {
    if bar.request().instruments.len() != 1 {
        return Err(Error::Invalid(
            "Strategy 350 join requires one instrument shard".into(),
        ));
    }
    aligned(
        signal,
        bar,
        |kind| matches!(kind, ExecutableKind::SignalStream),
        SIGNAL,
    )?;
    let watchlist = match watchlist_policy {
        WatchlistPolicy::NotRequired if watchlist.is_none() => None,
        WatchlistPolicy::Required => {
            let product =
                watchlist.ok_or_else(|| Error::Unready("required Watchlist missing".into()))?;
            aligned(
                product,
                bar,
                |kind| matches!(kind, ExecutableKind::Watchlist),
                WATCHLIST,
            )?;
            Some(product)
        }
        WatchlistPolicy::NotRequired => {
            return Err(Error::Invalid("unused Watchlist supplied".into()))
        }
    };
    let instrument = bar.request().instruments[0];
    let closes = BTreeMap::from([(
        instrument,
        PriceFact {
            value: prior_close.value,
            available_at_ns: prior_close.available_at_ns,
            source_hash: prior_close.source_hash.clone(),
            source_order: prior_close.source_order,
        },
    )]);
    let screen = strategy350_bar_screen::project(bar, config, &closes)?;
    if screen.len() != bar.batches().len() {
        return Err(Error::Conflict(
            "Strategy 350 bar screen batch count".into(),
        ));
    }
    let source_hash = content_hash(&(
        "arte.strategy-350-screen-input.v1",
        bar.request().hash()?,
        bar.coverage_hash(),
        signal.request().hash()?,
        signal.coverage_hash(),
        watchlist
            .map(|product| product.request().hash())
            .transpose()?,
        watchlist.map(boolean_catalogue::Complete::coverage_hash),
        config.hash()?,
        prior_close,
    ))?;
    let mut signal_values = BooleanCursor::new(signal);
    let mut watch_values = watchlist.map(BooleanCursor::new);
    let mut output = Vec::with_capacity(screen.len());
    for (
        ScreenBatch {
            instrument,
            first_start_ns,
            needs_refinement,
            late_before,
            prior_high_atoms,
        },
        raw_bar,
    ) in screen.into_iter().zip(bar.batches())
    {
        if raw_bar.instrument != instrument
            || raw_bar.first_start_ns != first_start_ns
            || raw_bar.count as usize != needs_refinement.len()
            || late_before.len() != needs_refinement.len()
            || prior_high_atoms.len() != needs_refinement.len()
        {
            return Err(Error::Conflict("Strategy 350 bar screen alignment".into()));
        }
        let (open, high, low) = raw_bar
            .open
            .as_ref()
            .zip(raw_bar.high.as_ref())
            .zip(raw_bar.low.as_ref())
            .map(|((open, high), low)| (open, high, low))
            .ok_or_else(|| Error::Unready("Strategy 350 OHLC screen columns".into()))?;
        let mut hasher = Sha256::new();
        hasher.update(source_hash.as_bytes());
        hasher.update(instrument.to_be_bytes());
        hasher.update(first_start_ns.to_be_bytes());
        hasher.update(raw_bar.count.to_be_bytes());
        let mut refine = Vec::with_capacity(needs_refinement.len());
        for (index, bar_possible) in needs_refinement.into_iter().enumerate() {
            let (signal_evaluated, signal_known, signal_true) = signal_values.next()?;
            let watch = watch_values.as_mut().map(BooleanCursor::next).transpose()?;
            let (watch_known, watch_true) =
                watch.map_or((true, true), |(_, known, value)| (known, value));
            if bar_possible && (!signal_known || !watch_known) {
                return Err(Error::Unready(
                    "Strategy 350 required signal or Watchlist unknown".into(),
                ));
            }
            let selected = bar_possible && signal_true && watch_true;
            hasher.update([
                u8::from(raw_bar.present[index]),
                u8::from(bar_possible),
                u8::from(late_before[index]),
                u8::from(signal_evaluated),
                u8::from(signal_known),
                u8::from(signal_true),
                u8::from(watch.is_some()),
                u8::from(watch.is_some_and(|(evaluated, _, _)| evaluated)),
                u8::from(watch_known),
                u8::from(watch_true),
                u8::from(selected),
            ]);
            for value in [
                open[index],
                high[index],
                low[index],
                prior_high_atoms[index],
            ] {
                hasher.update(value.to_be_bytes());
            }
            refine.push(selected);
        }
        output.push(SelectedBatch {
            instrument,
            first_start_ns,
            refine,
            evidence_hash: format!("{:x}", hasher.finalize()),
        });
    }
    if !signal_values.finished() || watch_values.is_some_and(|cursor| !cursor.finished()) {
        return Err(Error::Conflict("Strategy 350 Boolean grid length".into()));
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::events::Decimal;
    use crate::{
        bar_catalogue::{
            Batch as BarBatch, Column, Coverage as BarCoverage, Readback as BarReadback,
            Request as BarRequest, Source,
        },
        boolean_catalogue::{
            Batch as BoolBatch, Coverage as BoolCoverage, Readback as BoolReadback,
            Request as BoolRequest,
        },
        coverage::Interval,
        execution_interval::{ExecutionContract, ExecutionInterval},
    };
    use std::collections::BTreeSet;
    const S: u64 = 1_000_000_000;
    fn bar() -> bar_catalogue::Complete {
        let interval = Interval {
            start: S,
            end: S + 300_000_000,
        };
        let request = BarRequest {
            provider: 1,
            instruments: vec![10],
            session: 20260922,
            interval,
            timeframe_ns: 100_000_000,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: BTreeSet::from([Column::Open, Column::High, Column::Low]),
            maximum_rows: 3,
        };
        let coverage = BarCoverage {
            provider: 1,
            session: 20260922,
            interval,
            timeframe_ns: 100_000_000,
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
        let mut read = BarReadback::new(request.clone(), &coverage, 2 * S).unwrap();
        read.observe(BarBatch {
            request_hash: request.hash().unwrap(),
            coverage_hash: coverage.hash().unwrap(),
            instrument: 10,
            first_start_ns: S,
            count: 3,
            price_scale: 2,
            size_scale: 2,
            present: vec![true, true, true],
            open: Some(vec![1000, 1150, 1050]),
            high: Some(vec![1000, 1150, 1050]),
            low: Some(vec![1000, 1150, 1050]),
            close: None,
            volume: None,
            notional: None,
            trades: None,
        })
        .unwrap();
        read.finish().unwrap()
    }
    fn boolean(
        bar: &bar_catalogue::Complete,
        kind: ExecutableKind,
        id: &str,
        known: Vec<bool>,
        value: Vec<bool>,
    ) -> boolean_catalogue::Complete {
        boolean_with_parts(bar, kind, id, known, value, &[3])
    }
    fn boolean_with_parts(
        bar: &bar_catalogue::Complete,
        kind: ExecutableKind,
        id: &str,
        known: Vec<bool>,
        value: Vec<bool>,
        parts: &[usize],
    ) -> boolean_catalogue::Complete {
        let request = BoolRequest {
            provider: 1,
            instrument: 10,
            session: 20260922,
            interval: bar.request().interval,
            definition: ExecutionContract {
                kind,
                id: id.into(),
                implementation_hash: "d".repeat(64),
                interval: ExecutionInterval::Fixed(100_000_000),
            },
            source_bar_request_hash: bar.request().hash().unwrap(),
            source_bar_coverage_hash: bar.coverage_hash().into(),
            maximum_rows: 3,
        };
        let mut digest = boolean_catalogue::TransitionDigest::new(&request).unwrap();
        let mut last = None;
        for (i, (&k, &v)) in known.iter().zip(&value).enumerate() {
            let next = k.then_some(v);
            if next != last {
                digest.observe(S + i as u64 * 100_000_000, k, v).unwrap();
            }
            last = next;
        }
        let (transition_hash, transition_count) = digest.finish();
        let coverage = BoolCoverage {
            request_hash: request.hash().unwrap(),
            source_bar_coverage_hash: bar.coverage_hash().into(),
            producer_hash: "d".repeat(64),
            transition_hash,
            transition_count,
            published_at_ns: 3 * S,
        };
        let mut read = BoolReadback::new(request, &coverage, 3 * S).unwrap();
        let mut offset = 0;
        for &count in parts {
            let end = offset + count;
            read.observe(BoolBatch {
                request_hash: coverage.request_hash.clone(),
                coverage_hash: coverage.hash().unwrap(),
                first_start_ns: S + offset as u64 * 100_000_000,
                count: count as u32,
                evaluated: vec![true; count],
                known: known[offset..end].to_vec(),
                value: value[offset..end].to_vec(),
            })
            .unwrap();
            offset = end;
        }
        read.finish().unwrap()
    }
    fn config() -> strategy350_bar_screen::Config {
        strategy350_bar_screen::Config {
            execution_interval: ExecutionInterval::Fixed(100_000_000),
            prior_close_source_hash: "e".repeat(64),
            prior_close_max: Decimal::parse("20").unwrap(),
            purchase_min: Decimal::parse("1").unwrap(),
            late_gain_bps: 1500,
            hod_floor_bps: 7000,
        }
    }
    fn close() -> PriceFact {
        PriceFact {
            value: Decimal::parse("19").unwrap(),
            available_at_ns: S - 1,
            source_hash: "e".repeat(64),
            source_order: None,
        }
    }
    #[test]
    fn only_known_true_signal_and_watchlist_schedule_refinement() {
        let b = bar();
        let signal = boolean(
            &b,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![true; 3],
        );
        let watch = boolean(
            &b,
            ExecutableKind::Watchlist,
            WATCHLIST,
            vec![true; 3],
            vec![true, false, true],
        );
        let selected = select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::Required,
            Some(&watch),
        )
        .unwrap();
        assert_eq!(selected[0].refine, vec![true, false, true]);
        let unknown = boolean(
            &b,
            ExecutableKind::Watchlist,
            WATCHLIST,
            vec![true, false, true],
            vec![true, false, true],
        );
        assert!(select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::Required,
            Some(&unknown)
        )
        .is_err());
        let wrong = boolean(
            &b,
            ExecutableKind::Watchlist,
            "wrong",
            vec![true; 3],
            vec![true; 3],
        );
        assert!(select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::Required,
            Some(&wrong)
        )
        .is_err());
        assert!(select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::Required,
            None
        )
        .is_err());
        let without_watch = select(
            &b,
            &config(),
            &close(),
            &signal,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        assert_eq!(without_watch[0].refine, vec![true; 3]);
    }
    #[test]
    fn evidence_is_partition_independent_and_binds_prior_close_clock() {
        let b = bar();
        let one = boolean(
            &b,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![true; 3],
        );
        let split = boolean_with_parts(
            &b,
            ExecutableKind::SignalStream,
            SIGNAL,
            vec![true; 3],
            vec![true; 3],
            &[1, 2],
        );
        let select_one = select(
            &b,
            &config(),
            &close(),
            &one,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        let select_split = select(
            &b,
            &config(),
            &close(),
            &split,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        assert_eq!(select_one[0].refine, select_split[0].refine);
        assert_eq!(select_one[0].evidence_hash, select_split[0].evidence_hash);
        assert_eq!(select_one[0].evidence_hash.len(), 64);
        let mut later_close = close();
        later_close.available_at_ns -= 1;
        let changed = select(
            &b,
            &config(),
            &later_close,
            &one,
            WatchlistPolicy::NotRequired,
            None,
        )
        .unwrap();
        assert_eq!(select_one[0].refine, changed[0].refine);
        assert_ne!(select_one[0].evidence_hash, changed[0].evidence_hash);
    }
}
