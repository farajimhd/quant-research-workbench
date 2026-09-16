//! Fresh historical initialization. Recovery and live activation are separate.
use super::{document, market, Session};
use crate::replay_sources::startup;
use arte_core::{
    config::Acceptance, content_hash, market_structure::scheduler::playback::accounts::Run,
    run_manifest::Pinned, seed_storage::Bundle, v7_seed::HistoricalSeed, Error, Result,
};
use std::{collections::BTreeSet, future::Future};
#[cfg(test)]
mod tests;

pub trait Reader: startup::Reader {
    fn seed(
        &self,
        id: &str,
        instrument: u64,
        session: u32,
        start_ns: u64,
    ) -> impl Future<Output = Result<HistoricalSeed>> + Send;
}
impl Reader for crate::clickhouse::ClickHouse {
    async fn seed(
        &self,
        id: &str,
        instrument: u64,
        session: u32,
        start_ns: u64,
    ) -> Result<HistoricalSeed> {
        self.load_seed(id, instrument, session, start_ns).await
    }
}
pub struct Request<'a> {
    pub manifest: &'a Pinned,
    pub market: market::Document,
    pub market_hash: &'a str,
    pub source: startup::Request<'a>,
    pub limits: startup::Limits,
    pub seed_id: &'a str,
}
/// Read-only prepared input. This is not a persistence or activation receipt.
pub struct Prepared {
    pub run: Run,
    pub input: startup::Input,
    pub seed: Bundle,
}
pub struct Initialized {
    pub session: Session,
    pub input: startup::Input,
    pub seed: Bundle,
}
/// Use independently pinned planning identities. Never regenerate a changed run
/// manifest to make newly loaded data fit. No source or seed is selected implicitly.
pub async fn prepare(reader: &impl Reader, request: Request<'_>) -> Result<Prepared> {
    let c = &request.market.configuration;
    let nanos = |s: u64| {
        s.checked_mul(1_000_000_000)
            .ok_or_else(|| Error::Invalid("bootstrap clock overflow".into()))
    };
    if request.market.hash()? != request.market_hash
        || request.manifest.manifest().mode != arte_core::strategy_dispatch::Mode::Backtest
        || request.manifest.manifest().clock != arte_core::run_manifest::Clock::Historical
        || request.market.manifest_hash != request.manifest.hash()
        || request.source.scope.provider != c.provider
        || request.source.scope.instrument != c.instrument
        || request.source.scope.session != c.session
        || request.source.interval.start != nanos(c.start_second)?
        || request.source.interval.end != nanos(c.end_second)?
        || request.seed_id.len() != 64
        || !request
            .seed_id
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(Error::Conflict(
            "backtest bootstrap pins or source domain differ".into(),
        ));
    }
    let seed = reader
        .seed(
            request.seed_id,
            c.instrument,
            c.session,
            request.source.interval.start,
        )
        .await?;
    if seed.hash != request.seed_id {
        return Err(Error::Conflict(
            "backtest bootstrap seed identity differs".into(),
        ));
    }
    seed.require_available(c.instrument, c.session, c.start_second)?;
    let seed = Bundle::from_seed(&seed)?;
    if content_hash(&seed.manifest)? != request.manifest.manifest().seed_manifest_hash {
        return Err(Error::Conflict(
            "backtest bootstrap seed manifest differs".into(),
        ));
    }
    let input = startup::load(reader, request.source, request.limits).await?;
    let run = request.market.assemble(
        request.market_hash,
        request.manifest,
        &input.projection.catalog,
        input.projection.prepared.clone(),
        &seed,
    )?;
    Ok(Prepared { run, input, seed })
}
impl crate::clickhouse::ClickHouse {
    /// Loads immutable evidence, validates the complete fresh session, publishes
    /// startup inputs, then returns it paused. No automatic resume or broker I/O.
    pub async fn initialize_backtest(
        &self,
        request: Request<'_>,
        document: &document::Document,
        expected: &str,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<Initialized> {
        for gate in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&gate) {
                return Err(Error::Unready(format!(
                    "backtest initialization acceptance missing: {gate:?}"
                )));
            }
        }
        let manifest = request.manifest;
        if document.hash()? != expected || document.manifest_hash != manifest.hash() {
            return Err(Error::Conflict(
                "backtest initialization startup document differs".into(),
            ));
        }
        lease.require(&crate::clickhouse::backtest_startup_scope(manifest)?)?;
        let prepared = prepare(self, request).await?;
        let session = self
            .create_backtest_session(prepared.run, manifest, document, expected, passed, lease)
            .await?;
        Ok(Initialized {
            session,
            input: prepared.input,
            seed: prepared.seed,
        })
    }
}
