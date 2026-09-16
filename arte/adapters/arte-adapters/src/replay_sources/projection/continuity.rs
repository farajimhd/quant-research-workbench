//! Explicit historical-only clock projection of verified empty-trade evidence.
//! Source publication time remains factual; modeled availability never replaces it.
use super::*;
use arte_core::{
    acquisition::trade_seconds::EmptySpan, coverage::Interval, run_manifest::Pinned,
    strategy_dispatch::Mode,
};
#[derive(Clone, Serialize)]
pub struct Provenance {
    pub contract: String,
    pub run_manifest_hash: String,
    pub catalog_hash: String,
    pub source_certificate: String,
    pub source_published_at_ns: u64,
    pub source_knowledge_cutoff_ns: u64,
    pub source_empty_span_hash: String,
    pub interval: Interval,
    pub modeled_available_at_ns: u64,
}
pub struct HistoricalEmptySpan {
    source: EmptySpan,
    provenance: Provenance,
}
impl Projection {
    pub fn empty_trade_span(
        &self,
        trades: &Source,
        run: &Pinned,
        scope: Scope,
        interval: Interval,
        source_as_of_ns: u64,
    ) -> Result<HistoricalEmptySpan> {
        if run.manifest().mode != Mode::Backtest
            || run.manifest().clock != Clock::Historical
            || self.manifest.version != 1
            || self.manifest.session != scope.session
            || self.manifest.trade_certificate != trades.certificate().id()?
            || self.catalog.authority_manifest_hash != self.manifest.hash()?
            || self.catalog.shards.len() != 1
            || self.catalog.shards[0].provider != scope.provider
            || self.catalog.shards[0].instrument != scope.instrument
            || self.catalog.shards[0].session != scope.session
            || self.manifest.policy.delay_ns == 0
            || self.manifest.policy.delay_ns > 1_000_000_000
        {
            return Err(Error::Conflict(
                "historical empty-span projection pins differ".into(),
            ));
        }
        self.catalog.require(run, &self.prepared)?;
        let source = trades.prove_empty_trade_seconds(interval, source_as_of_ns)?;
        source.require(scope, interval, source_as_of_ns)?;
        let modeled_available_at_ns = interval
            .end
            .checked_add(self.manifest.policy.delay_ns)
            .ok_or_else(|| Error::Invalid("empty-span modeled clock overflow".into()))?;
        let provenance = Provenance {
            contract: "arte.historical-empty-trade-span.v1".into(),
            run_manifest_hash: run.hash().into(),
            catalog_hash: self.catalog.hash()?,
            source_certificate: source.certificate_id().into(),
            source_published_at_ns: source.published_at_ns(),
            source_knowledge_cutoff_ns: source_as_of_ns,
            source_empty_span_hash: source.fingerprint().into(),
            interval,
            modeled_available_at_ns,
        };
        Ok(HistoricalEmptySpan { source, provenance })
    }
}
impl HistoricalEmptySpan {
    pub fn provenance(&self) -> &Provenance {
        &self.provenance
    }
    pub fn require(
        &self,
        run: &Pinned,
        scope: Scope,
        interval: Interval,
        evaluated_at_ns: u64,
    ) -> Result<()> {
        if run.hash() != self.provenance.run_manifest_hash
            || run.manifest().mode != Mode::Backtest
            || run.manifest().clock != Clock::Historical
            || evaluated_at_ns < self.provenance.modeled_available_at_ns
        {
            return Err(Error::Conflict(
                "historical empty-span run or modeled clock differs".into(),
            ));
        }
        self.source
            .require(scope, interval, self.provenance.source_knowledge_cutoff_ns)
    }
}
