//! Deterministic cross-ticker barrier over independent account playbacks.
//! Every ticker retains its complete market/V7 tape and account journal gate.
use super::Run;
use crate::{
    market_structure::scheduler::playback::{sources::Catalog, Poll},
    run_manifest::Pinned,
    strategy_transaction::Committed,
    Error, Result,
};
use std::collections::BTreeSet;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MultiPoll {
    Paused,
    Yield,
    Boundary { shard: usize },
    Complete,
}

pub struct MultiRun {
    runs: Vec<Run>,
    selected: Option<(usize, String)>,
}

impl MultiRun {
    /// All source shards and all account consumers must belong to one pinned
    /// historical run. The caller constructs each Run through its source gate.
    pub fn new(manifest: &Pinned, catalog: &Catalog, mut runs: Vec<Run>) -> Result<Self> {
        if runs.is_empty() || runs.len() > 100_000 || runs.len() != catalog.shards.len() {
            return Err(Error::Capacity("multi-ticker playback shard budget".into()));
        }
        if catalog.clock != crate::run_manifest::Clock::Historical
            || catalog.hash()? != manifest.manifest().source_manifest_hash
        {
            return Err(Error::Conflict("multi-ticker run source pin".into()));
        }
        let mut keyed = runs
            .drain(..)
            .map(|run| {
                let scope = run.market_scope();
                Ok(((scope.provider, scope.instrument, scope.session), run))
            })
            .collect::<Result<Vec<_>>>()?;
        keyed.sort_by_key(|(key, _)| *key);
        let runs = keyed.into_iter().map(|(_, run)| run).collect::<Vec<_>>();
        let mut seen = BTreeSet::new();
        for (run, shard) in runs.iter().zip(&catalog.shards) {
            let scope = run.market_scope();
            let key = (scope.provider, scope.instrument, scope.session);
            if !seen.insert(key)
                || key != (shard.provider, shard.instrument, shard.session)
                || run.prepared_hash() != shard.prepared_hash
                || run.run_id() != manifest.manifest().run_id
                || run.manifest_hash() != manifest.hash()
            {
                return Err(Error::Conflict("multi-ticker playback source set".into()));
            }
        }
        let instruments = catalog
            .shards
            .iter()
            .map(|shard| shard.instrument)
            .collect::<BTreeSet<_>>();
        if manifest
            .manifest()
            .consumers
            .iter()
            .any(|consumer| !instruments.contains(&consumer.instrument))
        {
            return Err(Error::Conflict(
                "multi-ticker playback consumer lacks source".into(),
            ));
        }
        Ok(Self {
            runs,
            selected: None,
        })
    }

    pub fn shard_count(&self) -> usize {
        self.runs.len()
    }

    #[cfg(test)]
    pub(crate) fn runs(&self) -> &[Run] {
        &self.runs
    }

    pub fn selected(&self) -> Result<Option<(usize, &Run)>> {
        let Some((index, id)) = &self.selected else {
            return Ok(None);
        };
        let run = &self.runs[*index];
        if run.pending()?.is_none_or(|boundary| boundary.id != id) {
            return Err(Error::Conflict(
                "multi-ticker selected boundary changed".into(),
            ));
        }
        Ok(Some((*index, run)))
    }

    pub fn resume_all(&mut self) -> Result<()> {
        if self.selected.is_some() {
            return Err(Error::Unready(
                "multi-ticker boundary awaits journal".into(),
            ));
        }
        for run in &mut self.runs {
            if run.status().mode == super::super::Mode::Paused {
                run.resume()?;
            }
        }
        Ok(())
    }

    /// A pending boundary cannot be released until every other shard is also
    /// pending or complete; otherwise a still-loading shard might have an
    /// earlier decision. Ties use source scope and local boundary sequence.
    pub fn poll(&mut self) -> Result<MultiPoll> {
        if let Some((index, _)) = &self.selected {
            self.selected()?;
            return Ok(MultiPoll::Boundary { shard: *index });
        }
        let mut paused = false;
        let mut yielding = false;
        let mut complete = 0usize;
        for run in &mut self.runs {
            match run.poll()? {
                Poll::Paused => paused = true,
                Poll::Yield => yielding = true,
                Poll::Complete => complete += 1,
                Poll::Boundary => {}
            }
        }
        if paused {
            return Ok(MultiPoll::Paused);
        }
        if yielding {
            return Ok(MultiPoll::Yield);
        }
        if complete == self.runs.len() {
            return Ok(MultiPoll::Complete);
        }
        let mut head = None;
        for (index, run) in self.runs.iter().enumerate() {
            let Some(boundary) = run.pending()? else {
                continue;
            };
            let scope = run.market_scope();
            let order = (
                boundary.evaluated_at_ns,
                boundary.input(String::new()).event_time_ns,
                scope.provider,
                scope.instrument,
                scope.session,
                boundary.sequence,
            );
            if head.as_ref().is_none_or(|(prior, _, _)| order < *prior) {
                head = Some((order, index, boundary.id.to_owned()));
            }
        }
        let (_, index, id) = head.ok_or_else(|| {
            Error::Conflict("multi-ticker playback has no pending boundary".into())
        })?;
        self.selected = Some((index, id));
        Ok(MultiPoll::Boundary { shard: index })
    }

    pub fn record_selected(&mut self, committed: &Committed) -> Result<bool> {
        let (index, _) = self
            .selected
            .as_ref()
            .ok_or_else(|| Error::Unready("multi-ticker boundary absent".into()))?;
        self.runs[*index].record(committed)
    }

    pub fn acknowledge_selected(&mut self) -> Result<()> {
        let (index, _) = self
            .selected
            .as_ref()
            .ok_or_else(|| Error::Unready("multi-ticker boundary absent".into()))?;
        self.runs[*index].acknowledge()?;
        self.selected = None;
        Ok(())
    }
}
