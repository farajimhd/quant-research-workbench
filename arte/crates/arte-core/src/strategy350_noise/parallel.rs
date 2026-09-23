//! Bounded, deterministic multi-ticker historical noise projection.
use super::{project_compact_one_second, CompletedBar, SECOND};
use crate::{bar_catalogue::Complete, Error, Result};
use std::{collections::BTreeSet, thread};

#[derive(Clone, Copy)]
pub struct Pinned<'a> {
    pub product: &'a Complete,
    pub request_hash: &'a str,
    pub coverage_hash: &'a str,
}
pub struct Projection {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub bars: Vec<Option<CompletedBar>>,
}
/// Products are independent ticker/session units. The caller pins exact
/// catalogue identities and controls both concurrency and result memory.
pub fn project_many(
    products: &[Pinned<'_>],
    worker_count: usize,
    maximum_seconds: usize,
) -> Result<Vec<Projection>> {
    if products.is_empty()
        || products.len() > 100_000
        || worker_count == 0
        || worker_count > 256
        || maximum_seconds == 0
        || maximum_seconds > 10_000_000
    {
        return Err(Error::Invalid("Strategy 350 parallel noise budget".into()));
    }
    let mut sorted = products.to_vec();
    sorted.sort_by_key(|pin| {
        let request = pin.product.request();
        (
            request.session,
            request.provider,
            request.instruments.first().copied(),
        )
    });
    let mut seen = BTreeSet::new();
    let mut seconds = 0usize;
    for pin in &sorted {
        let request = pin.product.request();
        if request.instruments.len() != 1
            || !seen.insert((request.session, request.provider, request.instruments[0]))
            || !request.interval.start.is_multiple_of(SECOND)
            || !request.interval.end.is_multiple_of(SECOND)
        {
            return Err(Error::Conflict("Strategy 350 parallel ticker scope".into()));
        }
        seconds = seconds
            .checked_add(((request.interval.end - request.interval.start) / SECOND) as usize)
            .ok_or_else(|| Error::Capacity("Strategy 350 parallel seconds overflow".into()))?;
        if seconds > maximum_seconds {
            return Err(Error::Capacity(
                "Strategy 350 parallel output budget".into(),
            ));
        }
    }
    let compute = |pin: &Pinned<'_>| -> Result<Projection> {
        let request = pin.product.request();
        Ok(Projection {
            provider: request.provider,
            instrument: request.instruments[0],
            session: request.session,
            bars: project_compact_one_second(pin.product, pin.request_hash, pin.coverage_hash)?,
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
                    .map_err(|_| Error::Unready("Strategy 350 noise worker panicked".into()))?,
            );
        }
        results.into_iter().collect()
    })
}
