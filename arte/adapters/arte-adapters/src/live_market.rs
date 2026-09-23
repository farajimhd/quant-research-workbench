//! In-process audited feed to ordered market/V7 calculations. No service startup.
use crate::live_decode::AuditedEvent;
use crate::live_exact_signal;
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
pub mod recovery;
/// Both channels must progress. This conservative frontier can delay a quiet
/// instrument. It is an explicit lateness assumption, not provider completeness.
pub struct Lane {
    market: Scheduler,
    high: BTreeMap<EventKind, u64>,
    allowed_lateness_ns: u64,
    failed: bool,
    quotes: arte_core::quote_state::Book,
    bands: arte_core::luld::book::Book,
    maximum_quote_age_ns: u64,
    features: candidate_features::State,
    trade_policy: Option<std::sync::Arc<arte_core::trade_eligibility::Pinned>>,
    exact_signal: Option<live_exact_signal::Owner>,
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
            bands: arte_core::luld::book::Book::new(market.scope())?,
            maximum_quote_age_ns,
            market,
            trade_policy: None,
            exact_signal: None,
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
    pub fn bind_quote_policy(
        &mut self,
        policy: arte_core::quote_state::eligibility::Pinned,
    ) -> Result<()> {
        self.available()?;
        self.quotes.bind_policy(policy)
    }
    pub fn bind_shared_quote_policy(
        &mut self,
        policy: std::sync::Arc<arte_core::quote_state::eligibility::Pinned>,
    ) -> Result<()> {
        self.available()?;
        self.quotes.bind_shared_policy(policy)
    }
    pub fn bind_trade_policy(
        &mut self,
        policy: std::sync::Arc<arte_core::trade_eligibility::Pinned>,
    ) -> Result<()> {
        self.available()?;
        if policy.provider() != self.market.scope().provider
            || self
                .trade_policy
                .as_ref()
                .is_some_and(|old| old.hash() != policy.hash())
        {
            return Err(Error::Conflict("live trade policy binding differs".into()));
        }
        self.trade_policy = Some(policy);
        Ok(())
    }
    /// Bind before the first released boundary. A later bind could omit the
    /// first same-session squeeze occurrence and is rejected.
    pub fn bind_exact_signal(&mut self, owner: live_exact_signal::Owner) -> Result<()> {
        self.available()?;
        let scope = self.market.scope();
        let source = owner.scope();
        if self.exact_signal.is_some()
            || self.market.sequence() != 0
            || self.market.pending()?.is_some()
            || (source.provider, source.instrument, source.session)
                != (scope.provider, scope.instrument, scope.session)
        {
            return Err(Error::Conflict("exact signal live binding differs".into()));
        }
        self.exact_signal = Some(owner);
        Ok(())
    }
    /// A restored signal owner may join only the same pending scheduler cut.
    /// The pending boundary can then be read again without advancing signal
    /// state. A fully acknowledged scheduler cut has no boundary identity and
    /// cannot be inferred from its sequence alone.
    pub fn bind_recovered_exact_signal(&mut self, owner: live_exact_signal::Owner) -> Result<()> {
        self.available()?;
        let scope = self.market.scope();
        let source = owner.scope();
        let pending = self.market.pending()?.ok_or_else(|| {
            Error::Unready("recovered exact signal requires pending boundary".into())
        })?;
        if self.exact_signal.is_some()
            || (source.provider, source.instrument, source.session)
                != (scope.provider, scope.instrument, scope.session)
            || owner.last_boundary() != (pending.sequence, Some(pending.id))
            || owner.last_evaluated_at_ns() != pending.evaluated_at_ns
        {
            return Err(Error::Conflict(
                "recovered exact signal boundary differs".into(),
            ));
        }
        self.exact_signal = Some(owner);
        Ok(())
    }
    pub fn first_squeeze_occurrence(
        &self,
    ) -> Result<Option<arte_core::strategy350_signal::Occurrence>> {
        self.available()?;
        self.exact_signal
            .as_ref()
            .map(|owner| owner.first_occurrence())
            .ok_or_else(|| Error::Unready("exact signal not bound".into()))
    }
    /// Exact integer Strategy 350 adaptive-distance evidence. This is not an
    /// order admission result; structural stop and bracket checks remain separate.
    pub fn adaptive_noise_distance(
        &self,
        entry_atoms: i64,
    ) -> Result<arte_core::strategy350_noise::Distance> {
        self.available()?;
        self.exact_signal
            .as_ref()
            .ok_or_else(|| Error::Unready("exact Strategy 350 owner not bound".into()))?
            .noise_distance(entry_atoms)
    }
    /// Current eligible trade's four-timeframe preview, after the exact owner
    /// consumed the same pending scheduler boundary. Never entry permission.
    pub fn forming_macd(&self) -> Result<arte_core::strategy350_macd::Outcome> {
        self.available()?;
        let boundary = self
            .market
            .pending()?
            .ok_or_else(|| Error::Unready("Strategy 350 MACD pending boundary missing".into()))?;
        self.exact_signal
            .as_ref()
            .ok_or_else(|| Error::Unready("exact Strategy 350 owner not bound".into()))?
            .forming_macd_for_boundary(&boundary)
    }
    pub fn forming_macd_evidence(&self) -> Result<arte_core::strategy350_macd::live::Evidence> {
        self.available()?;
        let boundary = self
            .market
            .pending()?
            .ok_or_else(|| Error::Unready("Strategy 350 live MACD boundary missing".into()))?;
        self.exact_signal
            .as_ref()
            .ok_or_else(|| Error::Unready("exact Strategy 350 owner not bound".into()))?
            .forming_macd_evidence_for_boundary(&boundary)
    }
    /// Persist/audit every observation independently, including duplicate/rejected
    /// input. Eligibility comes from the pinned trade-condition policy, not health.
    pub fn ingest(&mut self, event: &AuditedEvent) -> Result<()> {
        self.available()?;
        let result = (|| {
            let observation = &event.observation;
            observation.validate()?;
            let policy = self
                .trade_policy
                .as_ref()
                .ok_or_else(|| Error::Unready("live trade eligibility policy missing".into()))?;
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
                let eligible = policy.evaluate(observation, observation.available_at_ns)?;
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
                if let Some(owner) = &mut self.exact_signal {
                    owner.observe(&boundary)?;
                }
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
    pub fn completed_admission(
        &self,
        authority: candidate_features::AdmissionAuthorities,
    ) -> Result<arte_core::strategy_entry::Admission> {
        self.available()?;
        let boundary = self
            .market
            .pending()?
            .ok_or_else(|| Error::Unready("live admission boundary missing".into()))?;
        self.features.completed_admission(&boundary, authority)
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
        arte_core::candidate_runtime::configuration_hash(
            policy,
            intrabar,
            &self.features,
            recovery,
            self.quotes.policy_hash()?,
        )
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
        context: candidate_features::OwnedEntryContext<'_>,
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
        let frame = self.features.owned_entry_frame(
            &boundary,
            evaluated_at_ns,
            self.market.state()?,
            &self.quotes,
            context,
        )?;
        let mut input = boundary.input(String::new());
        input.evaluated_at_ns = evaluated_at_ns;
        let safety = self.features.restrict_safety(&input, safety)?;
        candidate.completed(
            input,
            &safety,
            &frame,
            broker,
            gates,
            policy,
            intrabar,
            &self.features,
        )
    }
    /// Freeze the complete configured consumer set before account workers run.
    pub fn account_boundary(
        &self,
        scopes: &[arte_core::strategy_dispatch::Scope],
        maximum_accounts: usize,
    ) -> Result<arte_core::account_boundary::Barrier> {
        self.available()?;
        if scopes
            .iter()
            .any(|scope| scope.instrument != self.market.scope().instrument)
        {
            return Err(Error::Conflict(
                "live account boundary instrument mismatch".into(),
            ));
        }
        let boundary = self
            .market
            .pending()?
            .ok_or_else(|| Error::Unready("live account boundary missing".into()))?;
        arte_core::account_boundary::Barrier::new(
            boundary.input(String::new()),
            scopes,
            maximum_accounts,
        )
    }
    pub fn acknowledge_accounts(
        &mut self,
        barrier: &mut arte_core::account_boundary::Barrier,
    ) -> Result<()> {
        self.available()?;
        let input = self
            .market
            .pending()?
            .ok_or_else(|| Error::Unready("live account boundary missing".into()))?
            .input(String::new());
        barrier.acknowledge_market(&input, |id| self.market.acknowledge(id))
    }
    /// Market-only acknowledgment. Strategy consumers must use acknowledge_accounts.
    pub fn acknowledge_boundary(&mut self, id: &str) -> Result<()> {
        self.available()?;
        self.market.acknowledge(id)
    }
    pub fn transport_lost(&mut self) {
        self.bands.invalidate();
        self.high.clear();
        self.failed = true;
    }
    /// Separate official-band input. Does not advance trade/quote watermarks or
    /// imply that the market provider adapter has certified this evidence.
    pub fn observe_luld(
        &mut self,
        evidence: &arte_core::luld::Evidence,
        received_at_ns: u64,
    ) -> Result<arte_core::luld::book::Update> {
        self.available()?;
        self.bands.observe(evidence, received_at_ns)
    }
    pub fn current_luld(
        &self,
        check: Check<'_>,
        now_ns: u64,
        scale: u8,
        maximum_age_ns: u64,
    ) -> Result<&arte_core::luld::Evidence> {
        self.available()?;
        check.require(self.market.scope().instrument)?;
        self.bands.require_current(now_ns, scale, maximum_age_ns)
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
    /// Session scheduling and certification of the pinned source remain upstream duties.
    /// Uses the same current quote/band owners as execution. No HTTP or floats.
    pub fn regular_admission(
        &self,
        check: Check<'_>,
        now_ns: u64,
        previous_close: Option<&arte_core::reference_data::PreviousClose>,
        previous_close_requirement: &arte_core::reference_data::PreviousCloseRequirement,
        policy: &arte_core::luld::Policy,
    ) -> Result<arte_core::luld::Admission> {
        previous_close_requirement.validate(self.market.scope())?;
        let previous_close = previous_close
            .map(|record| record.require(self.market.scope(), previous_close_requirement, now_ns))
            .transpose()?;
        let quote = self.executable_quote(check, now_ns)?;
        let arte_core::events::Payload::Quote { bid, ask, .. } = quote.payload else {
            return Err(Error::Conflict("quote owner returned a non-quote".into()));
        };
        let band = self.current_luld(check, now_ns, policy.scale, policy.maximum_age_ns)?;
        arte_core::luld::regular_admission(
            self.market.scope(),
            now_ns,
            previous_close
                .map(|price| price.atoms_at_scale(policy.scale))
                .transpose()?,
            bid.atoms_at_scale(policy.scale)?,
            ask.atoms_at_scale(policy.scale)?,
            Some(band),
            policy,
        )
    }
    /// Startup-loaded references only. This path performs no persistence reads.
    pub fn regular_admission_cached(
        &self,
        check: Check<'_>,
        now_ns: u64,
        session: &crate::calendar::PinnedSession,
        references: &crate::startup_references::Cache,
        policy: &arte_core::luld::Policy,
    ) -> Result<arte_core::luld::Admission> {
        session.require_regular(self.market.scope(), now_ns)?;
        let (record, requirement) = references.get(self.market.scope(), now_ns)?;
        if requirement.session != session.record().previous_trading_session {
            return Err(Error::Conflict(
                "cached previous close differs from active calendar".into(),
            ));
        }
        self.regular_admission(check, now_ns, Some(record), requirement, policy)
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
    fn feature_config() -> candidate_features::Config {
        const SECOND: u64 = 1_000_000_000;
        candidate_features::Config {
            swings: arte_core::local_swings::Config {
                reversal_bps: 50.,
                volatility_multiple: 2.,
                volatility_cap_multiple: 2.,
                lifetime_bars: 1800,
                maximum_levels: 100,
            },
            encounters: arte_core::strategy_encounters::stream::Config {
                tick: 0.01,
                settings: arte_core::strategy_encounters::Settings {
                    breakout_buffer_ticks: 1.,
                    breakout_buffer_bps: 0.,
                    rejection_break_offset_bps: 10.,
                    topping_tail_fraction: 0.5,
                    maximum_encounters: 100,
                },
                maximum_prior_levels: 100,
            },
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
        }
    }
    fn lane() -> Lane {
        lane_with_seed().0
    }
    fn lane_with_seed() -> (Lane, String) {
        use arte_core::{
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
        let seed_hash = seed.hash.clone();
        let mut lane = Lane::new(scheduler, SECOND / 10, SECOND, feature_config()).unwrap();
        lane.bind_quote_policy(crate::test_quote_policy()).unwrap();
        (lane, seed_hash)
    }
    #[test]
    fn live_release_obeys_gate_and_requires_each_boundary_acknowledgment() {
        use arte_core::{
            events::{Decimal, EventKey, Observation, Payload, SourceTime},
            exposure::Gate,
        };
        const SECOND: u64 = 1_000_000_000;
        let mut lane = lane();
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
        let at_ns = features.one_second.as_ref().unwrap().at_ns;
        let authority = || candidate_features::AdmissionAuthorities {
            at_ns,
            permissions: false,
            session_open: true,
            tradable: false,
            encounter_blocked: true,
            regular_block: Some("fixture restriction".into()),
        };
        let admission = lane.completed_admission(authority()).unwrap();
        assert_eq!(admission.detector_at_ns, Some(at_ns));
        assert_eq!(admission.detector_fingerprint.len(), 64);
        assert!(!admission.permissions && !admission.tradable && !admission.macd_positive);
        assert!(admission.encounter_blocked && admission.regular_block.is_some());
        if let arte_core::market_structure::scheduler::Kind::Completed {
            interval_ns,
            bar,
            available_at_ns,
        } = boundary.kind
        {
            let mismatched = Boundary {
                id: boundary.id,
                sequence: boundary.sequence + 1,
                evaluated_at_ns: boundary.evaluated_at_ns,
                kind: arte_core::market_structure::scheduler::Kind::Completed {
                    interval_ns,
                    bar,
                    available_at_ns,
                },
            };
            assert!(lane
                .features
                .completed_admission(&mismatched, authority())
                .is_err());
        }
        use arte_core::strategy_dispatch::{Action, Mode, Safety, Scope};
        let scopes = ["a", "b"].map(|account| Scope {
            run_id: "live-test".into(),
            mode: Mode::Paper,
            account: account.into(),
            strategy_instance: "candidate".into(),
            strategy_kind: arte_core::strategy_dispatch::StrategyKind::GenericCandidate,
            execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                1_000_000_000,
            ),
            instrument: 1,
            code_hash: "code".into(),
            config_hash: "config".into(),
        });
        let input = boundary.input("features".into());
        let mut barrier = lane.account_boundary(&scopes, 2).unwrap();
        assert!(lane.acknowledge_accounts(&mut barrier).is_err());
        let safety = Safety {
            position_quantity: 0,
            pending_exit_quantity: 0,
            exit_pending: false,
            pending_entry: false,
            last_exit_reason: None,
            flatten: false,
            protective_stop_crossed: false,
            manual_exit: false,
            completed_macd_reversal: false,
            setup_phase: arte_core::strategy_lifecycle::Phase::Building,
            luld_buffer_reached: false,
            encounter_exit: false,
            early_setup_failed: false,
            structural_exit: false,
        };
        for (index, scope) in scopes.into_iter().enumerate() {
            let mut account =
                arte_core::strategy_transaction::Runtime::new(scope, 0_u64, 1024).unwrap();
            account
                .prepare(input.clone(), &safety, "evidence".into(), |_| {
                    Ok(vec![Action::Wait {
                        reason: "test".into(),
                    }])
                })
                .unwrap();
            // Missing readback cannot satisfy this account or advance the lane.
            assert!(account.acknowledge(&[]).is_err());
            assert!(lane.acknowledge_accounts(&mut barrier).is_err());
            let rows = account.pending_batch().unwrap().records().to_vec();
            barrier
                .record(&account.acknowledge(&rows).unwrap())
                .unwrap();
            assert_eq!(barrier.remaining(), 1 - index);
            assert_eq!(lane.pending_boundary().unwrap().unwrap().id, bar_id);
        }
        lane.acknowledge_accounts(&mut barrier).unwrap();
        assert!(barrier.finished());
        assert!(lane.pending_boundary().unwrap().is_none());
        assert!(!lane.prepare_next(gate.at(101), 202 * SECOND).unwrap());
        let band = arte_core::luld::Evidence {
            provider: 1,
            instrument: 1,
            session: 20260915,
            lower: 900,
            upper: 1100,
            scale: 2,
            effective_at_ns: 202 * SECOND,
            available_at_ns: 202 * SECOND,
            official: true,
        };
        assert!(lane
            .current_luld(gate.at(101), 202 * SECOND, 2, SECOND)
            .is_err());
        lane.observe_luld(&band, 202 * SECOND).unwrap();
        assert_eq!(
            lane.current_luld(gate.at(101), 202 * SECOND, 2, SECOND)
                .unwrap(),
            &band
        );
        assert!(lane
            .current_luld(gate.at(201), 202 * SECOND, 2, SECOND)
            .is_err());
        assert!(lane
            .current_luld(gate.at(101), 203 * SECOND + 1, 2, SECOND)
            .is_err());
        assert!(!lane.prepare_next(gate.at(101), 202 * SECOND).unwrap());
        let policy = arte_core::luld::Policy {
            tick: 1,
            scale: 2,
            buffer_ticks: 3,
            buffer_bps: Decimal { atoms: 0, scale: 0 },
            include_spread: true,
            maximum_age_ns: SECOND,
            minimum_previous_close: 500,
        };
        let record = arte_core::reference_data::PreviousClose {
            provider: 1,
            instrument: 1,
            session: 20260914,
            price: Decimal {
                atoms: 10,
                scale: 0,
            },
            available_at_ns: 200 * SECOND,
            source_manifest_hash: "a".repeat(64),
        };
        let requirement = arte_core::reference_data::PreviousCloseRequirement {
            session: record.session,
            record_hash: arte_core::content_hash(&record).unwrap(),
        };
        let prior = Some(&record);
        assert!(lane
            .regular_admission(gate.at(101), 202 * SECOND, prior, &requirement, &policy)
            .is_err());
        let quote = Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Quote,
                sequence: 1,
            },
            payload: Payload::Quote {
                bid: Decimal {
                    atoms: 99,
                    scale: 1,
                },
                ask: Decimal {
                    atoms: 10,
                    scale: 0,
                },
                bid_size: Decimal { atoms: 1, scale: 0 },
                ask_size: Decimal { atoms: 1, scale: 0 },
                bid_exchange: 1,
                ask_exchange: 1,
                conditions: vec![],
                indicators: vec![],
            },
            sip: SourceTime {
                ns: 202 * SECOND,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: 202 * SECOND,
            receipt: None,
        };
        // Direct offline fixture; does not claim provider ingestion certification.
        lane.quotes.observe(&quote).unwrap();
        let admitted = lane
            .regular_admission(gate.at(101), 202 * SECOND, prior, &requirement, &policy)
            .unwrap();
        assert!(admitted.block.is_none());
        assert_eq!(admitted.bands.unwrap().target, 1090);
        assert_eq!(
            lane.regular_admission(gate.at(101), 202 * SECOND, None, &requirement, &policy)
                .unwrap()
                .block,
            Some(arte_core::luld::Block::RegularPreviousCloseUnavailable)
        );
        assert!(lane
            .regular_admission(gate.at(201), 202 * SECOND, prior, &requirement, &policy)
            .is_err());
        assert!(lane
            .regular_admission(gate.at(101), 203 * SECOND, prior, &requirement, &policy)
            .is_err());
        let mut changed = record.clone();
        changed.price = Decimal {
            atoms: 10001,
            scale: 3,
        };
        assert!(lane
            .regular_admission(
                gate.at(101),
                202 * SECOND,
                Some(&changed),
                &requirement,
                &policy
            )
            .is_err());
        let exactness_requirement = arte_core::reference_data::PreviousCloseRequirement {
            session: changed.session,
            record_hash: arte_core::content_hash(&changed).unwrap(),
        };
        assert!(lane
            .regular_admission(
                gate.at(101),
                202 * SECOND,
                Some(&changed),
                &exactness_requirement,
                &policy
            )
            .is_err());
        lane.transport_lost();
        assert!(lane
            .current_luld(gate.at(101), 202 * SECOND, 2, SECOND)
            .is_err());
    }
    #[test]
    fn exact_signal_binds_before_first_live_boundary_and_consumes_it_once() {
        use arte_core::{
            events::{Decimal, EventKey, Observation, Payload, Receipt, SourceTime},
            exact_bars::{Builder, Mode as BarMode},
            exposure::Gate,
            strategy350_signal::{Config as SignalConfig, State as SignalState},
        };
        const SECOND: u64 = 1_000_000_000;
        let (mut lane, seed_hash) = lane_with_seed();
        let scope = lane.market.scope();
        let bars = Builder::new(
            scope,
            BarMode::Live,
            200 * SECOND,
            300 * SECOND,
            2,
            0,
            "a".repeat(64),
        )
        .unwrap();
        let signal = SignalState::new_live(
            SignalConfig {
                execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                    100_000_000,
                ),
                minimum_move_bps: 5,
                source_algorithm_hash: "b".repeat(64),
            },
            bars.configuration_hash().into(),
            200 * SECOND,
            300 * SECOND,
        )
        .unwrap();
        let noise = arte_core::strategy350_noise::State::new(
            crate::test_noise_config(),
            200 * SECOND,
            300 * SECOND,
        )
        .unwrap();
        lane.bind_exact_signal(
            live_exact_signal::Owner::new(bars, signal, noise, &crate::test_macd_config()).unwrap(),
        )
        .unwrap();
        assert_eq!(lane.first_squeeze_occurrence().unwrap(), None);
        let event = Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Trade,
                sequence: 1,
            },
            payload: Payload::Trade {
                price: Decimal::parse("10.00").unwrap(),
                size: Decimal::parse("1").unwrap(),
                exchange: 1,
                trade_id: "t1".into(),
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
            receipt: Some(Receipt {
                run_id: "test".into(),
                lane: 1,
                sequence: 1,
                utc_ns: 200 * SECOND + 2,
                monotonic_ns: 1,
            }),
        };
        lane.market.enqueue(&event, true).unwrap();
        lane.high.insert(EventKind::Trade, 202 * SECOND);
        lane.high.insert(EventKind::Quote, 202 * SECOND);
        let mut gate = Gate::new(100, 2).unwrap();
        gate.transport(true);
        gate.update(1, EventKind::Trade, 1, true).unwrap();
        gate.update(1, EventKind::Quote, 1, true).unwrap();
        assert!(lane.prepare_next(gate.at(1), 202 * SECOND).unwrap());
        assert_eq!(lane.exact_signal.as_ref().unwrap().last_boundary().0, 1);
        assert_eq!(lane.first_squeeze_occurrence().unwrap(), None);
        lane.market
            .bind_quote_policy(std::sync::Arc::new(crate::test_quote_policy()))
            .unwrap();
        let context = "c".repeat(64);
        let cut = lane.checkpoint_pending(&context, 1_000_000).unwrap();
        assert!(!lane.forming_macd().unwrap().bullish);
        let macd = lane.forming_macd_evidence().unwrap();
        assert!(!macd.outcome().bullish);
        assert_eq!(macd.run_id(), "test");
        let refs = recovery::Bundle::references(&cut.root).unwrap();
        assert_eq!(refs.scheduler, cut.scheduler.root.id);
        assert_eq!(refs.features, cut.features.id);
        assert_eq!(refs.signal, cut.signal.root.id);
        assert_eq!(cut.objects().len(), 13);
        assert_eq!(cut.objects().last().unwrap().id, cut.root.id);
        let scheduler_refs =
            arte_core::market_structure::scheduler::checkpoint::Bundle::references(
                &cut.scheduler.root,
            )
            .unwrap();
        assert_eq!(scheduler_refs[0], cut.scheduler.market.id);
        let market_hash = lane.market.state().unwrap().configuration_hash().to_owned();
        let generation = "a".repeat(64);
        let boundary = lane.market.pending().unwrap().unwrap();
        let id = boundary.id.to_owned();
        let restored = Lane::restore_pending(
            &cut,
            recovery::Request {
                context_hash: &context,
                expected_root: &cut.root.id,
                scheduler: arte_core::market_structure::scheduler::checkpoint::Request {
                    context_hash: &context,
                    run_id: "offline-live-lane",
                    seed_hash: &seed_hash,
                    configuration_hash: &market_hash,
                    quote_policy: std::sync::Arc::new(crate::test_quote_policy()),
                    maximum_pending: 10,
                    maximum_bytes: 1_000_000,
                },
                signal: live_exact_signal::Recovery {
                    scope,
                    session_start_ns: 200 * SECOND,
                    session_end_ns: 300 * SECOND,
                    price_scale: 2,
                    size_scale: 0,
                    source_generation_hash: &generation,
                    signal_config: SignalConfig {
                        execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                            100_000_000,
                        ),
                        minimum_move_bps: 5,
                        source_algorithm_hash: "b".repeat(64),
                    },
                    noise_config: crate::test_noise_config(),
                    macd_config: crate::test_macd_config(),
                    expected_sequence: 1,
                    expected_boundary_id: Some(&id),
                },
                feature_config: feature_config(),
                allowed_lateness_ns: SECOND / 10,
                maximum_quote_age_ns: SECOND,
                maximum_bytes: 1_000_000,
            },
        )
        .unwrap();
        assert_eq!(restored.pending_boundary().unwrap().unwrap().id, id);
        assert_eq!(restored.first_squeeze_occurrence().unwrap(), None);
        assert_eq!(
            restored.adaptive_noise_distance(1_000).unwrap(),
            lane.adaptive_noise_distance(1_000).unwrap()
        );
        assert!(restored.high.is_empty());
        assert!(restored.quotes.policy_hash().is_err());
        assert!(restored.bands.latest().is_none());
        assert!(lane.prepare_next(gate.at(1), 202 * SECOND).is_err());
        assert_eq!(lane.exact_signal.as_ref().unwrap().last_boundary().0, 1);
        let image = lane.exact_signal.as_ref().unwrap().checkpoint().unwrap();
        let generation = "a".repeat(64);
        let config = SignalConfig {
            execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                100_000_000,
            ),
            minimum_move_bps: 5,
            source_algorithm_hash: "b".repeat(64),
        };
        let boundary = lane.market.pending().unwrap().unwrap();
        let recovered = live_exact_signal::Owner::restore(
            &image,
            &image.root.id,
            live_exact_signal::Recovery {
                scope,
                session_start_ns: 200 * SECOND,
                session_end_ns: 300 * SECOND,
                price_scale: 2,
                size_scale: 0,
                source_generation_hash: &generation,
                signal_config: config,
                noise_config: crate::test_noise_config(),
                macd_config: crate::test_macd_config(),
                expected_sequence: boundary.sequence,
                expected_boundary_id: Some(boundary.id),
            },
        )
        .unwrap();
        lane.exact_signal = None;
        lane.bind_recovered_exact_signal(recovered).unwrap();
        assert_eq!(lane.first_squeeze_occurrence().unwrap(), None);
        let pending = lane.market.pending().unwrap().unwrap();
        assert_eq!(
            lane.exact_signal
                .as_mut()
                .unwrap()
                .observe(&pending)
                .unwrap(),
            None
        );
        let id = pending.id.to_owned();
        lane.acknowledge_boundary(&id).unwrap();
        lane.exact_signal = None;
        let recovery_without_pending = live_exact_signal::Owner::restore(
            &image,
            &image.root.id,
            live_exact_signal::Recovery {
                scope,
                session_start_ns: 200 * SECOND,
                session_end_ns: 300 * SECOND,
                price_scale: 2,
                size_scale: 0,
                source_generation_hash: &generation,
                signal_config: SignalConfig {
                    execution_interval: arte_core::execution_interval::ExecutionInterval::Fixed(
                        100_000_000,
                    ),
                    minimum_move_bps: 5,
                    source_algorithm_hash: "b".repeat(64),
                },
                noise_config: crate::test_noise_config(),
                macd_config: crate::test_macd_config(),
                expected_sequence: 1,
                expected_boundary_id: Some(&id),
            },
        )
        .unwrap();
        assert!(lane
            .bind_recovered_exact_signal(recovery_without_pending)
            .is_err());
    }

