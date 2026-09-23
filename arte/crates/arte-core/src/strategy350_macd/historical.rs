//! Sparse MACD close schedule from one certified 100 ms historical product.
//! The caller applies these inputs at their end clock before evaluating later
//! events. No live receipt or historical trade execution timestamp is inferred.
use super::{exact_source, Config, Outcome, State};
use crate::{
    bar_catalogue::{Column, Complete, BASE_INTERVAL_NS},
    content_hash,
    event_order::Scope,
    events::{Decimal, Observation, Payload},
    market_structure::scheduler::playback::sources::HistoricalEventProof,
    Error, Result,
};

pub struct Projection {
    scope: Scope,
    session_start_ns: u64,
    session_end_ns: u64,
    config: Config,
    request_hash: String,
    coverage_hash: String,
    completed: Vec<exact_source::CompletedInput>,
}

pub struct Evidence {
    outcome: Outcome,
    fingerprint: String,
    proof_hash: String,
}
impl Evidence {
    pub fn outcome(&self) -> &Outcome {
        &self.outcome
    }
    pub fn fingerprint(&self) -> &str {
        &self.fingerprint
    }
    pub fn require_proof(&self, proof: &HistoricalEventProof) -> Result<()> {
        if self.proof_hash != proof.identity_hash()? {
            return Err(Error::Conflict(
                "historical MACD evidence proof differs".into(),
            ));
        }
        Ok(())
    }
}

/// The only decision-facing historical view. The final projected state is
/// deliberately not exposed: replay must advance this cursor monotonically.
pub struct Cursor {
    projection: Projection,
    state: State,
    next: usize,
    clock_ns: u64,
}
impl Projection {
    pub fn completed_count(&self) -> usize {
        self.completed.len()
    }
    pub fn cursor(self) -> Result<Cursor> {
        let state = State::new(
            self.scope,
            self.session_start_ns,
            self.session_end_ns,
            &self.config,
        )?;
        Ok(Cursor {
            clock_ns: self.session_start_ns,
            projection: self,
            state,
            next: 0,
        })
    }
}
impl Cursor {
    pub fn scope(&self) -> Scope {
        self.projection.scope
    }
    pub fn advance_to(&mut self, clock_ns: u64) -> Result<usize> {
        if clock_ns < self.clock_ns || clock_ns > self.projection.session_end_ns {
            return Err(Error::Conflict("historical MACD replay clock".into()));
        }
        if self
            .projection
            .completed
            .get(self.next)
            .is_none_or(|input| input.end_ns > clock_ns)
        {
            self.clock_ns = clock_ns;
            return Ok(0);
        }
        let mut staged = self.state.clone();
        let mut next = self.next;
        while let Some(input) = self.projection.completed.get(next) {
            if input.end_ns > clock_ns {
                break;
            }
            staged.observe_completed(input.timeframe_ns, input.end_ns, input.close)?;
            next += 1;
        }
        let applied = next - self.next;
        self.state = staged;
        self.next = next;
        self.clock_ns = clock_ns;
        Ok(applied)
    }
    fn preview_trade(
        &mut self,
        event_time_ns: u64,
        evaluated_at_ns: u64,
        price: Decimal,
    ) -> Result<Outcome> {
        if event_time_ns >= self.projection.session_end_ns
            || evaluated_at_ns < event_time_ns
            || !price.positive()
            || price.scale != self.projection.config.price_scale
        {
            return Err(Error::Invalid("historical MACD trade".into()));
        }
        self.advance_to(event_time_ns)?;
        self.state
            .preview_trade(event_time_ns, evaluated_at_ns, price)
    }
    /// Consume the same historical proof that selected the pending trade.
    /// The proof supplies modeled availability; the source observation supplies
    /// the actual trade price. Neither is a measured live receipt.
    pub fn preview_proof(
        &mut self,
        proof: &HistoricalEventProof,
        observation: &Observation,
    ) -> Result<Evidence> {
        let Payload::Trade { price, .. } = &observation.payload else {
            return Err(Error::Conflict(
                "historical MACD proof is not a trade".into(),
            ));
        };
        if proof.scope() != self.scope()
            || proof.key() != &observation.key
            || proof.event_hash() != content_hash(observation)?
            || proof.source_time_ns() != observation.sip.ns
            || !proof.eligible()
            || proof.modeled_available_at_ns() > proof.evaluated_at_ns()
        {
            return Err(Error::Conflict("historical MACD proof differs".into()));
        }
        let outcome =
            self.preview_trade(proof.source_time_ns(), proof.evaluated_at_ns(), *price)?;
        let values = outcome.previews.map(|preview| {
            preview.map(|value| {
                (
                    value.timeframe_ns,
                    value.completed_end_ns,
                    value.line.to_bits(),
                    value.signal.to_bits(),
                )
            })
        });
        let proof_hash = proof.identity_hash()?;
        let fingerprint = content_hash(&(
            "arte.strategy-350-historical-macd-evidence.v1",
            self.projection.request_hash.as_str(),
            self.projection.coverage_hash.as_str(),
            self.state.config_hash(),
            proof_hash.as_str(),
            outcome.event_time_ns,
            outcome.evaluated_at_ns,
            values,
            outcome.bullish,
        ))?;
        Ok(Evidence {
            outcome,
            fingerprint,
            proof_hash,
        })
    }
}

