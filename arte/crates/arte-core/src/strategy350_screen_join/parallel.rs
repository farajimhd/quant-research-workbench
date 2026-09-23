//! Parallel, bounded preparation of independent Strategy 350 screen shards.
//! The complete market/V7 tape is unaffected by this sparse strategy plan.
use super::{select, RefinementPlan};
use crate::{
    bar_catalogue::Complete as Bars,
    boolean_catalogue::Complete as Booleans,
    event_order::Scope,
    strategy350_bar_screen::Config as ScreenConfig,
    strategy350_catalogue::WatchlistPolicy,
    strategy350_effective::{hash_bytes, Config as EffectiveConfig},
    strategy350_price_gate::PriceFact,
    Error, Result,
};
use std::{
    collections::BTreeSet,
    sync::atomic::{AtomicUsize, Ordering},
    thread,
};

pub struct Pinned<'a> {
    pub bars: &'a Bars,
    pub signal: &'a Booleans,
    pub watchlist: Option<&'a Booleans>,
    pub screen: &'a ScreenConfig,
    pub prior_close: &'a PriceFact,
    pub effective: &'a EffectiveConfig,
    pub watchlist_policy: WatchlistPolicy,
}
pub struct Limits {
    pub workers: usize,
    pub maximum_intervals_per_shard: usize,
    pub maximum_total_intervals: usize,
    pub maximum_selected_buckets_per_shard: usize,
    pub maximum_total_selected_buckets: usize,
}
pub struct Projection {
    pub scope: Scope,
    pub selected_buckets: usize,
    pub plan: RefinementPlan,
}
fn scope(pin: &Pinned<'_>) -> Result<Scope> {
    let request = pin.bars.request();
    if request.instruments.len() != 1 {
        return Err(Error::Invalid(
            "Strategy 350 screen shard requires one instrument".into(),
        ));
    }
    Ok(Scope {
        provider: request.provider,
        instrument: request.instruments[0],
        session: request.session,
    })
}
fn reserve(counter: &AtomicUsize, amount: usize, maximum: usize) -> Result<()> {
    counter
        .fetch_update(Ordering::AcqRel, Ordering::Acquire, |used| {
            used.checked_add(amount).filter(|next| *next <= maximum)
        })
        .map(|_| ())
        .map_err(|_| Error::Capacity("Strategy 350 screen run budget".into()))
}
/// Each worker owns disjoint ticker/session products. Results are returned in
/// stable session/provider/instrument order regardless of completion timing.
pub fn prepare_many(products: Vec<Pinned<'_>>, limits: Limits) -> Result<Vec<Projection>> {
    if products.is_empty()
        || products.len() > 100_000
        || limits.workers == 0
        || limits.workers > 256
        || limits.maximum_intervals_per_shard == 0
        || limits.maximum_intervals_per_shard > 1_000_000
        || limits.maximum_total_intervals == 0
        || limits.maximum_total_intervals > 10_000_000
        || limits.maximum_selected_buckets_per_shard == 0
        || limits.maximum_selected_buckets_per_shard > 2_000_000
        || limits.maximum_total_selected_buckets == 0
        || limits.maximum_total_selected_buckets > 100_000_000
    {
        return Err(Error::Capacity("Strategy 350 screen worker limits".into()));
    }
    let mut keyed = products
        .into_iter()
        .map(|pin| scope(&pin).map(|scope| (scope, pin)))
        .collect::<Result<Vec<_>>>()?;
    keyed.sort_by_key(|(scope, _)| (scope.session, scope.provider, scope.instrument));
    let mut seen = BTreeSet::new();
    for (scope, pin) in &keyed {
        if !seen.insert((scope.session, scope.provider, scope.instrument)) {
            return Err(Error::Conflict(
                "Strategy 350 duplicate screen shard".into(),
            ));
        }
        pin.effective.validate()?;
        pin.effective.require_screen_inputs(
            hash_bytes(&pin.screen.hash()?)?,
            hash_bytes(&pin.signal.request().definition.implementation_hash)?,
            pin.watchlist
                .map(|product| hash_bytes(&product.request().definition.implementation_hash))
                .transpose()?,
        )?;
    }
    let intervals = AtomicUsize::new(0);
    let selected_buckets = AtomicUsize::new(0);
    let compute = |(scope, pin): &(Scope, Pinned<'_>)| -> Result<Projection> {
        let selected = select(
            pin.bars,
            pin.screen,
            pin.prior_close,
            pin.signal,
            pin.watchlist_policy,
            pin.watchlist,
        )?;
        let buckets = selected
            .iter()
            .try_fold(0usize, |count, batch| {
                count.checked_add(batch.refine().iter().filter(|&&value| value).count())
            })
            .ok_or_else(|| Error::Capacity("Strategy 350 selected bucket count".into()))?;
        if buckets > limits.maximum_selected_buckets_per_shard {
            return Err(Error::Capacity("Strategy 350 selected bucket shard".into()));
        }
        let plan = RefinementPlan::from_batches(
            *scope,
            pin.bars.request().interval,
            &selected,
            limits.maximum_intervals_per_shard,
        )?;
        reserve(
            &intervals,
            plan.intervals().len(),
            limits.maximum_total_intervals,
        )?;
        reserve(
            &selected_buckets,
            buckets,
            limits.maximum_total_selected_buckets,
        )?;
        Ok(Projection {
            scope: *scope,
            selected_buckets: buckets,
            plan,
        })
    };
    if limits.workers == 1 || keyed.len() == 1 {
        return keyed.iter().map(compute).collect();
    }
    let chunk = keyed.len().div_ceil(limits.workers.min(keyed.len()));
    thread::scope(|thread_scope| {
        let handles: Vec<_> = keyed
            .chunks(chunk)
            .map(|shard| {
                let compute = &compute;
                thread_scope.spawn(move || shard.iter().map(compute).collect::<Result<Vec<_>>>())
            })
            .collect();
        let mut output = Vec::with_capacity(keyed.len());
        let mut first_error = None;
        for handle in handles {
            match handle.join() {
                Ok(Ok(shard)) if first_error.is_none() => output.extend(shard),
                Ok(Ok(_)) => {}
                Ok(Err(error)) if first_error.is_none() => first_error = Some(error),
                Ok(Err(_)) => {}
                Err(_) if first_error.is_none() => {
                    first_error = Some(Error::Unready("Strategy 350 screen worker panicked".into()))
                }
                Err(_) => {}
            }
        }
        first_error.map_or(Ok(output), Err)
    })
}
