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
}
pub struct Policy<'a> {
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
        if !f.fresh || f.bar.end_ns < self.last_bar_ns {
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
        next.reconcile(broker, f.bar.end_ns)?;
        if f.bar.end_ns == next.last_bar_ns {
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
                let evaluated = entry::evaluate(&frame, p.entry)?;
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
                f.bar.end_ns,
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
            let mut runtime = crate::strategy_transaction::Runtime::new(
                crate::strategy_dispatch::Scope {
                    run_id: "r".into(),
                    mode: Mode::Backtest,
                    account: "a".into(),
                    strategy_instance: "v7".into(),
                    instrument: 1,
                    code_hash: "pinned".into(),
                    config_hash: "effective".into(),
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
            let decision = runtime
                .prepare(input, &safety, "proof".into(), |state| {
                    Ok(state.completed(f, &flat, &gates, &policy)?.actions)
                })
                .unwrap();
            assert!(matches!(decision.actions[0], Action::Enter(_)));
            assert!(runtime.committed_state().active.is_none());
            let records = runtime.pending_batch().unwrap().records().to_vec();
            runtime.acknowledge(&records).unwrap();
            let mut state = runtime.committed_state().clone();
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
