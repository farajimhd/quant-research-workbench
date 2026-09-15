//! Shared setup/recovery entry evaluator for the pinned V7 candidate.
//! Upstream admission is explicit evidence; output is not an authorized order.
use crate::market::Bar;
use crate::strategy_lifecycle::{
    Phase, PositionSetup, RecoveryDecision, RecoveryPolicy, RecoveryState,
};
use crate::strategy_setup::Range;
use crate::strategy_targets::{
    initial_swing, resistance, stop_below, valid_level, CausalLevels, Policy as TargetPolicy,
    Swing, SwingSelection, Target, TargetLevel,
};
use crate::v7_encounters::ActiveRole;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Policy {
    pub tick: f64,
    pub price_only: bool,
    pub zone_fraction: f64,
    pub minimum_body_bps: f64,
    pub maximum_chase_bps: f64,
    pub minimum_quote_clearance_spreads: f64,
    pub early_base: bool,
    pub base_maximum_age_ns: u64,
    pub base_maximum_risk_pct: f64,
    pub base_maximum_range_pct: f64,
    pub base_maximum_extension_fraction: f64,
    pub recovery_compact_range_pct: f64,
    pub episode_high_entry: bool,
    pub breakout_buffer_bps: f64,
    pub breakout_buffer_ticks: f64,
    pub maximum_macd_age_ns: u64,
    pub maximum_admission_age_ns: u64,
    pub targets: TargetPolicy,
}
/// Produced by admission, not by chart state. Engine must bind this to the event.
#[derive(Debug, Clone)]
pub struct Admission {
    pub at_ns: u64,
    pub permissions: bool,
    pub session_open: bool,
    pub tradable: bool,
    pub encounter_blocked: bool,
    pub regular_block: Option<String>,
    pub macd_at_ns: Option<u64>,
    pub macd_positive: bool,
    pub detector_at_ns: Option<u64>,
    pub detector_fingerprint: String,
    pub activity_block: Option<String>,
}
pub struct Frame<'a> {
    pub bar: &'a Bar,
    pub previous: Option<&'a Bar>,
    pub bid: f64,
    pub ask: f64,
    pub fresh: bool,
    pub hod: Option<f64>,
    pub prior_hod: Option<f64>,
    pub vwap: Option<f64>,
    pub range: Option<&'a Range>,
    pub prior_episode_high: Option<f64>,
    pub prior_levels: &'a [TargetLevel],
    pub levels: &'a [TargetLevel],
    pub swings: &'a [Swing],
    pub regular_target: Option<f64>,
    pub regular: bool,
    pub admission: &'a Admission,
    pub recovery: &'a RecoveryState,
    pub recovery_policy: &'a RecoveryPolicy,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EarlyBaseEvidence {
    pub risk_pct: f64,
    pub range_pct: f64,
    pub extension_limit: f64,
    pub passed: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Evidence {
    pub at_ns: u64,
    pub zone: Option<(f64, f64)>,
    pub early_base: Option<EarlyBaseEvidence>,
    pub reference: Option<TargetLevel>,
    pub swing: Option<Swing>,
    pub recovery: Option<RecoveryDecision>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Proposal {
    pub reason: String,
    pub stop: f64,
    pub target: f64,
    pub maximum_buy_price: f64,
    pub target_selection: Option<Target>,
    pub boundary: TargetLevel,
    pub swing: Swing,
    pub setup: PositionSetup,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Evaluation {
    pub reason: String,
    pub proposal: Option<Proposal>,
    pub evidence: Evidence,
}
fn positive(p: f64) -> bool {
    p.is_finite() && p > 0.
}
fn valid_bar(b: &Bar) -> bool {
    b.start_ns < b.end_ns
        && [b.open, b.high, b.low, b.close].into_iter().all(positive)
        && b.low <= b.open.min(b.close)
        && b.high >= b.open.max(b.close)
}
fn wait(reason: &str, evidence: Evidence) -> Result<Evaluation> {
    Ok(Evaluation {
        reason: reason.into(),
        proposal: None,
        evidence,
    })
}
pub fn evaluate(f: &Frame<'_>, p: &Policy) -> Result<Evaluation> {
    let b = f.bar;
    let now = b.end_ns;
    let a = f.admission;
    if !valid_bar(b)
        || b.end_ns.checked_sub(b.start_ns) != Some(1_000_000_000)
        || ![f.bid, f.ask, p.tick].into_iter().all(positive)
        || f.bid > f.ask
        || !p.zone_fraction.is_finite()
        || !(0. ..=1.).contains(&p.zone_fraction)
        || [
            p.minimum_body_bps,
            p.maximum_chase_bps,
            p.minimum_quote_clearance_spreads,
            p.base_maximum_risk_pct,
            p.base_maximum_range_pct,
            p.base_maximum_extension_fraction,
            p.recovery_compact_range_pct,
            p.breakout_buffer_bps,
            p.breakout_buffer_ticks,
        ]
        .into_iter()
        .any(|v| !v.is_finite() || v < 0.)
        || f.previous
            .is_some_and(|v| !valid_bar(v) || v.end_ns > b.start_ns)
        || a.at_ns > now
        || a.macd_at_ns.is_some_and(|v| v > now)
        || a.detector_at_ns.is_some_and(|v| v > now)
        || f.regular_target.is_some_and(|v| !positive(v))
    {
        return Err(Error::Invalid(
            "invalid entry inputs or future evidence".into(),
        ));
    }
    if let Some(r) = f.range {
        if !positive(r.low)
            || !positive(r.high)
            || r.low > r.high
            || r.start_ns >= r.end_ns
            || r.end_ns > b.start_ns
            || r.count == 0
        {
            return Err(Error::Invalid("invalid causal setup range".into()));
        }
    }
    for l in f.prior_levels {
        valid_level(l)?;
        if l.geometry.confirmed_at_ns > b.start_ns {
            return Err(Error::Invalid("future prior entry level".into()));
        }
    }
    let mut e = Evidence {
        at_ns: now,
        zone: None,
        early_base: None,
        reference: None,
        swing: None,
        recovery: None,
    };
    if a.encounter_blocked {
        return wait("unresolved_level_rejection", e);
    }
    if !a.permissions {
        return wait("entry_permission_closed", e);
    }
    if !a.session_open {
        return wait("outside_entry_session", e);
    }
    if !f.fresh || !a.macd_positive || a.macd_at_ns.is_none_or(|v| now - v > p.maximum_macd_age_ns)
    {
        return wait("waiting_for_completed_1s_and_bullish_5s_macd", e);
    }
    if let Some(reason) = &a.regular_block {
        return wait(reason, e);
    }
    if !a.tradable || now - a.at_ns > p.maximum_admission_age_ns {
        return wait("tradability_incomplete", e);
    }
    if a.detector_at_ns != Some(now) || a.detector_fingerprint.is_empty() {
        return wait("certified_detector_unavailable", e);
    }
    let Some(previous) = f.previous.filter(|v| v.end_ns == b.start_ns) else {
        return wait("hod_history_or_vwap_gate", e);
    };
    let Some((_prior_hod, vwap)) = f
        .prior_hod
        .zip(f.vwap)
        .filter(|(h, v)| positive(*h) && positive(*v) && b.close > *v)
    else {
        return wait("hod_history_or_vwap_gate", e);
    };
    let decision_bid = if p.price_only { b.close } else { f.bid };
    let decision_ask = if p.price_only { b.close } else { f.ask };
    e.zone = f
        .hod
        .filter(|h| positive(*h) && *h > vwap)
        .map(|h| (vwap + (1. - p.zone_fraction) * (h - vwap), h));
    let mut early = None;
    if p.early_base {
        if let Some(r) = f.range {
            if let Some(s) = initial_swing(
                f.swings,
                b.close.min(decision_bid),
                now,
                &SwingSelection {
                    closest: true,
                    price_only: p.price_only,
                    pivot_not_before_ns: r.start_ns,
                    maximum_age_ns: Some(p.base_maximum_age_ns),
                    ..Default::default()
                },
            )? {
                let stop = stop_below(s.lower, &p.targets, p.tick)?;
                let risk_pct = (decision_ask - stop) / decision_ask * 100.;
                let range_pct = (r.high / r.low - 1.) * 100.;
                let extension_limit = r.high + p.base_maximum_extension_fraction * (r.high - r.low);
                let passed = b.close > previous.close
                    && b.close >= b.open
                    && risk_pct <= p.base_maximum_risk_pct
                    && range_pct <= p.base_maximum_range_pct
                    && b.close <= extension_limit;
                e.early_base = Some(EarlyBaseEvidence {
                    risk_pct,
                    range_pct,
                    extension_limit,
                    passed,
                });
                if passed {
                    early = Some(s);
                }
            }
        }
    }
    if early.is_none()
        && !e.zone.is_some_and(|(lo, hi)| {
            lo <= b.close.min(decision_bid) && b.close.max(decision_ask) <= hi
        })
    {
        return wait("outside_v7_hod_entry_zone", e);
    }
    // Setup mode selects the nearest prior acquisition level above current price.
    let boundary = if e.zone.is_some() {
        f.prior_levels
            .iter()
            .filter(|l| {
                (resistance(l, &p.targets) || l.geometry.role == ActiveRole::Transition)
                    && l.geometry.price >= b.close
            })
            .min_by(|a, b| a.geometry.price.total_cmp(&b.geometry.price))
    } else {
        None
    };
    let Some(boundary) = boundary else {
        return wait("no_v7_resistance_in_entry_zone", e);
    };
    e.reference = Some(boundary.clone());
    if b.close < b.open {
        return wait("red_breakout_candle", e);
    }
    if b.close - b.open + 1e-9 < b.open * p.minimum_body_bps / 10000. {
        return wait("setup_body_below_minimum", e);
    }
    if let Some(reason) = &a.activity_block {
        return wait(reason, e);
    }
    let Some(range) = f.range else {
        return wait(
            if p.episode_high_entry {
                "waiting_for_episode_high_break"
            } else {
                "waiting_for_rising_setup_below_range_high"
            },
            e,
        );
    };
    if p.episode_high_entry {
        if !f
            .prior_episode_high
            .is_some_and(|h| positive(h) && b.close > h)
        {
            return wait("waiting_for_episode_high_break", e);
        }
    } else if early.is_none() && (b.close <= previous.close || b.close >= range.high) {
        return wait("waiting_for_rising_setup_below_range_high", e);
    }
    let selected = if f.regular {
        None
    } else {
        CausalLevels::new(f.levels, now)?.available(
            decision_ask.max(b.close),
            0.,
            None,
            None,
            &p.targets,
            p.tick,
        )?
    };
    let target = if f.regular {
        f.regular_target
    } else {
        selected.as_ref().map(|v| v.price)
    };
    let Some(target) = target else {
        return wait("qualified_target_unavailable", e);
    };
    let swing = early.or(initial_swing(
        f.swings,
        decision_bid,
        now,
        &SwingSelection {
            price_only: p.price_only,
            ..Default::default()
        },
    )?);
    let Some(swing) = swing else {
        return wait("confirmed_local_swing_low_unavailable", e);
    };
    e.swing = Some(swing.clone());
    let mut recovery_policy = f.recovery_policy.clone();
    recovery_policy.tight_base = p.recovery_compact_range_pct > 0.
        && early.is_some()
        && (range.high / range.low - 1.) * 100. <= p.recovery_compact_range_pct;
    let recovery = f
        .recovery
        .permission(swing, b, Some(range), &recovery_policy)?;
    e.recovery = Some(recovery);
    if recovery != RecoveryDecision::Building {
        return wait(
            match recovery {
                RecoveryDecision::BreachedSetupSwing => "breached_setup_swing",
                RecoveryDecision::WaitingForNewSupportAfterExit => {
                    "waiting_for_new_support_after_exit"
                }
                RecoveryDecision::WaitingForFailedSetupReclaim => {
                    "waiting_for_failed_setup_reclaim"
                }
                _ => "waiting_for_post_move_recovery_or_higher_base",
            },
            e,
        );
    }
    let stop = stop_below(swing.lower, &p.targets, p.tick)?;
    if p.minimum_quote_clearance_spreads > 0.
        && (f.bid - stop <= 0.
            || f.bid - stop + 1e-9 < p.minimum_quote_clearance_spreads * (f.ask - f.bid))
    {
        return wait("setup_stop_inside_quote_noise", e);
    }
    let ceiling = (f.ask * (1. + p.maximum_chase_bps / 10000.)).min(target - p.tick);
    if !(0. < stop
        && stop < decision_bid
        && decision_bid <= decision_ask
        && decision_ask <= ceiling)
    {
        return wait("invalid_stop_or_entry_price", e);
    }
    let threshold = range.high
        + (range.high * p.breakout_buffer_bps / 10000.).max(p.tick * p.breakout_buffer_ticks);
    if !threshold.is_finite() {
        return Err(Error::Invalid("breakout threshold overflow".into()));
    }
    let reason = if early.is_some() {
        "v7_fresh_base_entry"
    } else if p.episode_high_entry {
        "v7_episode_high_entry"
    } else {
        "v7_early_setup_entry"
    };
    let proposal = Proposal {
        reason: reason.into(),
        stop,
        target,
        maximum_buy_price: ceiling,
        target_selection: selected,
        boundary: boundary.clone(),
        swing: swing.clone(),
        setup: PositionSetup {
            confirmed_at_ns: now,
            phase: Phase::Building,
            breakout_threshold: threshold,
            breakout_at_ns: None,
            entry_bar: b.clone(),
            initial_fill_price: None,
            initial_risk: None,
            best_close: b.close,
            entry_failure_recovery: None,
        },
    };
    Ok(Evaluation {
        reason: reason.into(),
        proposal: Some(proposal),
        evidence: e,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_encounters::Level;
    const S: u64 = 1_000_000_000;
    fn scenario(edit: impl FnOnce(&mut Frame<'_>, &mut Policy)) -> Result<Evaluation> {
        let b = Bar {
            start_ns: 30 * S,
            end_ns: 31 * S,
            open: 10.3,
            close: 10.4,
            high: 10.45,
            low: 10.2,
            volume: 10.,
            notional: 104.,
            trades: 10,
        };
        let previous = Bar {
            start_ns: 29 * S,
            end_ns: 30 * S,
            close: 10.3,
            ..b.clone()
        };
        let range = Range {
            high: 10.6,
            low: 10.,
            start_ns: 5 * S,
            end_ns: 30 * S,
            count: 25,
        };
        let levels = [TargetLevel {
            geometry: Level {
                id: "r".into(),
                price: 10.5,
                lower: 10.45,
                upper: 10.55,
                role: ActiveRole::Resistance,
                confirmed_at_ns: 10 * S,
            },
            historical: true,
            transition_from: None,
            synthetic: false,
        }];
        let swings = [Swing {
            id: "s".into(),
            price: 10.1,
            lower: 10.05,
            upper: 10.15,
            pivot_at_ns: 20 * S,
            confirmed_at_ns: 25 * S,
            support: true,
            active: true,
        }];
        let admission = Admission {
            at_ns: 31 * S,
            permissions: true,
            session_open: true,
            tradable: true,
            encounter_blocked: false,
            regular_block: None,
            macd_at_ns: Some(31 * S),
            macd_positive: true,
            detector_at_ns: Some(31 * S),
            detector_fingerprint: "seed-and-stream-hash".into(),
            activity_block: None,
        };
        let recovery = RecoveryState::default();
        let rp = RecoveryPolicy::default();
        let mut f = Frame {
            bar: &b,
            previous: Some(&previous),
            bid: 10.39,
            ask: 10.41,
            fresh: true,
            hod: Some(10.5),
            prior_hod: Some(10.5),
            vwap: Some(10.),
            range: Some(&range),
            prior_episode_high: Some(10.3),
            prior_levels: &levels,
            levels: &levels,
            swings: &swings,
            regular_target: None,
            regular: false,
            admission: &admission,
            recovery: &recovery,
            recovery_policy: &rp,
        };
        let mut p = Policy {
            tick: 0.01,
            price_only: true,
            zone_fraction: 0.3,
            minimum_body_bps: 0.,
            maximum_chase_bps: 20.,
            minimum_quote_clearance_spreads: 1.,
            early_base: false,
            base_maximum_age_ns: 30 * S,
            base_maximum_risk_pct: 10.,
            base_maximum_range_pct: 10.,
            base_maximum_extension_fraction: 0.5,
            recovery_compact_range_pct: 0.,
            episode_high_entry: false,
            breakout_buffer_bps: 10.,
            breakout_buffer_ticks: 1.,
            maximum_macd_age_ns: S,
            maximum_admission_age_ns: S,
            targets: TargetPolicy {
                distance_fraction: 0.1,
                offset_ticks: 1.,
                stop_buffer_bps: 10.,
                all_origins: true,
                encounter_transitions: true,
            },
        };
        edit(&mut f, &mut p);
        evaluate(&f, &p)
    }
    #[test]
    fn actual_entry_composes_range_support_recovery_and_target() {
        let result = scenario(|_, _| {}).unwrap();
        assert_eq!(result.reason, "v7_early_setup_entry");
        let proposal = result.proposal.unwrap();
        assert_eq!(proposal.swing.id, "s");
        assert_eq!(proposal.boundary.geometry.id, "r");
        assert!(proposal.stop < 10.39 && proposal.target > 10.41);
        assert!(proposal.setup.initial_fill_price.is_none());
        assert_eq!(proposal.setup.breakout_threshold, 10.6 + 0.0106);
    }
    #[test]
    fn price_only_does_not_bypass_real_quote_noise() {
        let result = scenario(|f, _| {
            f.bid = 10.0;
            f.ask = 10.5;
        })
        .unwrap();
        assert_eq!(result.reason, "setup_stop_inside_quote_noise");
        assert!(result.proposal.is_none());
    }
    #[test]
    fn regular_requires_explicit_target_and_never_estimates() {
        assert_eq!(
            scenario(|f, _| {
                f.regular = true;
            })
            .unwrap()
            .reason,
            "qualified_target_unavailable"
        );
        let result = scenario(|f, _| {
            f.regular = true;
            f.regular_target = Some(11.);
        })
        .unwrap();
        assert_eq!(result.proposal.unwrap().target, 11.);
    }
    #[test]
    fn early_base_is_explicit_and_never_invents_a_missing_range() {
        assert_eq!(
            scenario(|_, p| {
                p.early_base = true;
            })
            .unwrap()
            .reason,
            "v7_fresh_base_entry"
        );
        let result = scenario(|f, p| {
            p.early_base = true;
            f.range = None;
        })
        .unwrap();
        assert!(result.proposal.is_none());
    }
    #[test]
    fn stale_detector_and_future_prior_levels_block() {
        scenario(|f, p| {
            let mut a = f.admission.clone();
            a.detector_at_ns = Some(f.bar.start_ns);
            let frame = Frame {
                admission: &a,
                ..*f
            };
            assert_eq!(
                evaluate(&frame, p).unwrap().reason,
                "certified_detector_unavailable"
            );
            let mut levels = f.prior_levels.to_vec();
            levels[0].geometry.confirmed_at_ns = f.bar.end_ns;
            let frame = Frame {
                prior_levels: &levels,
                ..*f
            };
            assert!(evaluate(&frame, p).is_err());
        })
        .unwrap();
        let result = scenario(|f, _| {
            f.fresh = false;
        })
        .unwrap();
        assert_eq!(
            result.reason,
            "waiting_for_completed_1s_and_bullish_5s_macd"
        );
        assert!(scenario(|f, _| {
            f.ask = f64::NAN;
        })
        .is_err());
    }
}
