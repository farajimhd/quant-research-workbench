//! Account-owned selected-candidate composition; call on transaction working state.
//! Global exit arbitration and provider/admission producers remain outside this component.
use crate::strategy_dispatch::{Action, ExitReason};
use crate::strategy_lifecycle::RecoveryState;
use crate::{content_hash, Error, Result};
use crate::{strategy_adds as adds, strategy_entry as entry, strategy_protection as protection};
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PositionObservation {
    pub revision: u64,
    pub at_ns: u64,
    pub quantity: u64,
    pub average_price: Option<f64>,
    pub stop: Option<f64>,
    pub target: Option<protection::ActiveTarget>,
    pub pending_entry: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Active {
    pub entry: entry::Proposal,
    pub adds: adds::State,
    pub protection: protection::State,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct State {
    pub active: Option<Active>,
    pub recovery: RecoveryState,
    broker: Option<PositionObservation>,
    last_bar_ns: u64,
    last_intrabar_ns: u64,
    encounter_cancel_notified: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcquisitionPolicy {
    pub maximum_macd_age_ns: u64,
    pub confirmation_lifetime_ns: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcquisitionObservation {
    pub at_ns: u64,
    pub price: f64,
    pub ask: f64,
    pub vwap: Option<f64>,
    pub macd_at_ns: Option<u64>,
    pub macd_positive: bool,
    pub macd_episode_present: bool,
    pub tradable: bool,
    pub regular_block: bool,
    pub encounter_blocked: bool,
    pub pending_capital: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AcquisitionEvaluation {
    pub actions: Vec<Action>,
    pub continue_position_management: bool,
    pub reasons: Vec<String>,
}
#[derive(Serialize)]
pub struct Policy<'a> {
    pub maximum_completed_bar_age_ns: u64,
    pub entry: &'a entry::Policy,
    pub adds: &'a adds::Policy,
    pub protection: &'a protection::Policy,
    pub phase_minimum_progress_r: f64,
    pub failure_window_ns: u64,
    pub failure_buffer_ticks: f64,
    pub failure_exit_enabled: bool,
    pub preserve_peak: bool,
    pub stop_gain_guard: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Evaluation {
    pub actions: Vec<Action>,
    pub entry_evidence: Option<entry::Evidence>,
    pub phase_transitioned: bool,
}
impl State {
    /// Evaluate on each causal update after reconciled-position observation.
    /// This does not replace OMS deadline checks for already submitted orders.
    pub fn acquisition_update(
        &mut self,
        o: &AcquisitionObservation,
        p: &AcquisitionPolicy,
    ) -> Result<AcquisitionEvaluation> {
        if o.at_ns < self.last_intrabar_ns
            || o.at_ns < self.last_bar_ns
            || p.confirmation_lifetime_ns == 0
            || [o.price, o.ask]
                .into_iter()
                .any(|v| !v.is_finite() || v <= 0.)
            || o.macd_at_ns.is_some_and(|v| v > o.at_ns)
        {
            return Err(Error::Invalid(
                "invalid intrabar acquisition observation".into(),
            ));
        }
        let broker = self.broker.as_ref().ok_or_else(|| {
            Error::Unready("acquisition has no reconciled account snapshot".into())
        })?;
        if broker.at_ns > o.at_ns {
            return Err(Error::Invalid("future account snapshot".into()));
        }
        let acquired = broker.quantity > 0;
        let pending = broker.pending_entry || o.pending_capital;
        let macd = o.macd_positive
            && o.macd_episode_present
            && o.macd_at_ns
                .is_some_and(|v| o.at_ns - v <= p.maximum_macd_age_ns);
        let mut reasons = Vec::new();
        if o.regular_block {
            reasons.push("regular_session_block".into());
        }
        if self.active.is_none() {
            reasons.push("entry_context_missing".into());
        }
        if !o.tradable {
            reasons.push("tradability_incomplete".into());
        }
        if !macd {
            reasons.push("bullish_macd_unavailable".into());
        }
        if !o
            .vwap
            .is_some_and(|v| v.is_finite() && v > 0. && o.price > v)
        {
            reasons.push("vwap_gate".into());
        }
        if let Some(active) = &self.active {
            if o.at_ns < active.entry.setup.confirmed_at_ns {
                return Err(Error::Invalid("future entry confirmation".into()));
            }
            if o.at_ns - active.entry.setup.confirmed_at_ns >= p.confirmation_lifetime_ns {
                reasons.push("confirmation_expired".into());
            }
            if o.ask > active.entry.maximum_buy_price {
                reasons.push("maximum_buy_price_exceeded".into());
            }
        }
        let encounter =
            o.encounter_blocked && (pending || acquired) && !self.encounter_cancel_notified;
        let invalid = pending && (o.pending_capital || o.regular_block) && !reasons.is_empty();
        let cancel = encounter || invalid;
        if o.encounter_blocked {
            if encounter {
                self.encounter_cancel_notified = true;
            }
        } else {
            self.encounter_cancel_notified = false;
        }
        if encounter {
            reasons.insert(0, "unresolved_level_rejection".into());
        }
        self.last_intrabar_ns = o.at_ns;
        Ok(AcquisitionEvaluation {
            actions: if cancel {
                vec![Action::CancelEntry {
                    reason: if acquired && encounter {
                        "unresolved_level_rejection".into()
                    } else {
                        "entry_acquisition_invalidated".into()
                    },
                }]
            } else {
                vec![]
            },
            continue_position_management: !cancel || (acquired && encounter),
            reasons,
        })
    }
    /// Apply a reconciled account snapshot before global exit arbitration. Retain
    /// body-high evidence supplied by the causal market authority, not a future bar.
    pub fn observe_reconciled(
        &mut self,
        broker: &PositionObservation,
        observed_at_ns: u64,
        body_high: f64,
        preserve_peak: bool,
        stop_gain_guard: bool,
    ) -> Result<()> {
        let mut next = self.clone();
        next.reconcile(broker, observed_at_ns)?;
        if broker.quantity > 0 {
            let active = next.active.as_ref().unwrap();
            next.recovery.observe_position(
                observed_at_ns,
                Some((&active.entry.setup, broker.stop.unwrap(), body_high)),
                preserve_peak,
                stop_gain_guard,
            )?;
        }
        *self = next;
        Ok(())
    }
    /// Complete snapshot from the account's reconciliation authority, not order intent state.
    fn reconcile(&mut self, b: &PositionObservation, now: u64) -> Result<()> {
        if b.at_ns > now
            || b.quantity > 0
                && (!b.average_price.is_some_and(|v| v.is_finite() && v > 0.)
                    || !b.stop.is_some_and(|v| v.is_finite() && v > 0.)
                    || !b
                        .target
                        .as_ref()
                        .is_some_and(|v| v.price().is_finite() && v.price() > 0.))
        {
            return Err(Error::Invalid(
                "invalid reconciled candidate position".into(),
            ));
        }
        if let Some(previous) = &self.broker {
            if b.revision < previous.revision || b.at_ns < previous.at_ns {
                return Err(Error::Invalid("broker position cannot rewind".into()));
            }
            if b.revision == previous.revision && content_hash(b)? != content_hash(previous)? {
                return Err(Error::Conflict(
                    "broker revision has changed contents".into(),
                ));
            }
        }
        if b.quantity > 0 {
            let active = self.active.as_mut().ok_or_else(|| {
                Error::Unready("filled position has no pinned strategy context".into())
            })?;
            if active.entry.setup.initial_fill_price.is_none() && !b.pending_entry {
                let fill = b.average_price.unwrap();
                let risk = fill - active.entry.stop;
                if risk <= 0. {
                    return Err(Error::Unready("fill risk is not positive".into()));
                }
                active.entry.setup.initial_fill_price = Some(fill);
                active.entry.setup.initial_risk = Some(risk);
            }
        } else if !b.pending_entry {
            if self.broker.as_ref().is_some_and(|p| p.quantity > 0) {
                self.recovery
                    .observe_position(b.at_ns, None, false, false)?;
            }
            self.active = None;
        }
        self.broker = Some(b.clone());
        Ok(())
    }
    pub fn completed(
        &mut self,
        f: &entry::Frame<'_>,
        broker: &PositionObservation,
        gates: &adds::Gates,
        p: &Policy<'_>,
    ) -> Result<Evaluation> {
        self.completed_at(f, broker, gates, p, f.bar.end_ns)
    }
    /// Market geometry retains bar-close coordinates. Account/recovery observations
    /// use the actual evaluation clock and may arrive after that close.
    pub fn completed_at(
        &mut self,
        f: &entry::Frame<'_>,
        broker: &PositionObservation,
        gates: &adds::Gates,
        p: &Policy<'_>,
        evaluated_at_ns: u64,
    ) -> Result<Evaluation> {
        if !f.fresh
            || f.bar.end_ns < self.last_bar_ns
            || evaluated_at_ns < f.bar.end_ns
            || evaluated_at_ns < self.last_intrabar_ns
        {
            return Err(Error::Invalid("candidate completed clock rewind".into()));
        }
        // Distinct policies must not silently disagree on common geometry.
        if p.entry.tick != p.adds.tick
            || p.entry.breakout_buffer_bps != p.adds.buffer_bps
            || p.entry.breakout_buffer_ticks != p.adds.buffer_ticks
            || p.entry.tick != p.protection.tick
            || p.entry.price_only != p.adds.price_only
            || p.entry.price_only != p.protection.price_only
            || content_hash(&p.entry.targets)? != content_hash(&p.protection.levels)?
        {
            return Err(Error::Invalid(
                "candidate policies disagree on shared geometry".into(),
            ));
        }
        let mut next = self.clone();
        next.reconcile(broker, evaluated_at_ns)?;
        if f.bar.end_ns == next.last_bar_ns {
            *self = next;
            return Ok(Evaluation {
                actions: vec![Action::Hold {
                    reason: "duplicate_completed_bar".into(),
                }],
                entry_evidence: None,
                phase_transitioned: false,
            });
        }
        let mut result = Evaluation {
            actions: vec![],
            entry_evidence: None,
            phase_transitioned: false,
        };
        if broker.quantity == 0 {
            if broker.pending_entry {
                result.actions.push(Action::Wait {
                    reason: "entry_fill_pending".into(),
                });
            } else {
                let frame = entry::Frame {
                    recovery: &next.recovery,
                    ..*f
                };
                let evaluated = entry::evaluate_at(&frame, p.entry, evaluated_at_ns)?;
                result.entry_evidence = Some(evaluated.evidence);
                if let Some(proposal) = evaluated.proposal {
                    let adds =
                        adds::State::new(proposal.boundary.clone(), f.bar.close, f.bar.end_ns)?;
                    next.active = Some(Active {
                        entry: proposal.clone(),
                        adds,
                        protection: protection::State::default(),
                    });
                    result.actions.push(Action::Enter(Box::new(proposal)));
                } else {
                    result.actions.push(Action::Wait {
                        reason: evaluated.reason,
                    });
                }
            }
        } else {
            let active = next.active.as_mut().unwrap();
            let contiguous = f.previous.is_some_and(|b| b.end_ns == f.bar.start_ns);
            result.phase_transitioned =
                active
                    .entry
                    .setup
                    .advance_phase(f.bar, f.fresh, p.phase_minimum_progress_r)?;
            let failure = active.entry.setup.entry_failure(
                f.bar,
                f.fresh,
                contiguous,
                p.failure_window_ns,
                p.failure_buffer_ticks,
                p.entry.tick,
            )?;
            if let Some(failure) = &failure {
                active.entry.setup.entry_failure_recovery = Some(failure.reclaim_threshold);
            }
            next.recovery.observe_position(
                evaluated_at_ns,
                Some((
                    &active.entry.setup,
                    broker.stop.unwrap(),
                    f.bar.open.max(f.bar.close),
                )),
                p.preserve_peak,
                p.stop_gain_guard,
            )?;
            next.recovery.retire_breached(f.swings, f.bar, f.fresh)?;
            if failure.is_some() && p.failure_exit_enabled {
                result.actions.push(Action::CancelEntry {
                    reason: "early_setup_failed".into(),
                });
                result.actions.push(Action::Exit {
                    reason: ExitReason::EarlySetupFailed,
                    quantity: broker.quantity,
                    reduce_only: true,
                });
            } else {
                let protection = active.protection.evaluate(
                    &protection::Frame {
                        bar: f.bar,
                        contiguous,
                        bid: f.bid,
                        ask: f.ask,
                        setup: &active.entry.setup,
                        swings: f.swings,
                        levels: f.levels,
                        detector_at_ns: f.admission.detector_at_ns,
                        detector_fingerprint: &f.admission.detector_fingerprint,
                        active_stop: broker.stop.unwrap(),
                        active_target: broker.target.as_ref().unwrap(),
                        regular: f.regular,
                        official_target: f.regular_target,
                    },
                    p.protection,
                )?;
                active.entry.setup.best_close = protection.best_close;
                if let Some(stop) = protection.stop {
                    result.actions.push(Action::ReplaceStop(stop));
                }
                if let Some(target) = protection.target {
                    result.actions.push(Action::ReplaceTarget(target));
                }
                let mut gates = gates.clone();
                gates.no_pending_acquisition &= !broker.pending_entry;
                if result.phase_transitioned {
                    gates.encounter_clear = true;
                }
                let added = active.adds.evaluate(
                    &adds::Frame {
                        bar: f.bar,
                        previous: f.previous,
                        acquisition_levels: f.prior_levels,
                        bid: f.bid,
                        ask: f.ask,
                        vwap: f.vwap,
                        stop: broker.stop.unwrap(),
                        target: broker.target.as_ref().unwrap().price(),
                        gates: &gates,
                    },
                    p.adds,
                )?;
                if let Some(add) = added.proposal {
                    result.actions.push(Action::Add(Box::new(add)));
                }
                if result.actions.is_empty() {
                    result.actions.push(Action::Hold {
                        reason: "structure_valid".into(),
                    });
                }
            }
        }
        next.last_bar_ns = f.bar.end_ns;
        *self = next;
        Ok(result)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_dispatch::Mode;
    const S: u64 = 1_000_000_000;
    fn pending_state() -> State {
        let entry = entry::tests::scenario(|_, _| {}).unwrap().proposal.unwrap();
        let at = entry.setup.confirmed_at_ns;
        let adds =
            adds::State::new(entry.boundary.clone(), entry.setup.entry_bar.close, at).unwrap();
        State {
            active: Some(Active {
                entry,
                adds,
                protection: protection::State::default(),
            }),
            broker: Some(PositionObservation {
                revision: 1,
                at_ns: at,
                quantity: 0,
                average_price: None,
                stop: None,
                target: None,
                pending_entry: true,
            }),
            ..State::default()
        }
    }
    fn acquisition(at: u64) -> AcquisitionObservation {
        AcquisitionObservation {
            at_ns: at,
            price: 10.4,
            ask: 10.41,
            vwap: Some(10.),
            macd_at_ns: Some(at),
            macd_positive: true,
            macd_episode_present: true,
            tradable: true,
            regular_block: false,
            encounter_blocked: false,
            pending_capital: true,
        }
    }
    #[test]
    fn acquisition_expiration_is_exact_and_rejections_do_not_change_fill_state() {
        let mut state = pending_state();
        let at = state.active.as_ref().unwrap().entry.setup.confirmed_at_ns;
        let p = AcquisitionPolicy {
            maximum_macd_age_ns: S,
            confirmation_lifetime_ns: S,
        };
        assert!(state
            .acquisition_update(&acquisition(at + S - 1), &p)
            .unwrap()
            .actions
            .is_empty());
        let result = state.acquisition_update(&acquisition(at + S), &p).unwrap();
        assert!(matches!(result.actions[0], Action::CancelEntry { .. }));
        assert!(result.reasons.iter().any(|r| r == "confirmation_expired"));
        assert!(state
            .active
            .as_ref()
            .unwrap()
            .entry
            .setup
            .initial_fill_price
            .is_none());
        assert!(state.broker.as_ref().unwrap().pending_entry);
    }
    #[test]
    fn encounter_cancellation_is_latched_without_suppressing_protection() {
        let mut state = pending_state();
        state.broker.as_mut().unwrap().quantity = 10;
        let at = state.active.as_ref().unwrap().entry.setup.confirmed_at_ns;
        let p = AcquisitionPolicy {
            maximum_macd_age_ns: S,
            confirmation_lifetime_ns: 10 * S,
        };
        let mut o = acquisition(at);
        o.encounter_blocked = true;
        o.pending_capital = false;
        let result = state.acquisition_update(&o, &p).unwrap();
        assert_eq!(result.actions.len(), 1);
        assert!(result.continue_position_management);
        assert!(state.acquisition_update(&o, &p).unwrap().actions.is_empty());
        o.encounter_blocked = false;
        state.acquisition_update(&o, &p).unwrap();
        o.encounter_blocked = true;
        assert_eq!(state.acquisition_update(&o, &p).unwrap().actions.len(), 1);
    }
    #[test]
    fn stale_macd_cancels_capital_wait_and_future_clock_does_not_mutate() {
        let mut state = pending_state();
        let at = state.active.as_ref().unwrap().entry.setup.confirmed_at_ns;
        let p = AcquisitionPolicy {
            maximum_macd_age_ns: S,
            confirmation_lifetime_ns: 10 * S,
        };
        let mut o = acquisition(at + 2 * S);
        o.macd_at_ns = Some(at);
        assert_eq!(state.acquisition_update(&o, &p).unwrap().actions.len(), 1);
        let before = content_hash(&state).unwrap();
        o.macd_at_ns = Some(o.at_ns + 1);
        assert!(state.acquisition_update(&o, &p).is_err());
        assert_eq!(content_hash(&state).unwrap(), before);
    }
    #[test]
    fn composed_entry_fill_and_transaction_use_one_candidate_state() {
        entry::tests::scenario(|f, ep| {
            let ap = adds::Policy {
                tick: ep.tick,
                buffer_ticks: ep.breakout_buffer_ticks,
                buffer_bps: ep.breakout_buffer_bps,
                maximum_chase_bps: ep.maximum_chase_bps,
                maximum_upper_wick_fraction: 0.5,
                price_only: ep.price_only,
                tranche_count: 3,
                maximum_pending_levels: 16,
            };
            let pp = protection::Policy {
                tick: ep.tick,
                price_only: ep.price_only,
                minimum_progress_r: 0.,
                requires_current_gain: false,
                current_gain_requires_bid: false,
                requires_breakout: false,
                activation_r: 0.,
                maximum_pending_targets: 16,
                levels: ep.targets.clone(),
            };
            let policy = Policy {
                maximum_completed_bar_age_ns: 100_000_000,
                entry: ep,
                adds: &ap,
                protection: &pp,
                phase_minimum_progress_r: 0.,
                failure_window_ns: 0,
                failure_buffer_ticks: 0.,
                failure_exit_enabled: false,
                preserve_peak: true,
                stop_gain_guard: true,
            };
            let mut gates = adds::Gates {
                at_ns: f.bar.end_ns,
                detector_fresh: true,
                regular_allowed: true,
                no_pending_acquisition: true,
                permission: true,
                tradable: true,
                macd_ready: true,
                no_pending_failed_attempt: true,
                encounter_clear: true,
                range_breakout_allowed: true,
            };
            let flat = PositionObservation {
                revision: 1,
                at_ns: f.bar.end_ns,
                quantity: 0,
                average_price: None,
                stop: None,
                target: None,
                pending_entry: false,
            };
            let intrabar_policy = AcquisitionPolicy {
                maximum_macd_age_ns: S,
                confirmation_lifetime_ns: 10 * S,
            };
            let market = crate::market_structure::tests::runtime_with_timeframes(
                100,
                vec![crate::market_structure::Timeframe {
                    interval_ns: 5 * S,
                    macd_periods: (12, 26, 9),
                    maximum_bars: 20,
                }],
            );
            let feature_config = crate::candidate_features::Config {
                setup: crate::strategy_setup::SetupSettings {
                    range_ns: 30 * S,
                    minimum_bars: 5,
                    maximum_gap_ns: 0,
                },
                forming_macd: true,
                minimum_range_pct: 0.,
                minimum_progress_pct: 0.,
                maximum_quote_age_ns: S,
                maximum_completed_bar_age_ns: policy.maximum_completed_bar_age_ns,
                maximum_levels: 100,
            };
            let features =
                crate::candidate_features::State::new(&market, feature_config.clone()).unwrap();
            let mut runtime = crate::candidate_runtime::Runtime::new(
                crate::strategy_dispatch::Scope {
                    run_id: "r".into(),
                    mode: Mode::Backtest,
                    account: "a".into(),
                    strategy_instance: "v7".into(),
                    instrument: 1,
                    code_hash: "pinned".into(),
                    config_hash: crate::candidate_runtime::configuration_hash(
                        &policy,
                        &intrabar_policy,
                        &features,
                        f.recovery_policy,
                    )
                    .unwrap(),
                },
                State::default(),
                1024 * 1024,
            )
            .unwrap();
            let safety = crate::strategy_dispatch::Safety {
                position_quantity: 0,
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
            let input = crate::strategy_dispatch::InputBoundary {
                event_id: "e".into(),
                event_time_ns: f.bar.end_ns,
                available_at_ns: f.bar.end_ns,
                evaluated_at_ns: f.bar.end_ns,
                source_sequence: 1,
                feature_hash: "f".into(),
            };
            let mut changed_feature_config = feature_config.clone();
            changed_feature_config.setup.range_ns += S;
            let changed_features =
                crate::candidate_features::State::new(&market, changed_feature_config).unwrap();
            assert_ne!(
                features.configuration_hash(),
                changed_features.configuration_hash()
            );
            assert!(runtime
                .completed(
                    input.clone(),
                    &safety,
                    f,
                    &flat,
                    &gates,
                    &policy,
                    &intrabar_policy,
                    &changed_features
                )
                .is_err());
            assert!(runtime.pending_batch().is_none());
            let mut conflicting_age_config = feature_config.clone();
            conflicting_age_config.maximum_completed_bar_age_ns += 1;
            let conflicting_age =
                crate::candidate_features::State::new(&market, conflicting_age_config).unwrap();
            assert!(crate::candidate_runtime::configuration_hash(
                &policy,
                &intrabar_policy,
                &conflicting_age,
                f.recovery_policy
            )
            .is_err());
            let decision = runtime
                .completed(
                    input.clone(),
                    &safety,
                    f,
                    &flat,
                    &gates,
                    &policy,
                    &intrabar_policy,
                    &features,
                )
                .unwrap();
            assert!(matches!(decision.actions[0], Action::Enter(_)));
            let mut other_scope = decision.scope.clone();
            other_scope.instrument = 2;
            let mut other_instrument =
                crate::candidate_runtime::Runtime::new(other_scope, State::default(), 1024 * 1024)
                    .unwrap();
            assert!(other_instrument
                .completed(
                    input.clone(),
                    &safety,
                    f,
                    &flat,
                    &gates,
                    &policy,
                    &intrabar_policy,
                    &features
                )
                .is_err());
            assert!(other_instrument.pending_batch().is_none());
            // The candle itself is still fresh. Its admission operands can expire
            // independently during the delay before an account evaluates it.
            for stale_macd in [false, true] {
                let mut delayed = crate::candidate_runtime::Runtime::new(
                    decision.scope.clone(),
                    State::default(),
                    1024 * 1024,
                )
                .unwrap();
                let mut admission = f.admission.clone();
                if stale_macd {
                    admission.macd_at_ns = Some(f.bar.end_ns - policy.entry.maximum_macd_age_ns);
                } else {
                    admission.at_ns = f.bar.end_ns - policy.entry.maximum_admission_age_ns;
                }
                let frame = entry::Frame {
                    admission: &admission,
                    ..*f
                };
                let mut delayed_input = input.clone();
                delayed_input.evaluated_at_ns += 1;
                let rejected = delayed
                    .completed(
                        delayed_input,
                        &safety,
                        &frame,
                        &flat,
                        &gates,
                        &policy,
                        &intrabar_policy,
                        &features,
                    )
                    .unwrap();
                assert!(matches!(&rejected.actions[0], Action::Wait { reason }
                    if reason == if stale_macd { "waiting_for_completed_1s_and_bullish_5s_macd" }
                    else { "tradability_incomplete" }));
                assert!(delayed.state().active.is_none());
                assert!(delayed.pending_batch().is_some());
            }
            for future_account in [false, true] {
                let mut delayed = crate::candidate_runtime::Runtime::new(
                    decision.scope.clone(),
                    State::default(),
                    1024 * 1024,
                )
                .unwrap();
                let mut delayed_input = input.clone();
                delayed_input.evaluated_at_ns += 2;
                let mut current_account = flat.clone();
                current_account.at_ns += if future_account { 3 } else { 1 };
                let evaluated = delayed.completed(
                    delayed_input,
                    &safety,
                    f,
                    &current_account,
                    &gates,
                    &policy,
                    &intrabar_policy,
                    &features,
                );
                if future_account {
                    assert!(evaluated.is_err());
                    assert!(delayed.pending_batch().is_none());
                } else {
                    let evaluated = evaluated.unwrap();
                    assert!(matches!(evaluated.actions[0], Action::Enter(_)));
                    assert_eq!(evaluated.input.event_time_ns, f.bar.end_ns);
                    assert_eq!(evaluated.input.evaluated_at_ns, f.bar.end_ns + 2);
                }
            }
            for pending_entry in [false, true] {
                let mut stale_runtime = crate::candidate_runtime::Runtime::new(
                    decision.scope.clone(),
                    State::default(),
                    1024 * 1024,
                )
                .unwrap();
                let mut stale_input = input.clone();
                stale_input.evaluated_at_ns += policy.maximum_completed_bar_age_ns;
                let mut stale_safety = safety.clone();
                stale_safety.pending_entry = pending_entry;
                let mut stale_broker = flat.clone();
                stale_broker.pending_entry = pending_entry;
                let rejected = stale_runtime
                    .completed(
                        stale_input,
                        &stale_safety,
                        f,
                        &stale_broker,
                        &gates,
                        &policy,
                        &intrabar_policy,
                        &features,
                    )
                    .unwrap();
                assert!(match &rejected.actions[0] {
                    Action::CancelEntry { reason } if pending_entry =>
                        reason == "completed_bar_stale_at_evaluation",
                    Action::Wait { reason } if !pending_entry =>
                        reason == "completed_bar_stale_at_evaluation",
                    _ => false,
                });
                assert!(stale_runtime.state().active.is_none());
                assert!(stale_runtime.pending_batch().is_some());
            }
            assert!(runtime.state().active.is_none());
            let retried = runtime
                .completed(
                    input.clone(),
                    &safety,
                    f,
                    &flat,
                    &gates,
                    &policy,
                    &intrabar_policy,
                    &features,
                )
                .unwrap();
            assert_eq!(retried.decision_id, decision.decision_id);
            let mut changed = intrabar_policy.clone();
            changed.confirmation_lifetime_ns += 1;
            assert!(runtime
                .completed(
                    input.clone(),
                    &safety,
                    f,
                    &flat,
                    &gates,
                    &policy,
                    &changed,
                    &features
                )
                .is_err());
            let mut conflicting_safety = safety.clone();
            conflicting_safety.pending_entry = true;
            assert!(runtime
                .completed(
                    input.clone(),
                    &conflicting_safety,
                    f,
                    &flat,
                    &gates,
                    &policy,
                    &intrabar_policy,
                    &features,
                )
                .is_err());
            let records = runtime.pending_batch().unwrap().records().to_vec();
            let committed = runtime.acknowledge(&records).unwrap();
            let allocation = crate::decision_orders::Allocation {
                account: "a".into(),
                instrument: 1,
                quantity: 100,
                price_scale: 4,
                tick: 100,
                entry_limit: 104000,
                deadline_ns: f.bar.end_ns + S,
            };
            let risk = crate::orders::RiskPolicy {
                band_provider: 1,
                band_session: 20260915,
                band_buffer_ticks: 3,
                max_band_age_ns: S,
            };
            let plan = crate::decision_orders::bracket(
                &committed,
                0,
                &allocation,
                f.bar.end_ns,
                false,
                None,
                &risk,
            )
            .unwrap();
            assert_eq!(plan.bracket.quantity, 100);
            assert!(plan.bracket.stop.is_some() && plan.bracket.target.is_some());
            let mut wrong = allocation.clone();
            wrong.account = "other".into();
            assert!(crate::decision_orders::bracket(
                &committed,
                0,
                &wrong,
                f.bar.end_ns,
                false,
                None,
                &risk
            )
            .is_err());
            assert!(crate::decision_orders::bracket(
                &committed,
                0,
                &allocation,
                f.bar.end_ns,
                true,
                None,
                &risk
            )
            .is_err());
            let mut state = runtime.state().clone();
            let mut live_input = input.clone();
            live_input.event_id = "intrabar".into();
            live_input.source_sequence = 2;
            live_input.event_time_ns += 1;
            live_input.available_at_ns += 1;
            live_input.evaluated_at_ns += 1;
            let mut pending_broker = flat.clone();
            pending_broker.revision = 2;
            pending_broker.at_ns += 1;
            pending_broker.pending_entry = true;
            let mut pending_safety = safety.clone();
            pending_safety.pending_entry = true;
            let mut observation = acquisition(live_input.evaluated_at_ns);
            observation.encounter_blocked = true;
            let before_mismatch = content_hash(runtime.state()).unwrap();
            assert!(runtime
                .intrabar(
                    live_input.clone(),
                    &pending_safety,
                    &observation,
                    &pending_broker,
                    f.bar.open.max(f.bar.close),
                    &policy,
                    &intrabar_policy,
                    &changed_features,
                    f.recovery_policy
                )
                .is_err());
            assert!(runtime.pending_batch().is_none());
            assert_eq!(content_hash(runtime.state()).unwrap(), before_mismatch);
            let cancellation = runtime
                .intrabar(
                    live_input.clone(),
                    &pending_safety,
                    &observation,
                    &pending_broker,
                    f.bar.open.max(f.bar.close),
                    &policy,
                    &intrabar_policy,
                    &features,
                    f.recovery_policy,
                )
                .unwrap();
            assert!(matches!(
                cancellation.actions[0],
                Action::CancelEntry { .. }
            ));
            assert_eq!(
                content_hash(runtime.state()).unwrap(),
                content_hash(&state).unwrap()
            );
            let retried = runtime
                .intrabar(
                    live_input,
                    &pending_safety,
                    &observation,
                    &pending_broker,
                    f.bar.open.max(f.bar.close),
                    &policy,
                    &intrabar_policy,
                    &features,
                    f.recovery_policy,
                )
                .unwrap();
            assert_eq!(retried.decision_id, cancellation.decision_id);
            let rows = runtime.pending_batch().unwrap().records().to_vec();
            runtime.acknowledge(&rows).unwrap();
            let proposal = state.active.as_ref().unwrap().entry.clone();
            let target = proposal
                .target_selection
                .clone()
                .map(|t| protection::ActiveTarget::Structure(Box::new(t)))
                .unwrap();
            let filled = PositionObservation {
                revision: 2,
                at_ns: f.bar.end_ns + S,
                quantity: 100,
                average_price: Some(10.4),
                stop: Some(proposal.stop),
                target: Some(target),
                pending_entry: false,
            };
            let mut b = f.bar.clone();
            b.start_ns += S;
            b.end_ns += S;
            let mut admission = f.admission.clone();
            admission.at_ns = b.end_ns;
            admission.detector_at_ns = Some(b.end_ns);
            admission.macd_at_ns = Some(b.end_ns);
            let frame = entry::Frame {
                bar: &b,
                previous: Some(f.bar),
                admission: &admission,
                ..*f
            };
            gates.at_ns = b.end_ns;
            state.completed(&frame, &filled, &gates, &policy).unwrap();
            assert_eq!(
                state
                    .active
                    .as_ref()
                    .unwrap()
                    .entry
                    .setup
                    .initial_fill_price,
                Some(10.4)
            );
            assert!(state.recovery.held.is_some());
            let before = content_hash(&state).unwrap();
            let mut conflicting = filled.clone();
            conflicting.quantity = 90;
            assert!(state
                .completed(&frame, &conflicting, &gates, &policy)
                .is_err());
            assert_eq!(content_hash(&state).unwrap(), before);
        })
        .unwrap();
    }
}
