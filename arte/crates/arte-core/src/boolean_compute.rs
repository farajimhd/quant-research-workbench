//! Bounded columnar publication grid for fixed-cadence Boolean calculations.
//! Algorithms supply one result per declared evaluation boundary. This module
//! carries state between boundaries but never substitutes unknown with false.
use crate::{
    bar_catalogue::{Complete as Bars, BASE_INTERVAL_NS},
    boolean_catalogue::Request,
    execution_interval::ExecutionInterval,
    Error, Result,
};

pub const MAX_BATCH_ROWS: usize = 1_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Evaluation {
    pub bucket_start_ns: u64,
    pub value: Option<bool>,
}

pub struct DenseBatch {
    pub first_start_ns: u64,
    pub evaluated: Vec<bool>,
    pub known: Vec<bool>,
    pub value: Vec<bool>,
}

/// Results must be ordered and complete on the declared cadence grid. The
/// bar catalogue has already certified a complete source interval; no source
/// or execution timestamp is inferred from a missing row.
pub fn project_fixed(
    bars: &Bars,
    request: &Request,
    evaluations: &[Evaluation],
    batch_rows: usize,
) -> Result<Vec<DenseBatch>> {
    request.validate()?;
    let source = bars.request();
    if source.instruments.as_slice() != [request.instrument]
        || source.provider != request.provider
        || source.session != request.session
        || source.interval != request.interval
        || source.timeframe_ns != BASE_INTERVAL_NS
        || source.hash()? != request.source_bar_request_hash
        || bars.coverage_hash() != request.source_bar_coverage_hash
    {
        return Err(Error::Conflict(
            "Boolean calculation bar source differs".into(),
        ));
    }
    let ExecutionInterval::Fixed(cadence_ns) = request.definition.interval else {
        return Err(Error::Invalid(
            "bar projection requires fixed cadence".into(),
        ));
    };
    if batch_rows == 0 || batch_rows > MAX_BATCH_ROWS {
        return Err(Error::Capacity("Boolean calculation batch rows".into()));
    }
    let buckets = ((request.interval.end - request.interval.start) / BASE_INTERVAL_NS) as usize;
    let expected = (0..buckets)
        .filter(|i| {
            (request.interval.start + (*i as u64 + 1) * BASE_INTERVAL_NS).is_multiple_of(cadence_ns)
        })
        .count();
    if evaluations.len() != expected {
        return Err(Error::Unready("Boolean evaluation grid incomplete".into()));
    }
    let mut result = Vec::with_capacity(buckets.div_ceil(batch_rows));
    let mut cursor = 0;
    let mut state = None;
    for start in (0..buckets).step_by(batch_rows) {
        let count = (buckets - start).min(batch_rows);
        let mut batch = DenseBatch {
            first_start_ns: request.interval.start + start as u64 * BASE_INTERVAL_NS,
            evaluated: Vec::with_capacity(count),
            known: Vec::with_capacity(count),
            value: Vec::with_capacity(count),
        };
        for offset in 0..count {
            let bucket_start_ns = batch.first_start_ns + offset as u64 * BASE_INTERVAL_NS;
            let due = (bucket_start_ns + BASE_INTERVAL_NS).is_multiple_of(cadence_ns);
            if due {
                let evaluation = evaluations[cursor];
                if evaluation.bucket_start_ns != bucket_start_ns {
                    return Err(Error::Conflict(
                        "Boolean evaluation boundary differs".into(),
                    ));
                }
                state = evaluation.value;
                cursor += 1;
            }
            batch.evaluated.push(due);
            batch.known.push(state.is_some());
            batch.value.push(state.unwrap_or(false));
        }
        result.push(batch);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        bar_catalogue::{self, Column, Source},
        coverage::Interval,
        execution_interval::{ExecutableKind, ExecutionContract},
    };
    use std::collections::{BTreeMap, BTreeSet};

    fn source() -> (Bars, Request) {
        let interval = Interval {
            start: 1_000_000_000,
            end: 1_400_000_000,
        };
        let bar_request = bar_catalogue::Request {
            provider: 1,
            instruments: vec![10],
            session: 20260922,
            interval,
            timeframe_ns: BASE_INTERVAL_NS,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: BTreeSet::from([Column::Close]),
            maximum_rows: 4,
        };
        let coverage = bar_catalogue::Coverage {
            provider: 1,
            session: 20260922,
            interval,
            timeframe_ns: BASE_INTERVAL_NS,
            source_generation: bar_request.source_generation.clone(),
            calculation_hash: bar_request.calculation_hash.clone(),
            sources: BTreeMap::from([(
                10,
                Source {
                    certificate_hash: "c".repeat(64),
                    price_scale: 2,
                    size_scale: 0,
                },
            )]),
            published_at_ns: 2_000_000_000,
        };
        let mut read =
            bar_catalogue::Readback::new(bar_request.clone(), &coverage, 2_000_000_000).unwrap();
        read.observe(bar_catalogue::Batch {
            request_hash: bar_request.hash().unwrap(),
            coverage_hash: coverage.hash().unwrap(),
            instrument: 10,
            first_start_ns: interval.start,
            count: 4,
            price_scale: 2,
            size_scale: 0,
            present: vec![false; 4],
            open: None,
            high: None,
            low: None,
            close: Some(vec![0; 4]),
            volume: None,
            notional: None,
            trades: None,
        })
        .unwrap();
        let bars = read.finish().unwrap();
        let request = Request {
            provider: 1,
            instrument: 10,
            session: 20260922,
            interval,
            definition: ExecutionContract {
                kind: ExecutableKind::SignalStream,
                id: "rule".into(),
                implementation_hash: "d".repeat(64),
                interval: ExecutionInterval::Fixed(200_000_000),
            },
            source_bar_request_hash: bar_request.hash().unwrap(),
            source_bar_coverage_hash: bars.coverage_hash().into(),
            maximum_rows: 4,
        };
        (bars, request)
    }

    #[test]
    fn fixed_grid_carries_true_then_unknown_without_inventing_false() {
        let (bars, request) = source();
        let evaluations = [
            Evaluation {
                bucket_start_ns: 1_100_000_000,
                value: Some(true),
            },
            Evaluation {
                bucket_start_ns: 1_300_000_000,
                value: None,
            },
        ];
        let batches = project_fixed(&bars, &request, &evaluations, 3).unwrap();
        assert_eq!(batches.len(), 2);
        assert_eq!(batches[0].evaluated, vec![false, true, false]);
        assert_eq!(batches[0].known, vec![false, true, true]);
        assert_eq!(batches[0].value, vec![false, true, true]);
        assert_eq!(batches[1].evaluated, vec![true]);
        assert_eq!(batches[1].known, vec![false]);
        assert_eq!(batches[1].value, vec![false]);
        assert!(project_fixed(&bars, &request, &evaluations[..1], 3).is_err());
        let wrong = [evaluations[1], evaluations[0]];
        assert!(project_fixed(&bars, &request, &wrong, 3).is_err());
        assert!(project_fixed(&bars, &request, &evaluations, 0).is_err());
        let mut mismatched = request.clone();
        mismatched.source_bar_coverage_hash = "e".repeat(64);
        assert!(project_fixed(&bars, &mismatched, &evaluations, 3).is_err());
    }
}
