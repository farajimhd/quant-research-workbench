//! Bind a verified compact bar product to the same certified historical trade
//! source used by a run-pinned full event replay. Screening never narrows replay.
use super::Projection;
use arte_core::{
    bar_catalogue::{Complete as Bars, Coverage as BarCoverage},
    market_structure::scheduler::playback::sources::HistoricalSource,
    run_manifest::Pinned,
    strategy_dispatch::Mode,
    Error, Result,
};

impl Projection {
    pub fn bind_compact_bars<'a>(
        &'a self,
        bars: &Bars,
        coverage: &BarCoverage,
        run: &Pinned,
        source_as_of_ns: u64,
    ) -> Result<HistoricalSource<'a>> {
        let request = bars.request();
        let scope = self.prepared.scope();
        let instrument = request.instruments.first().copied();
        if run.manifest().mode != Mode::Backtest
            || request.instruments.len() != 1
            || instrument != Some(scope.instrument)
            || request.provider != scope.provider
            || request.session != scope.session
            || self.manifest.session != scope.session
            || self.catalog.authority_manifest_hash != self.manifest.hash()?
            || request.source_generation != self.manifest.trade_certificate
            || coverage.hash()? != bars.coverage_hash()
            || coverage
                .sources
                .get(&scope.instrument)
                .is_none_or(|source| source.certificate_hash != self.manifest.trade_certificate)
        {
            return Err(Error::Conflict(
                "compact bars and historical replay trade authority differ".into(),
            ));
        }
        coverage.require(request, source_as_of_ns)?;
        self.prepared.require_interval(request.interval)?;
        self.catalog.bind_historical(run, &self.prepared)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::replay_sources::projection::{Manifest as ProjectionManifest, Policy};
    use arte_core::{
        bar_catalogue::{Batch, Column, Readback, Request, Source},
        coverage::Interval,
        event_order::Scope,
        execution_interval::ExecutionInterval,
        market_structure::scheduler::playback::{
            sources::{Catalog, Shard},
            Frame, Limits as PlaybackLimits, Prepared,
        },
        run_manifest::{Clock, Consumer, Execution, Manifest},
        strategy_dispatch::StrategyKind,
    };
    use std::collections::{BTreeMap, BTreeSet};

    fn fixture() -> (Projection, Bars, BarCoverage, Pinned) {
        let interval = Interval {
            start: 1_000_000_000,
            end: 1_300_000_000,
        };
        let scope = Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        };
        let projection_manifest = ProjectionManifest {
            version: 1,
            trade_certificate: "a".repeat(64),
            quote_certificate: "b".repeat(64),
            policy: Policy { delay_ns: 2 },
            eligibility_policy: "c".repeat(64),
            eligibility_decisions: "d".repeat(64),
            session: scope.session,
        };
        let prepared = Prepared::new(
            scope,
            "historical-modeled",
            vec![Frame {
                watermark_ns: interval.end,
                evaluated_at_ns: interval.end + 2,
                inputs: vec![],
            }],
            PlaybackLimits {
                maximum_frames: 10,
                maximum_events: 10,
                maximum_serialized_bytes: 4096,
            },
        )
        .unwrap();
        let catalog = Catalog {
            schema_version: 1,
            authority_manifest_hash: projection_manifest.hash().unwrap(),
            clock: Clock::Historical,
            shards: vec![Shard {
                provider: scope.provider,
                instrument: scope.instrument,
                session: scope.session,
                prepared_hash: prepared.hash().into(),
                clock_model: "historical-modeled".into(),
            }],
        };
        let manifest = Manifest {
            schema_version: 3,
            run_id: "bar-source-link-test".into(),
            mode: Mode::Backtest,
            code_release_hash: "e".repeat(64),
            source_manifest_hash: catalog.hash().unwrap(),
            reference_manifest_hash: "f".repeat(64),
            seed_manifest_hash: "1".repeat(64),
            algorithm_manifest_hash: "2".repeat(64),
            dependency_plan_hash: "3".repeat(64),
            hardware_profile_hash: "4".repeat(64),
            clock: Clock::Historical,
            execution: Execution::Simulated {
                fill_model_hash: "5".repeat(64),
                cost_model_hash: "6".repeat(64),
            },
            consumers: vec![Consumer {
                account: "account".into(),
                instrument: scope.instrument,
                strategy_instance: "strategy-350".into(),
                strategy_kind: StrategyKind::Strategy350,
                execution_interval: ExecutionInterval::Events,
                effective_config_hash: "7".repeat(64),
            }],
        };
        let run = Pinned::new(manifest.clone(), &manifest.hash().unwrap()).unwrap();
        let request = Request {
            provider: scope.provider,
            instruments: vec![scope.instrument],
            session: scope.session,
            interval,
            timeframe_ns: 100_000_000,
            source_generation: projection_manifest.trade_certificate.clone(),
            calculation_hash: "8".repeat(64),
            columns: BTreeSet::from([Column::Close]),
            maximum_rows: 3,
        };
        let coverage = BarCoverage {
            provider: scope.provider,
            session: scope.session,
            interval,
            timeframe_ns: request.timeframe_ns,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            sources: BTreeMap::from([(
                scope.instrument,
                Source {
                    certificate_hash: projection_manifest.trade_certificate.clone(),
                    price_scale: 2,
                    size_scale: 0,
                },
            )]),
            published_at_ns: 2_000_000_000,
        };
        let mut readback = Readback::new(request.clone(), &coverage, 2_000_000_000).unwrap();
        readback
            .observe(Batch {
                request_hash: request.hash().unwrap(),
                coverage_hash: coverage.hash().unwrap(),
                instrument: scope.instrument,
                first_start_ns: interval.start,
                count: 3,
                price_scale: 2,
                size_scale: 0,
                present: vec![false; 3],
                open: None,
                high: None,
                low: None,
                close: Some(vec![0; 3]),
                volume: None,
                notional: None,
                trades: None,
            })
            .unwrap();
        (
            Projection {
                prepared,
                catalog,
                manifest: projection_manifest,
            },
            readback.finish().unwrap(),
            coverage,
            run,
        )
    }

    #[test]
    fn compact_bar_source_must_be_same_certified_trade_revision() {
        let (projection, bars, coverage, run) = fixture();
        let bound = projection
            .bind_compact_bars(&bars, &coverage, &run, 2_000_000_000)
            .unwrap();
        assert_eq!(bound.prepared().hash(), projection.prepared.hash());
        assert!(projection
            .bind_compact_bars(&bars, &coverage, &run, 1_999_999_999)
            .is_err());
        let mut wrong_coverage = coverage.clone();
        wrong_coverage
            .sources
            .get_mut(&10)
            .unwrap()
            .certificate_hash = "0".repeat(64);
        assert!(projection
            .bind_compact_bars(&bars, &wrong_coverage, &run, 2_000_000_000)
            .is_err());
        let (mut changed_projection, _, _, _) = fixture();
        changed_projection.manifest.trade_certificate = "0".repeat(64);
        assert!(changed_projection
            .bind_compact_bars(&bars, &coverage, &run, 2_000_000_000)
            .is_err());
    }
}
