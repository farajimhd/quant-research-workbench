//! Bounded, deterministic preparation of independent historical signal shards.
use super::{prepare_strategy350_signal, Prepared};
use arte_core::{bar_catalogue::Complete as Bars, boolean_catalogue::Request, Error, Result};
use std::{collections::BTreeSet, thread};

pub struct Pinned<'a> {
    pub bars: &'a Bars,
    pub request: Request,
    pub config: arte_core::strategy350_signal::Config,
    pub session_start_ns: u64,
    pub published_at_ns: u64,
}

pub struct Projection {
    pub prepared: Prepared,
    pub first_occurrence_end_ns: Option<u64>,
}

/// Each shard is a separately certified ticker/session computation. At most
/// two transitions are possible: known false, followed by latched true.
/// Reserve that worst-case result budget before starting any worker.
pub fn prepare_many(
    products: Vec<Pinned<'_>>,
    worker_count: usize,
    maximum_transition_rows: usize,
) -> Result<Vec<Projection>> {
    if products.is_empty()
        || products.len() > 100_000
        || worker_count == 0
        || worker_count > 256
        || maximum_transition_rows == 0
        || products
            .len()
            .checked_mul(2)
            .is_none_or(|rows| rows > maximum_transition_rows)
    {
        return Err(Error::Capacity(
            "Strategy 350 parallel signal budget".into(),
        ));
    }
    let mut sorted = products;
    sorted.sort_by_key(|pin| {
        (
            pin.request.session,
            pin.request.provider,
            pin.request.instrument,
        )
    });
    let mut seen = BTreeSet::new();
    for pin in &sorted {
        if !seen.insert((
            pin.request.session,
            pin.request.provider,
            pin.request.instrument,
        )) {
            return Err(Error::Conflict(
                "Strategy 350 duplicate signal scope".into(),
            ));
        }
    }
    let compute = |pin: &Pinned<'_>| -> Result<Projection> {
        let (prepared, first_occurrence_end_ns) = prepare_strategy350_signal(
            pin.bars,
            pin.request.clone(),
            pin.config.clone(),
            pin.session_start_ns,
            pin.published_at_ns,
        )?;
        Ok(Projection {
            prepared,
            first_occurrence_end_ns,
        })
    };
    if worker_count == 1 || sorted.len() == 1 {
        return sorted.iter().map(compute).collect();
    }
    let chunk = sorted.len().div_ceil(worker_count.min(sorted.len()));
    thread::scope(|scope| {
        let handles: Vec<_> = sorted
            .chunks(chunk)
            .map(|slice| scope.spawn(move || slice.iter().map(compute).collect::<Vec<_>>()))
            .collect();
        let mut results = Vec::with_capacity(sorted.len());
        for handle in handles {
            results.extend(
                handle
                    .join()
                    .map_err(|_| Error::Unready("Strategy 350 signal worker panicked".into()))?,
            );
        }
        results.into_iter().collect()
    })
}