pub fn project(
    product: &Complete,
    expected_request_hash: &str,
    expected_coverage_hash: &str,
    config: &Config,
) -> Result<Projection> {
    let request = product.request();
    config.hash()?;
    if request.hash()? != expected_request_hash
        || product.coverage_hash() != expected_coverage_hash
        || request.instruments.len() != 1
        || request.timeframe_ns != BASE_INTERVAL_NS
        || request.calculation_hash != config.source_algorithm_hash
        || !request.columns.contains(&Column::Close)
        || !request.columns.contains(&Column::Trades)
    {
        return Err(Error::Invalid(
            "Strategy 350 historical MACD product".into(),
        ));
    }
    let first = product
        .batches()
        .first()
        .ok_or_else(|| Error::Unready("historical MACD bars missing".into()))?;
    let scope = Scope {
        provider: request.provider,
        instrument: request.instruments[0],
        session: request.session,
    };
    if first.price_scale != config.price_scale {
        return Err(Error::Conflict("historical MACD price scale".into()));
    }
    let mut source = exact_source::Source::new(
        scope,
        request.interval.start,
        request.interval.end,
        config.price_scale,
        request.calculation_hash.clone(),
    )?;
    let mut state = State::new(scope, request.interval.start, request.interval.end, config)?;
    let mut completed = Vec::new();
    for batch in product.batches() {
        if batch.instrument != scope.instrument || batch.price_scale != config.price_scale {
            return Err(Error::Conflict("historical MACD source changed".into()));
        }
        let closes = batch
            .close
            .as_deref()
            .ok_or_else(|| Error::Unready("historical MACD close missing".into()))?;
        let trades = batch
            .trades
            .as_deref()
            .ok_or_else(|| Error::Unready("historical MACD trades missing".into()))?;
        for input in
            source.advance_compact_batch(batch.first_start_ns, &batch.present, closes, trades)?
        {
            state.observe_completed(input.timeframe_ns, input.end_ns, input.close)?;
            completed.push(input);
        }
    }
    if source.watermark_ns() != request.interval.end {
        return Err(Error::Conflict("historical MACD coverage".into()));
    }
    state.require_source(&source)?;
    Ok(Projection {
        scope,
        session_start_ns: request.interval.start,
        session_end_ns: request.interval.end,
        config: config.clone(),
        request_hash: expected_request_hash.into(),
        coverage_hash: expected_coverage_hash.into(),
        completed,
    })
}