    #[test]
    fn live_ingestion_uses_pinned_eligibility_and_unknown_rules_poison_lane() {
        use arte_core::{
            events::*,
            latency::{Assessment, Health},
            trade_eligibility::{Pinned, Policy},
        };
        const S: u64 = 1_000_000_000;
        for fault in 0..4 {
            let mut lane = lane();
            let policy = Policy {
                schema_version: 1,
                provider: 1,
                valid_from_ns: 200 * S,
                valid_to_ns: 300 * S,
                available_at_ns: 199 * S,
                source_manifest_hash: "a".repeat(64),
                allowed_conditions: Default::default(),
                excluded_conditions: [2].into(),
                allow_empty_conditions: true,
            };
            if fault != 1 {
                let hash = policy.hash().unwrap();
                lane.bind_trade_policy(std::sync::Arc::new(Pinned::new(policy, &hash).unwrap()))
                    .unwrap();
            }
            let event = AuditedEvent {
                observation: Observation {
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
                        conditions: if fault == 2 {
                            vec![3]
                        } else if fault == 3 {
                            vec![2]
                        } else {
                            vec![]
                        },
                        correction: None,
                    },
                    sip: SourceTime {
                        ns: 201 * S,
                        precision_ns: 1,
                    },
                    participant: None,
                    available_at_ns: 201 * S + 1,
                    receipt: Some(Receipt {
                        run_id: "fixture".into(),
                        lane: 0,
                        sequence: 1,
                        utc_ns: 201 * S + 1,
                        monotonic_ns: 1,
                    }),
                },
                sip_latency: Assessment {
                    state: Health::Healthy,
                    notify: false,
                    lower_age_ns: 1,
                    upper_age_ns: 1,
                },
                participant_latency: None,
                exposure_permitted: true,
            };
            let result = lane.ingest(&event);
            if fault == 1 || fault == 2 {
                assert!(result.is_err());
                assert!(lane.available().is_err());
                assert_eq!(lane.market.pending_events(), 0);
            } else {
                result.unwrap();
                assert_eq!(lane.market.pending_events(), 1);
                assert!(lane.market.prepare_next(201 * S + 1, 201 * S + 1).unwrap());
                let boundary = lane.market.pending().unwrap().unwrap();
                match boundary.kind {
                    arte_core::market_structure::scheduler::Kind::Trade { eligible, .. } => {
                        assert_eq!(eligible, fault == 0)
                    }
                    _ => panic!("expected trade boundary"),
                }
            }
        }
    }
}
