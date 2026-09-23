//! Historical event-signal precomputation on the shared causal playback.
//! This lane cannot place orders or acknowledge account decision journals.
use super::{ledger::SourceLedger, Builder, Product};
use crate::{
    coverage::Interval,
    execution_interval::ExecutionContract,
    market_structure::scheduler::{
        playback::{sources::Catalog, Mode, Playback, Poll, Prepared},
        Boundary,
    },
    quote_state::Book,
    run_manifest::Pinned,
    Error, Result,
};

pub struct Limits {
    pub maximum_events: u64,
    pub maximum_transitions: usize,
    pub maximum_boundaries: u64,
}

pub struct Context<'a> {
    pub prepared: &'a Prepared,
    pub catalog: &'a Catalog,
    pub manifest: &'a Pinned,
    pub interval: Interval,
    pub definition: &'a ExecutionContract,
    pub limits: Limits,
    pub certified_at_ns: u64,
}

/// Produce one complete event-cadence Boolean product from an unused playback.
/// The callback sees the market/quote state at each causal event boundary.
/// It must be deterministic for the pinned implementation hash. No source
/// timestamp or missing live receipt is synthesized by this function.
pub fn project(
    mut playback: Playback,
    context: Context<'_>,
    mut evaluate: impl FnMut(
        &Boundary<'_>,
        &crate::market_structure::Runtime,
        &Book,
    ) -> Result<Option<bool>>,
) -> Result<(Product, super::ledger::SourceProof)> {
    let Context {
        prepared,
        catalog,
        manifest,
        interval,
        definition,
        limits,
        certified_at_ns,
    } = context;
    catalog.require(manifest, prepared)?;
    prepared.require_interval(interval)?;
    let status = playback.status();
    if status.mode != Mode::Paused
        || status.prepared_hash != prepared.hash()
        || playback.scope() != prepared.scope()
        || playback.run_id() != manifest.manifest().run_id
        || status.completed_frames != 0
        || status.admitted_events != 0
        || status.coalesced_events != 0
        || status.queued_events != 0
        || status.acknowledged_boundaries != 0
        || status.pending_boundary
        || status.failure.is_some()
        || limits.maximum_boundaries == 0
        || limits.maximum_boundaries > 20_000_000
    {
        return Err(Error::Conflict(
            "event Boolean replay requires unused pinned playback".into(),
        ));
    }
    let scope = prepared.scope();
    let mut ledger = SourceLedger::new(scope, interval, definition, limits.maximum_events)?;
    let mut product = Builder::new(
        scope,
        interval,
        definition,
        limits.maximum_events,
        limits.maximum_transitions,
    )?;
    let mut boundaries = 0_u64;
    playback.resume()?;
    loop {
        match playback.poll()? {
            Poll::Boundary => {
                boundaries = boundaries
                    .checked_add(1)
                    .filter(|count| *count <= limits.maximum_boundaries)
                    .ok_or_else(|| {
                        Error::Capacity("event Boolean replay boundary budget".into())
                    })?;
                let boundary = playback.pending()?.ok_or_else(|| {
                    Error::Unready("event Boolean replay boundary missing".into())
                })?;
                if !matches!(
                    boundary.kind,
                    crate::market_structure::scheduler::Kind::Completed { .. }
                ) {
                    let value = evaluate(&boundary, playback.market()?, playback.quotes()?)?;
                    product.observe(&boundary, value)?;
                }
                ledger.observe(&boundary)?;
                let id = boundary.id.to_owned();
                playback.acknowledge(&id)?;
            }
            Poll::Yield => {}
            Poll::Complete => break,
            Poll::Paused => {
                return Err(Error::Conflict(
                    "event Boolean replay paused unexpectedly".into(),
                ))
            }
        }
    }
    let proof = ledger.certify_playback(&playback, prepared, catalog, manifest, certified_at_ns)?;
    Ok((product.seal_verified(&proof)?, proof))
}
