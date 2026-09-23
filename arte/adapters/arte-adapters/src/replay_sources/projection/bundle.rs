//! One historical run catalog over independently verified ticker/session
//! projections. The catalog is a run pin, not a source acquisition receipt.
use super::{bar_link::require_compact_bar_source, Projection};
use arte_core::{
    bar_catalogue::{Complete as Bars, Coverage as BarCoverage},
    content_hash,
    event_order::Scope,
    events::EventKind,
    market_structure::scheduler::playback::sources::{Catalog, HistoricalSource, Shard},
    run_manifest::{Clock, Pinned},
    strategy350_screen_join::RefinementPlan,
    strategy_dispatch::Mode,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::{
    sync::atomic::{AtomicUsize, Ordering},
    thread,
};

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub projection_manifest_hash: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u32,
    pub entries: Vec<Entry>,
}
pub struct Bundle<'a> {
    projections: Vec<&'a Projection>,
    manifest: Manifest,
    catalog: Catalog,
}
/// Sparse strategy lookup into the complete, run-pinned historical tape.
/// These positions must never be used to narrow market or V7 replay.
#[derive(Clone, Serialize)]
pub struct SelectedIndex {
    pub scope: ScopeKey,
    pub plan_hash: String,
    pub prepared_hash: String,
    pub trade_positions: Vec<(usize, usize)>,
    pub quote_positions: Vec<(usize, usize)>,
}
#[derive(Clone, Copy, PartialEq, Eq, Serialize)]
pub struct ScopeKey {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
}
impl From<Scope> for ScopeKey {
    fn from(value: Scope) -> Self {
        Self {
            provider: value.provider,
            instrument: value.instrument,
            session: value.session,
        }
    }
}
impl SelectedIndex {
    pub fn hash(&self) -> Result<String> {
        content_hash(&("arte.historical-selected-index.v1", self))
    }
}
pub struct SelectedInput<'a> {
    pub bars: &'a Bars,
    pub coverage: &'a BarCoverage,
    pub plan: &'a RefinementPlan,
    pub source_as_of_ns: u64,
}
fn key(provider: u16, instrument: u64, session: u32) -> (u16, u64, u32) {
    (provider, instrument, session)
}
fn hash_ok(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
impl Manifest {
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || self.entries.is_empty()
            || self.entries.len() > 100_000
            || self.entries.iter().any(|entry| {
                entry.provider == 0
                    || entry.instrument == 0
                    || !(19000101..=29991231).contains(&entry.session)
                    || !hash_ok(&entry.projection_manifest_hash)
            })
            || self.entries.windows(2).any(|pair| {
                key(pair[0].provider, pair[0].instrument, pair[0].session)
                    >= key(pair[1].provider, pair[1].instrument, pair[1].session)
            })
        {
            return Err(Error::Conflict(
                "historical projection bundle manifest".into(),
            ));
        }
        content_hash(&("arte.historical-projection-bundle.v1", self))
    }
}
impl<'a> Bundle<'a> {
    /// Require exactly one screened product per certified shard. Each product
    /// is authority-linked before its selected trade and quote positions are
    /// indexed. The full prepared tape remains unchanged.
    pub fn index_selected(
        &self,
        mut inputs: Vec<SelectedInput<'_>>,
        run: &Pinned,
        workers: usize,
        maximum_per_shard: usize,
        maximum_total: usize,
    ) -> Result<Vec<SelectedIndex>> {
        if inputs.len() != self.projections.len()
            || maximum_per_shard == 0
            || maximum_per_shard > 10_000_000
            || maximum_total == 0
            || maximum_total > 100_000_000
            || workers == 0
            || workers > 256
        {
            return Err(Error::Capacity("historical selected index bounds".into()));
        }
        inputs.sort_by_key(|input| {
            let scope = input.plan.scope();
            key(scope.provider, scope.instrument, scope.session)
        });
        for (entry, input) in self.manifest.entries.iter().zip(&inputs) {
            let scope = input.plan.scope();
            if key(entry.provider, entry.instrument, entry.session)
                != key(scope.provider, scope.instrument, scope.session)
            {
                return Err(Error::Conflict(
                    "historical selected index shard set".into(),
                ));
            }
        }
        let total = AtomicUsize::new(0);
        let compute = |input: &SelectedInput<'_>| -> Result<SelectedIndex> {
            let scope = input.plan.scope();
            let source =
                self.bind_compact_bars(input.bars, input.coverage, run, input.source_as_of_ns)?;
            let prepared = source.prepared();
            let positions = input
                .plan
                .selected_prepared_positions(prepared, maximum_per_shard)?;
            total
                .fetch_update(Ordering::AcqRel, Ordering::Acquire, |used| {
                    used.checked_add(positions.len())
                        .filter(|count| *count <= maximum_total)
                })
                .map_err(|_| Error::Capacity("historical selected index run budget".into()))?;
            let mut trade_positions = Vec::new();
            let mut quote_positions = Vec::new();
            for position in positions {
                match prepared.frames()[position.0].inputs[position.1]
                    .observation
                    .key
                    .kind
                {
                    EventKind::Trade => trade_positions.push(position),
                    EventKind::Quote => quote_positions.push(position),
                }
            }
            Ok(SelectedIndex {
                scope: scope.into(),
                plan_hash: input.plan.evidence_hash().into(),
                prepared_hash: prepared.hash().into(),
                trade_positions,
                quote_positions,
            })
        };
        if workers == 1 || inputs.len() == 1 {
            return inputs.iter().map(compute).collect();
        }
        let chunk = inputs.len().div_ceil(workers.min(inputs.len()));
        thread::scope(|thread_scope| {
            let handles: Vec<_> = inputs
                .chunks(chunk)
                .map(|shard| {
                    let compute = &compute;
                    thread_scope
                        .spawn(move || shard.iter().map(compute).collect::<Result<Vec<_>>>())
                })
                .collect();
            let mut output = Vec::with_capacity(inputs.len());
            let mut first_error = None;
            for handle in handles {
                match handle.join() {
                    Ok(Ok(shard)) if first_error.is_none() => output.extend(shard),
                    Ok(Ok(_)) => {}
                    Ok(Err(error)) if first_error.is_none() => first_error = Some(error),
                    Ok(Err(_)) => {}
                    Err(_) if first_error.is_none() => {
                        first_error = Some(Error::Unready(
                            "historical selected index worker panicked".into(),
                        ))
                    }
                    Err(_) => {}
                }
            }
            first_error.map_or(Ok(output), Err)
        })
    }
    pub fn new(mut projections: Vec<&'a Projection>) -> Result<Self> {
        if projections.is_empty() || projections.len() > 100_000 {
            return Err(Error::Capacity("historical projection bundle size".into()));
        }
        projections.sort_by_key(|projection| {
            let scope = projection.prepared.scope();
            key(scope.provider, scope.instrument, scope.session)
        });
        let mut entries = Vec::with_capacity(projections.len());
        let mut shards = Vec::with_capacity(projections.len());
        for projection in &projections {
            let scope = projection.prepared.scope();
            let local = &projection.catalog;
            let manifest = &projection.manifest;
            let manifest_hash = manifest.hash()?;
            if manifest.version != 1
                || manifest.session != scope.session
                || manifest.policy.delay_ns == 0
                || manifest.policy.delay_ns > 1_000_000_000
                || !hash_ok(&manifest.trade_certificate)
                || !hash_ok(&manifest.quote_certificate)
                || manifest.trade_certificate == manifest.quote_certificate
                || !hash_ok(&manifest.eligibility_policy)
                || !hash_ok(&manifest.eligibility_decisions)
                || local.schema_version != 1
                || local.clock != Clock::Historical
                || local.authority_manifest_hash != manifest_hash
                || local.shards.len() != 1
            {
                return Err(Error::Conflict(
                    "historical projection shard authority".into(),
                ));
            }
            local.hash()?;
            let shard = &local.shards[0];
            if key(shard.provider, shard.instrument, shard.session)
                != key(scope.provider, scope.instrument, scope.session)
                || shard.prepared_hash != projection.prepared.hash()
            {
                return Err(Error::Conflict(
                    "historical projection prepared shard".into(),
                ));
            }
            entries.push(Entry {
                provider: scope.provider,
                instrument: scope.instrument,
                session: scope.session,
                projection_manifest_hash: manifest_hash,
            });
            shards.push(Shard {
                provider: shard.provider,
                instrument: shard.instrument,
                session: shard.session,
                prepared_hash: shard.prepared_hash.clone(),
                clock_model: shard.clock_model.clone(),
            });
        }
        let manifest = Manifest {
            schema_version: 1,
            entries,
        };
        let catalog = Catalog {
            schema_version: 1,
            authority_manifest_hash: manifest.hash()?,
            clock: Clock::Historical,
            shards,
        };
        catalog.hash()?;
        Ok(Self {
            projections,
            manifest,
            catalog,
        })
    }
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }
    pub fn catalog(&self) -> &Catalog {
        &self.catalog
    }
    /// A run manifest must pin this combined catalog. One shard cannot claim
    /// the source identity of the whole run, even when its own data are valid.
    pub fn bind_compact_bars(
        &self,
        bars: &Bars,
        coverage: &BarCoverage,
        run: &Pinned,
        source_as_of_ns: u64,
    ) -> Result<HistoricalSource<'_>> {
        let request = bars.request();
        if request.instruments.len() != 1 || run.manifest().mode != Mode::Backtest {
            return Err(Error::Conflict(
                "historical bundle bar scope or run mode".into(),
            ));
        }
        let identity = key(request.provider, request.instruments[0], request.session);
        let index = self
            .manifest
            .entries
            .binary_search_by_key(&identity, |entry| {
                key(entry.provider, entry.instrument, entry.session)
            })
            .map_err(|_| Error::Unready("historical bundle shard missing".into()))?;
        let projection = self.projections[index];
        if self.catalog.authority_manifest_hash != self.manifest.hash()?
            || self.catalog.hash()? != run.manifest().source_manifest_hash
            || self.manifest.entries[index].projection_manifest_hash
                != projection.manifest.hash()?
        {
            return Err(Error::Conflict(
                "historical bundle run source pin differs".into(),
            ));
        }
        require_compact_bar_source(projection, bars, coverage, source_as_of_ns)?;
        self.catalog.bind_historical(run, &projection.prepared)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replay_sources::projection::bar_link::tests::fixture_for;

    #[test]
    fn combined_catalog_pins_each_projection_and_binds_two_bar_sources() {
        let (first, first_bars, first_coverage, single_run) = fixture_for(10, "a");
        let (second, second_bars, second_coverage, _) = fixture_for(20, "9");
        let bundle = Bundle::new(vec![&second, &first]).unwrap();
        assert_eq!(bundle.manifest().entries.len(), 2);
        assert_eq!(bundle.manifest().entries[0].instrument, 10);
        assert_eq!(bundle.manifest().entries[1].instrument, 20);
        let mut manifest = single_run.manifest().clone();
        manifest.source_manifest_hash = bundle.catalog().hash().unwrap();
        let mut consumer = manifest.consumers[0].clone();
        consumer.account = "other-account".into();
        consumer.instrument = 20;
        manifest.consumers.push(consumer);
        let run = Pinned::new(manifest.clone(), &manifest.hash().unwrap()).unwrap();
        let bound_first = bundle
            .bind_compact_bars(&first_bars, &first_coverage, &run, 2_000_000_000)
            .unwrap();
        let bound_second = bundle
            .bind_compact_bars(&second_bars, &second_coverage, &run, 2_000_000_000)
            .unwrap();
        assert_eq!(bound_first.manifest_hash(), run.hash());
        assert_eq!(bound_second.manifest_hash(), run.hash());
        assert_ne!(
            bound_first.prepared().hash(),
            bound_second.prepared().hash()
        );
        assert!(bundle
            .bind_compact_bars(&first_bars, &first_coverage, &single_run, 2_000_000_000)
            .is_err());
        assert!(bundle
            .bind_compact_bars(&first_bars, &second_coverage, &run, 2_000_000_000)
            .is_err());
        assert!(Bundle::new(vec![&first, &first]).is_err());
        let (mut changed, _, _, _) = fixture_for(30, "8");
        changed.manifest.trade_certificate = "0".repeat(64);
        assert!(Bundle::new(vec![&first, &changed]).is_err());
    }

    #[test]
    fn selected_index_rejects_missing_screened_shards_and_unbounded_limits() {
        let (projection, _, _, run) = fixture_for(10, "a");
        let bundle = Bundle::new(vec![&projection]).unwrap();
        assert!(bundle.index_selected(vec![], &run, 1, 1, 1).is_err());
        assert!(bundle.index_selected(vec![], &run, 1, 0, 1).is_err());
        assert!(bundle.index_selected(vec![], &run, 1, 1, 0).is_err());
        assert!(bundle.index_selected(vec![], &run, 0, 1, 1).is_err());
    }
}