#[cfg(test)]
pub(crate) fn test_empty_cursor(
    scope: Scope,
    session_start_ns: u64,
    session_end_ns: u64,
    price_scale: u8,
) -> Cursor {
    use crate::execution_interval::ExecutionInterval;
    Projection {
        scope,
        session_start_ns,
        session_end_ns,
        config: Config {
            execution_interval: ExecutionInterval::Events,
            price_scale,
            source_algorithm_hash: "b".repeat(64),
        },
        request_hash: "d".repeat(64),
        coverage_hash: "e".repeat(64),
        completed: Vec::new(),
    }
    .cursor()
    .unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        bar_catalogue::{Batch, Coverage, Readback, Request, Source},
        coverage::Interval,
        execution_interval::ExecutionInterval,
    };
    const SECOND: u64 = 1_000_000_000;
    const START: u64 = 30 * SECOND;

    fn product_with_bars(end_ns: u64, bars: &[(usize, i64)]) -> Complete {
        let count = ((end_ns - START) / BASE_INTERVAL_NS) as usize;
        let request = Request {
            provider: 1,
            instruments: vec![10],
            session: 20260922,
            interval: Interval {
                start: START,
                end: end_ns,
            },
            timeframe_ns: BASE_INTERVAL_NS,
            source_generation: "a".repeat(64),
            calculation_hash: "b".repeat(64),
            columns: [Column::Close, Column::Trades].into(),
            maximum_rows: count,
        };
        let coverage = Coverage {
            provider: request.provider,
            session: request.session,
            interval: request.interval,
            timeframe_ns: request.timeframe_ns,
            source_generation: request.source_generation.clone(),
            calculation_hash: request.calculation_hash.clone(),
            sources: [(
                10,
                Source {
                    certificate_hash: "c".repeat(64),
                    price_scale: 2,
                    size_scale: 0,
                },
            )]
            .into(),
            published_at_ns: end_ns + SECOND,
        };
        let mut present = vec![false; count];
        let mut close = vec![0; count];
        let mut trades = vec![0; count];
        for &(slot, price) in bars {
            present[slot] = true;
            close[slot] = price;
            trades[slot] = 1;
        }
        let batch = Batch {
            request_hash: request.hash().unwrap(),
            coverage_hash: coverage.hash().unwrap(),
            instrument: 10,
            first_start_ns: START,
            count: count as u32,
            price_scale: 2,
            size_scale: 0,
            present,
            open: None,
            high: None,
            low: None,
            close: Some(close),
            volume: None,
            notional: None,
            trades: Some(trades),
        };
        let mut readback = Readback::new(request, &coverage, end_ns + SECOND).unwrap();
        readback.observe(batch).unwrap();
        readback.finish().unwrap()
    }

    fn product() -> Complete {
        product_with_bars(START + 30 * SECOND, &[(0, 1000), (9, 1010), (299, 1050)])
    }

    #[test]
    fn certified_sparse_compact_bars_seal_without_fabricated_trades() {
        let complete = product();
        let config = Config {
            execution_interval: ExecutionInterval::Events,
            price_scale: 2,
            source_algorithm_hash: "b".repeat(64),
        };
        let request_hash = complete.request().hash().unwrap();
        let coverage_hash = complete.coverage_hash().to_owned();
        let projection = project(&complete, &request_hash, &coverage_hash, &config).unwrap();
        assert_eq!(projection.completed_count(), 7);
        assert_eq!(
            projection
                .completed
                .iter()
                .filter(|input| input.timeframe_ns == SECOND)
                .count(),
            2
        );
        assert_eq!(
            projection
                .completed
                .iter()
                .filter(|input| input.timeframe_ns == 30 * SECOND)
                .count(),
            1
        );
        assert_eq!(
            projection.completed.last().unwrap().end_ns,
            START + 30 * SECOND
        );
        let mut cursor = projection.cursor().unwrap();
        let price = Decimal {
            atoms: 1000,
            scale: 2,
        };
        assert!(
            !cursor
                .preview_trade(START + SECOND - 1, START + SECOND - 1, price)
                .unwrap()
                .bullish
        );
        assert_eq!(cursor.advance_to(START + SECOND).unwrap(), 1);
        assert!(cursor.advance_to(START + SECOND - 1).is_err());
        let before_later_closes = cursor
            .preview_trade(START + 4 * SECOND, START + 4 * SECOND, price)
            .unwrap();
        assert!(before_later_closes.previews[0].is_none());
        assert!(before_later_closes.previews[2].is_none());
        assert_eq!(cursor.advance_to(START + 29 * SECOND).unwrap(), 2);
        assert_eq!(cursor.advance_to(START + 30 * SECOND).unwrap(), 4);
        assert!(project(&complete, &"f".repeat(64), &coverage_hash, &config).is_err());
        assert!(project(&complete, &request_hash, &"f".repeat(64), &config).is_err());
    }

    #[test]
    fn certified_later_bar_can_make_all_four_forming_frames_bullish() {
        use crate::{
            events::{EventKey, EventKind, Payload, SourceTime},
            market_structure::scheduler::playback::{
                sources::{Catalog, Shard},
                Frame, Input, Limits, Prepared,
            },
            run_manifest::{Clock, Consumer, Execution, Manifest, Pinned},
            strategy_dispatch::{InputBoundary, Mode, StrategyKind},
        };
        let complete = product_with_bars(START + 60 * SECOND, &[(299, 900)]);
        let config = Config {
            execution_interval: ExecutionInterval::Events,
            price_scale: 2,
            source_algorithm_hash: "b".repeat(64),
        };
        let mut cursor = project(
            &complete,
            &complete.request().hash().unwrap(),
            complete.coverage_hash(),
            &config,
        )
        .unwrap()
        .cursor()
        .unwrap();
        let event_time = START + 30 * SECOND + 1;
        let before = cursor
            .preview_trade(
                event_time - 2,
                event_time - 2,
                Decimal {
                    atoms: 1000,
                    scale: 2,
                },
            )
            .unwrap();
        assert!(!before.bullish);
        let after = cursor
            .preview_trade(
                event_time,
                event_time + 1,
                Decimal {
                    atoms: 1000,
                    scale: 2,
                },
            )
            .unwrap();
        assert!(after.bullish);
        assert!(after.previews.iter().all(Option::is_some));
        let observation = Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind: EventKind::Trade,
                sequence: 1,
            },
            payload: Payload::Trade {
                price: Decimal {
                    atoms: 1000,
                    scale: 2,
                },
                size: Decimal { atoms: 1, scale: 0 },
                exchange: 1,
                trade_id: "later-trade".into(),
                trf: None,
                conditions: Vec::new(),
                correction: None,
            },
            sip: SourceTime {
                ns: event_time,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: event_time + 1,
            receipt: None,
        };
        let scope = cursor.scope();
        let prepared = Prepared::new(
            scope,
            "modeled-completed-macd-v1",
            vec![Frame {
                watermark_ns: event_time,
                evaluated_at_ns: event_time + 1,
                inputs: vec![Input {
                    observation: observation.clone(),
                    eligible: true,
                }],
            }],
            Limits {
                maximum_frames: 1,
                maximum_events: 1,
                maximum_serialized_bytes: 4096,
            },
        )
        .unwrap();
        let catalog = Catalog {
            schema_version: 1,
            authority_manifest_hash: "c".repeat(64),
            clock: Clock::Historical,
            shards: vec![Shard {
                provider: scope.provider,
                instrument: scope.instrument,
                session: scope.session,
                prepared_hash: prepared.hash().into(),
                clock_model: "modeled-completed-macd-v1".into(),
            }],
        };
        let manifest = Manifest {
            schema_version: 3,
            run_id: "later-macd-run".into(),
            mode: Mode::Backtest,
            code_release_hash: "a".repeat(64),
            source_manifest_hash: catalog.hash().unwrap(),
            reference_manifest_hash: "b".repeat(64),
            seed_manifest_hash: "d".repeat(64),
            algorithm_manifest_hash: "e".repeat(64),
            dependency_plan_hash: "f".repeat(64),
            hardware_profile_hash: "1".repeat(64),
            clock: Clock::Historical,
            execution: Execution::Simulated {
                fill_model_hash: "2".repeat(64),
                cost_model_hash: "3".repeat(64),
            },
            consumers: vec![Consumer {
                account: "first".into(),
                instrument: 10,
                strategy_instance: "strategy-350".into(),
                strategy_kind: StrategyKind::Strategy350,
                execution_interval: ExecutionInterval::Events,
                effective_config_hash: "4".repeat(64),
            }],
        };
        let pinned = Pinned::new(manifest.clone(), &manifest.hash().unwrap()).unwrap();
        let source = catalog.bind_historical(&pinned, &prepared).unwrap();
        let proof = source.event(0, 0).unwrap();
        let evidence = cursor.preview_proof(&proof, &observation).unwrap();
        assert!(evidence.outcome().bullish);
        let input = InputBoundary {
            event_id: "later-trade".into(),
            event_time_ns: event_time,
            available_at_ns: event_time + 1,
            evaluated_at_ns: event_time + 1,
            source_sequence: 1,
            feature_hash: "features".into(),
        };
        crate::strategy350_transaction::require_historical_macd(Some(&evidence), &proof, &input)
            .unwrap();
        use crate::{
            strategy350_price_gate::{
                Config as PriceConfig, ContextSource, PriceFact, SessionContext,
                State as PriceState,
            },
            strategy_dispatch::{Action, Safety},
            trade_eligibility,
        };
        let policy = trade_eligibility::Policy {
            schema_version: 1,
            provider: 1,
            valid_from_ns: 0,
            valid_to_ns: u64::MAX,
            available_at_ns: 0,
            source_manifest_hash: "b".repeat(64),
            allowed_conditions: Default::default(),
            excluded_conditions: Default::default(),
            allow_empty_conditions: true,
        };
        let policy_hash = policy.hash().unwrap();
        let policy = trade_eligibility::Pinned::new(policy, &policy_hash).unwrap();
        let price_config = PriceConfig {
            execution_interval: ExecutionInterval::Events,
            price_scale: 2,
            prior_close_max_atoms: 2000,
            purchase_min_atoms: 100,
            late_gain_bps: 1500,
            hod_floor_bps: 7000,
            prior_close_source_hash: "a".repeat(64),
            trade_policy_hash: policy_hash,
        };
        let price_hash = price_config.hash().unwrap();
        let mut price_gate = PriceState::new(
            scope,
            ContextSource::HistoricalRest,
            START,
            price_config,
            &price_hash,
        )
        .unwrap();
        let prior_close = PriceFact {
            value: Decimal {
                atoms: 1900,
                scale: 2,
            },
            available_at_ns: START,
            source_hash: "a".repeat(64),
            source_order: None,
        };
        let context = SessionContext {
            source: ContextSource::HistoricalRest,
            session: scope.session,
            at_ns: event_time + 1,
            source_order: (event_time, 1),
            open: Decimal {
                atoms: 900,
                scale: 2,
            },
            high: Decimal {
                atoms: 1000,
                scale: 2,
            },
            prior_high: None,
            complete: true,
        };
        let price_evidence = price_gate
            .observe_evidence(
                &observation,
                &policy,
                Some(&prior_close),
                &context,
                event_time + 1,
            )
            .unwrap();
        assert!(price_evidence.outcome().block.is_none());
        let refinement = crate::strategy350_screen_join::test_historical_refinement(
            scope,
            Interval {
                start: event_time / BASE_INTERVAL_NS * BASE_INTERVAL_NS,
                end: event_time / BASE_INTERVAL_NS * BASE_INTERVAL_NS + BASE_INTERVAL_NS,
            },
        );
        let safety = Safety {
            position_quantity: 1,
            pending_exit_quantity: 0,
            exit_pending: false,
            pending_entry: false,
            last_exit_reason: None,
            flatten: false,
            protective_stop_crossed: false,
            manual_exit: false,
            completed_macd_reversal: false,
            setup_phase: crate::strategy_lifecycle::Phase::Building,
            luld_buffer_reached: false,
            encounter_exit: false,
            early_setup_failed: false,
            structural_exit: false,
        };
        let level = crate::strategy_targets::TargetLevel {
            geometry: crate::strategy_encounters::Level {
                id: "resistance".into(),
                price: 10.,
                lower: 10.,
                upper: 10.,
                role: crate::v7_encounters::ActiveRole::Resistance,
                confirmed_at_ns: START,
            },
            historical: true,
            transition_from: None,
            synthetic: false,
        };
        let proposal = crate::strategy_adds::Proposal {
            confirmed_at_ns: event_time,
            tranche_index: 2,
            tranche_count: 3,
            broken: level,
            threshold: 10.,
            stop: 9.,
            target: 11.,
            maximum_buy_price: 10.5,
        };
        let mut account = crate::strategy_transaction::Runtime::new(
            pinned.scope("first", 10, "strategy-350").unwrap(),
            0_u64,
            1024,
        )
        .unwrap();
        let other_hash = "e".repeat(64);
        let decision = crate::strategy350_transaction::prepare_historical_market_decision(
            &mut account,
            crate::strategy350_transaction::HistoricalMarketDecisionInput {
                input: input.clone(),
                safety: &safety,
                price: &price_evidence,
                source: &proof,
                expected_price_gate_hash: &price_hash,
                refinement: Some(&refinement),
                macd: Some(&evidence),
                other_evidence_hash: &other_hash,
            },
            |_| Ok(()),
            |_| Ok(vec![Action::Add(Box::new(proposal))]),
        )
        .unwrap();
        assert_eq!(decision.actions.len(), 1);
        let rows = account.pending_batch().unwrap().records().to_vec();
        let committed = account.acknowledge(&rows).unwrap();
        let sealed = crate::strategy350_transaction::CommittedHistoricalDecision::from_readback(
            &committed,
            &price_evidence,
            &proof,
            &price_hash,
            Some(&refinement),
            Some(&evidence),
            &other_hash,
        )
        .unwrap();
        assert!(
            crate::strategy350_transaction::CommittedHistoricalDecision::from_readback(
                &committed,
                &price_evidence,
                &proof,
                &price_hash,
                Some(&refinement),
                None,
                &other_hash,
            )
            .is_err()
        );
        let allocation = crate::decision_orders::Allocation {
            account: "first".into(),
            instrument: 10,
            quantity: 1,
            price_scale: 2,
            tick: 1,
            entry_limit: 1000,
            deadline_ns: event_time + SECOND,
        };
        let bands = crate::luld::Evidence {
            provider: 1,
            instrument: 10,
            session: scope.session,
            lower: 800,
            upper: 1200,
            scale: 2,
            effective_at_ns: event_time,
            available_at_ns: event_time + 1,
            official: true,
        };
        let risk = crate::orders::RiskPolicy {
            band_provider: 1,
            band_session: scope.session,
            band_buffer_ticks: 3,
            max_band_age_ns: SECOND,
        };
        assert!(crate::decision_orders::bracket(
            &committed,
            0,
            &allocation,
            event_time + 1,
            true,
            Some(&bands),
            &risk,
        )
        .is_err());
        let plan = crate::decision_orders::bracket_350_historical(
            &sealed,
            0,
            &allocation,
            event_time + 1,
            true,
            Some(&bands),
            &risk,
        )
        .unwrap();
        assert_eq!(plan.bracket.stop, Some(900));
        assert_eq!(plan.bracket.target, Some(1100));
    }
}
