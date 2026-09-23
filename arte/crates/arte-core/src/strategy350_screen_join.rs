//! Conservative Strategy 350 bar, signal and Watchlist conjunction.
//! The output schedules event/quote refinement; it never authorizes an order.
use crate::{
    bar_catalogue, boolean_catalogue,
    execution_interval::ExecutableKind,
    strategy350_bar_screen::{self, ScreenBatch},
    strategy350_catalogue::{SIGNAL, WATCHLIST},
    strategy350_price_gate::PriceFact,
    Error, Result,
};
use std::collections::BTreeMap;

pub struct SelectedBatch {
    pub instrument: u64,
    pub first_start_ns: u64,
    pub refine: Vec<bool>,
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
fn values(product: &boolean_catalogue::Complete) -> Vec<(bool, bool)> {
    product
        .batches()
        .iter()
        .flat_map(|batch| batch.known.iter().copied().zip(batch.value.iter().copied()))
        .collect()
}
pub fn select(
    bar: &bar_catalogue::Complete,
    config: &strategy350_bar_screen::Config,
    prior_close: &PriceFact,
    signal: &boolean_catalogue::Complete,
    watchlist: &boolean_catalogue::Complete,
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
    aligned(
        watchlist,
        bar,
        |kind| matches!(kind, ExecutableKind::Watchlist),
        WATCHLIST,
    )?;
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
    let signal_values = values(signal);
    let watch_values = values(watchlist);
    let total = ((bar.request().interval.end - bar.request().interval.start)
        / bar_catalogue::BASE_INTERVAL_NS) as usize;
    if signal_values.len() != total || watch_values.len() != total {
        return Err(Error::Conflict("Strategy 350 Boolean grid length".into()));
    }
    let mut output = Vec::with_capacity(screen.len());
    for ScreenBatch {
        instrument,
        first_start_ns,
        needs_refinement,
        ..
    } in screen
    {
        let start = ((first_start_ns - bar.request().interval.start)
            / bar_catalogue::BASE_INTERVAL_NS) as usize;
        let mut refine = Vec::with_capacity(needs_refinement.len());
        for (index, bar_possible) in needs_refinement.into_iter().enumerate() {
            let (signal_known, signal_true) = signal_values[start + index];
            let (watch_known, watch_true) = watch_values[start + index];
            if bar_possible && (!signal_known || !watch_known) {
                return Err(Error::Unready(
                    "Strategy 350 required signal or Watchlist unknown".into(),
                ));
            }
            refine.push(bar_possible && signal_true && watch_true);
        }
        output.push(SelectedBatch {
            instrument,
            first_start_ns,
            refine,
        });
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
        read.observe(BoolBatch {
            request_hash: coverage.request_hash.clone(),
            coverage_hash: coverage.hash().unwrap(),
            first_start_ns: S,
            count: 3,
            evaluated: vec![true; 3],
            known,
            value,
        })
        .unwrap();
        read.finish().unwrap()
    }
    fn config() -> strategy350_bar_screen::Config {
        strategy350_bar_screen::Config {
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
        let selected = select(&b, &config(), &close(), &signal, &watch).unwrap();
        assert_eq!(selected[0].refine, vec![true, false, true]);
        let unknown = boolean(
            &b,
            ExecutableKind::Watchlist,
            WATCHLIST,
            vec![true, false, true],
            vec![true, false, true],
        );
        assert!(select(&b, &config(), &close(), &signal, &unknown).is_err());
        let wrong = boolean(
            &b,
            ExecutableKind::Watchlist,
            "wrong",
            vec![true; 3],
            vec![true; 3],
        );
        assert!(select(&b, &config(), &close(), &signal, &wrong).is_err());
    }
}
