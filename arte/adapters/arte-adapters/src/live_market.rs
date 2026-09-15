//! In-process audited feed to ordered market/V7 calculations. No service startup.
use crate::live_decode::AuditedEvent;
use arte_core::{
    candidate_features,
    events::EventKind,
    exposure::Check,
    market::Series,
    market_structure::scheduler::{Boundary, Scheduler},
    v7_stream::Level,
    Error, Result,
};
use std::collections::BTreeMap;
/// Both channels must progress. This conservative frontier can delay a quiet
/// instrument. It is an explicit lateness assumption, not provider completeness.
pub struct Lane {
    market: Scheduler,
    high: BTreeMap<EventKind, u64>,
    allowed_lateness_ns: u64,
    failed: bool,
    quotes: arte_core::quote_state::Book,
    maximum_quote_age_ns: u64,
    features: candidate_features::State,
}
impl Lane {
    pub fn new(
        market: Scheduler,
        allowed_lateness_ns: u64,
        maximum_quote_age_ns: u64,
        feature_config: candidate_features::Config,
    ) -> Result<Self> {
        if allowed_lateness_ns == 0
            || allowed_lateness_ns > 1_000_000_000
            || maximum_quote_age_ns == 0
        {
            return Err(Error::Invalid(
                "live ordering allowance must be in (0, 1 second]".into(),
            ));
        }
        Ok(Self {
            features: candidate_features::State::new(market.state()?, feature_config)?,
            quotes: arte_core::quote_state::Book::new(market.scope())?,
            maximum_quote_age_ns,
            market,
            high: BTreeMap::new(),
            allowed_lateness_ns,
            failed: false,
        })
    }
    fn available(&self) -> Result<()> {
        if self.failed {
            return Err(Error::Unready("live market lane requires recovery".into()));
        }
        Ok(())
    }
    /// Persist/audit every observation independently, including duplicate/rejected
    /// input. Eligibility comes from the pinned trade-condition policy, not health.
    pub fn ingest(&mut self, event: &AuditedEvent, eligible: bool) -> Result<()> {
        self.available()?;
        let result = (|| {
            let observation = &event.observation;
            observation.validate()?;
            let scope = self.market.scope();
            if observation.key.provider != scope.provider
                || observation.key.instrument != scope.instrument
                || observation.key.session != scope.session
                || observation.receipt.is_none()
            {
                return Err(Error::Conflict(
                    "live market source scope or receipt missing".into(),
                ));
            }
            if observation.key.kind == EventKind::Trade {
                self.market.enqueue(observation, eligible)?;
            } else {
                self.quotes.observe(observation)?;
            }
            if event.exposure_permitted {
                self.high
                    .entry(observation.key.kind)
                    .and_modify(|at| *at = (*at).max(observation.sip.ns))
                    .or_insert(observation.sip.ns);
            }
            Ok(())
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    /// Borrow the current shared feed gate at release time. No cached permission.
    pub fn prepare_next(&mut self, check: Check<'_>, processed_at_ns: u64) -> Result<bool> {
        self.available()?;
        check.require(self.market.scope().instrument)?;
        if self.market.pending()?.is_some() {
            return Err(Error::Unready(
                "live boundary awaits consumer acknowledgment".into(),
            ));
        }
        let next = frontier(
            &self.high,
            self.allowed_lateness_ns,
            self.market.watermark_ns(),
        )?;
        let result = (|| {
            let prepared = self.market.prepare_next(next, processed_at_ns)?;
            if prepared {
                let boundary = self
                    .market
                    .pending()?
                    .ok_or_else(|| Error::Conflict("prepared live boundary missing".into()))?;
                self.features.observe(&boundary, self.market.state()?)?;
            }
            Ok(prepared)
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    /// Pending boundaries remain readable when feed exposure is blocked. A caller
    /// must still recheck current order safety before any exposure increase.
    pub fn pending_boundary(&self) -> Result<Option<Boundary<'_>>> {
        self.available()?;
        self.market.pending()
    }
    pub fn candidate_features(&self) -> Result<Option<&candidate_features::Snapshot>> {
        self.available()?;
        self.features.snapshot()
    }
    pub fn entry_frame<'a>(
        &'a self,
        context: candidate_features::EntryContext<'a>,
        evaluated_at_ns: u64,
    ) -> Result<arte_core::strategy_entry::Frame<'a>> {
        self.available()?;
        let boundary = self
            .market
            .pending()?
            .ok_or_else(|| Error::Unready("live entry boundary missing".into()))?;
        self.features.entry_frame(
            &boundary,
            evaluated_at_ns,
            self.market.state()?,
            &self.quotes,
            context,
        )
    }
    pub fn candidate_configuration_hash(
        &self,
        policy: &arte_core::strategy_candidate::Policy<'_>,
        intrabar: &arte_core::strategy_candidate::AcquisitionPolicy,
        recovery: &arte_core::strategy_lifecycle::RecoveryPolicy,
    ) -> Result<String> {
        self.available()?;
        arte_core::candidate_runtime::configuration_hash(policy, intrabar, &self.features, recovery)
    }
    /// Prepare an intent/journal batch, not an order. The caller must commit that
    /// batch before advancing account state or acknowledging the market boundary.
    /// Emergency exits must remain available outside entry-frame readiness.
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_completed_candidate(
        &self,
        check: Check<'_>,
        evaluated_at_ns: u64,
        candidate: &mut arte_core::candidate_runtime::Runtime,
        context: candidate_features::EntryContext<'_>,
        safety: &arte_core::strategy_dispatch::Safety,
        broker: &arte_core::strategy_candidate::PositionObservation,
        gates: &arte_core::strategy_adds::Gates,
        policy: &arte_core::strategy_candidate::Policy<'_>,
        intrabar: &arte_core::strategy_candidate::AcquisitionPolicy,
    ) -> Result<arte_core::strategy_dispatch::Decision> {
        self.available()?;
        check.require(self.market.scope().instrument)?;
        let boundary = self
            .market
            .pending()?
            .ok_or_else(|| Error::Unready("live candidate boundary missing".into()))?;
        let frame = self.entry_frame(context, evaluated_at_ns)?;
        let mut input = boundary.input(String::new());
        input.evaluated_at_ns = evaluated_at_ns;
        candidate.completed(
            input,
            safety,
            &frame,
            broker,
            gates,
            policy,
            intrabar,
            &self.features,
        )
    }
    pub fn acknowledge_boundary(&mut self, id: &str) -> Result<()> {
        self.available()?;
        self.market.acknowledge(id)
    }
    pub fn transport_lost(&mut self) {
        self.high.clear();
        self.failed = true;
    }
    pub fn market(&self) -> Result<&Series> {
        self.available()?;
        self.market.state()?.market()
    }
    pub fn timeframe(&self, interval_ns: u64) -> Result<&Series> {
        self.available()?;
        self.market.state()?.timeframe(interval_ns)
    }
    pub fn levels(&self) -> Result<impl Iterator<Item = &Level>> {
        self.available()?;
        self.market.state()?.levels()
    }
    pub fn prior_strategy_levels(
        &self,
    ) -> Result<(u64, &[arte_core::strategy_targets::TargetLevel])> {
        self.available()?;
        self.market.state()?.prior_strategy_levels()
    }
    pub fn strategy_levels(
        &self,
        at_ns: u64,
        maximum: usize,
    ) -> Result<Vec<arte_core::strategy_targets::TargetLevel>> {
        self.available()?;
        self.market.state()?.strategy_levels(at_ns, maximum)
    }
    pub fn executable_quote(
        &self,
        check: Check<'_>,
        now_ns: u64,
    ) -> Result<&arte_core::events::Observation> {
        self.available()?;
        check.require(self.market.scope().instrument)?;
        self.quotes
            .require_executable(now_ns, self.maximum_quote_age_ns)
    }
}
fn frontier(high: &BTreeMap<EventKind, u64>, allowance: u64, previous: u64) -> Result<u64> {
    let trade = high
        .get(&EventKind::Trade)
        .ok_or_else(|| Error::Unready("live trade frontier missing".into()))?;
    let quote = high
        .get(&EventKind::Quote)
        .ok_or_else(|| Error::Unready("live quote frontier missing".into()))?;
    let next = (*trade)
        .min(*quote)
        .checked_sub(allowance)
        .ok_or_else(|| Error::Invalid("live frontier precedes epoch".into()))?;
    Ok(previous.max(next))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn frontier_needs_both_channels_and_never_advances_from_silence() {
        let mut high = BTreeMap::new();
        high.insert(EventKind::Trade, 100);
        assert!(frontier(&high, 10, 0).is_err());
        high.insert(EventKind::Quote, 120);
        assert_eq!(frontier(&high, 10, 0).unwrap(), 90);
        assert_eq!(frontier(&high, 10, 95).unwrap(), 95);
        high.insert(EventKind::Trade, 150);
        assert_eq!(frontier(&high, 10, 95).unwrap(), 110);
    }
    #[test]
    fn live_release_obeys_gate_and_requires_each_boundary_acknowledgment() {
        use arte_core::{
            events::{Decimal, EventKey, Observation, Payload, SourceTime},
            exposure::Gate,
            market_structure::{Config, Ordered, Runtime},
            v7_extraction::Candle,
            v7_seed::{build, input_hash, SeedPolicy, SourceCertificate, SplitAdjustment},
            v7_stream::StreamPolicy,
        };
        const SECOND: u64 = 1_000_000_000;
        let bars: Vec<_> = (100..118)
            .map(|t| Candle {
                t,
                open: 10.,
                high: 11.,
                low: 9.,
                close: 10.,
                volume: 1.,
            })
            .collect();
        let seed = build(
            &bars,
            &[],
            SourceCertificate {
                instrument: 1,
                ticker: "TEST".into(),
                session: 20260914,
                start_second: 100,
                end_second: 120,
                source_generation: "offline".into(),
                input_hash: input_hash(&bars, &[]).unwrap(),
                certified_at_second: 121,
            },
            None,
            &SeedPolicy::default(),
            &SplitAdjustment::default(),
            121,
        )
        .unwrap();
        let runtime = Runtime::new(
            &seed,
            Config {
                provider: 1,
                instrument: 1,
                session: 20260915,
                start_second: 200,
                end_second: 300,
                macd_periods: (2, 3, 2),
                maximum_bars: 100,
                maximum_market_events: 100,
                additional_timeframes: vec![arte_core::market_structure::Timeframe {
                    interval_ns: 5 * SECOND,
                    macd_periods: (12, 26, 9),
                    maximum_bars: 20,
                }],
                structure: StreamPolicy {
                    input_generation: "offline-live-lane".into(),
                    ..StreamPolicy::default()
                },
            },
            &SplitAdjustment::default(),
        )
        .unwrap();
        let scheduler = Scheduler::new(
            Ordered::new(runtime, 10).unwrap(),
            "offline-live-lane".into(),
        )
        .unwrap();
        let mut lane = Lane::new(
            scheduler,
            SECOND / 10,
            SECOND,
            candidate_features::Config {
                setup: arte_core::strategy_setup::SetupSettings {
                    range_ns: 30 * SECOND,
                    minimum_bars: 1,
                    maximum_gap_ns: 0,
                },
                forming_macd: true,
                minimum_range_pct: 1.,
                minimum_progress_pct: 1.,
                maximum_quote_age_ns: SECOND,
                maximum_completed_bar_age_ns: SECOND,
                maximum_levels: 100,
            },
        )
        .unwrap();
        // This fixture exercises release and current-gate binding, not decoding or
        // ingestion. No real live receipt or provider health evidence is claimed.
        lane.market
            .enqueue(
                &Observation {
                    key: EventKey {
                        provider: 1,
                        instrument: 1,
                        session: 20260915,
                        kind: EventKind::Trade,
                        sequence: 1,
                    },
                    payload: Payload::Trade {
                        price: Decimal {
                            atoms: 10,
                            scale: 0,
                        },
                        size: Decimal { atoms: 1, scale: 0 },
                        exchange: 1,
                        trade_id: "1".into(),
                        trf: None,
                        conditions: vec![],
                        correction: None,
                    },
                    sip: SourceTime {
                        ns: 200 * SECOND + 1,
                        precision_ns: 1,
                    },
                    participant: None,
                    available_at_ns: 200 * SECOND + 2,
                    receipt: None,
                },
                true,
            )
            .unwrap();
        lane.high.insert(EventKind::Trade, 202 * SECOND);
        lane.high.insert(EventKind::Quote, 202 * SECOND);
        let mut gate = Gate::new(100, 2).unwrap();
        assert!(lane.prepare_next(gate.at(1), 202 * SECOND).is_err());
        assert!(lane.pending_boundary().unwrap().is_none());
        gate.transport(true);
        gate.update(1, EventKind::Trade, 1, true).unwrap();
        gate.update(1, EventKind::Quote, 1, true).unwrap();
        assert!(lane.prepare_next(gate.at(1), 202 * SECOND).unwrap());
        let trade_id = lane.pending_boundary().unwrap().unwrap().id.to_owned();
        assert!(lane.prepare_next(gate.at(1), 202 * SECOND).is_err());
        assert_eq!(lane.market().unwrap().developing().unwrap().trades, 1);
        lane.acknowledge_boundary(&trade_id).unwrap();
        assert!(lane.prepare_next(gate.at(101), 202 * SECOND).is_err());
        assert!(lane.pending_boundary().unwrap().is_none());
        gate.update(1, EventKind::Trade, 101, true).unwrap();
        gate.update(1, EventKind::Quote, 101, true).unwrap();
        assert!(lane.prepare_next(gate.at(101), 202 * SECOND).unwrap());
        let boundary = lane.pending_boundary().unwrap().unwrap();
        assert!(matches!(
            boundary.kind,
            arte_core::market_structure::scheduler::Kind::Completed { .. }
        ));
        let bar_id = boundary.id.to_owned();
        let features = lane.candidate_features().unwrap().unwrap();
        assert_eq!(features.boundary_id, bar_id);
        assert_eq!(features.one_second.as_ref().unwrap().vwap, 10.);
        assert_eq!(
            features.macd.as_ref().unwrap().kind,
            arte_core::strategy_macd::Kind::Unavailable
        );
        lane.acknowledge_boundary(&bar_id).unwrap();
        assert!(!lane.prepare_next(gate.at(101), 202 * SECOND).unwrap());
    }
}
