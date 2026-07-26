use crate::bars::BarRow;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub const MARKET_SIGNAL_SCHEMA_VERSION: u16 = 2;
pub const MARKET_SIGNAL_ENGINE_VERSION: &str = "qmd-market-signal-v1";

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MarketSignalEvent {
    pub schema_version: u16,
    pub engine_version: String,
    pub event_id: String,
    pub signal_id: String,
    pub signal_key: String,
    pub producer: String,
    #[serde(default = "market_domain")]
    pub domain: String,
    pub ticker: String,
    pub working_timeframe: String,
    #[serde(default)]
    pub clock: SignalClock,
    pub confirmation_timeframe: Option<String>,
    pub observed_at: DateTime<Utc>,
    pub effective_at: DateTime<Utc>,
    pub state: String,
    pub direction: String,
    pub score: f64,
    pub confidence: f64,
    pub trigger_reason: String,
    pub resolution_reason: String,
    pub reference_price: f64,
    pub invalidation_price: Option<f64>,
    pub expires_at: Option<DateTime<Utc>>,
    pub evidence: MarketSignalEvidence,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct SignalClock {
    pub input_basis: String,
    pub calculation_window: String,
    pub evaluation_mode: String,
    pub update_trigger: String,
    pub publication_cadence: String,
    pub publication_interval_ms: Option<u64>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct MarketSignalEvidence {
    pub close: f64,
    pub high: f64,
    pub low: f64,
    pub vwap: f64,
    pub price_change_pct: f64,
    pub volume: f64,
    pub dollar_volume: f64,
    pub trade_rate: f64,
    pub quote_rate: f64,
    pub tape_imbalance: f64,
    pub spread_bps: f64,
    pub liquidity_score: f64,
    pub estimated_luld_active: bool,
    pub estimated_luld_state: String,
}

#[derive(Clone, Debug)]
struct Candidate {
    key: &'static str,
    confirmation_timeframe: Option<&'static str>,
    direction: &'static str,
    score: f64,
    confidence: f64,
    reason: &'static str,
    invalidation_price: Option<f64>,
}

#[derive(Clone, Debug)]
struct SignalSeriesState {
    session_date: String,
    session_high: f64,
    previous: BarRow,
}

#[derive(Default)]
pub struct MarketSignalEngine {
    active: HashMap<String, MarketSignalEvent>,
    series: HashMap<String, SignalSeriesState>,
}

impl MarketSignalEngine {
    pub fn update(&mut self, row: &BarRow) -> Vec<MarketSignalEvent> {
        if !row.is_closed || row.close <= 0.0 {
            return Vec::new();
        }

        let series_key = format!(
            "{}:{}",
            row.sym.to_ascii_uppercase(),
            row.timeframe.to_ascii_lowercase()
        );
        let prior = self
            .series
            .get(&series_key)
            .filter(|state| state.session_date == row.session_date);
        let prior_session_high = prior.map_or(0.0, |state| state.session_high);
        let candidates = evaluate_bar(row, prior.map(|state| &state.previous), prior_session_high);
        let candidate_by_key = candidates
            .iter()
            .map(|candidate| (candidate.key, candidate))
            .collect::<HashMap<_, _>>();
        let relevant_keys = signal_keys_for_timeframe(&row.timeframe);
        let mut events = Vec::new();

        for key in relevant_keys {
            let identity = signal_identity(&row.sym, &row.timeframe, key);
            match (
                self.active.get(&identity).cloned(),
                candidate_by_key.get(key),
            ) {
                (None, Some(candidate)) => {
                    let event = build_event(row, candidate, "triggered", "", None);
                    self.active.insert(identity, event.clone());
                    events.push(event);
                }
                (Some(previous), Some(candidate)) if previous.direction != candidate.direction => {
                    let resolved = resolve_event(
                        row,
                        &previous,
                        "direction reversed before the prior setup reconfirmed",
                    );
                    events.push(resolved);
                    let triggered = build_event(row, candidate, "triggered", "", None);
                    self.active.insert(identity, triggered.clone());
                    events.push(triggered);
                }
                (Some(previous), Some(candidate))
                    if (previous.score.abs() - candidate.score).abs() >= 0.05
                        || (previous.confidence - candidate.confidence).abs() >= 0.05 =>
                {
                    let updated =
                        build_event(row, candidate, "updated", "", Some(&previous.signal_id));
                    self.active.insert(identity, updated.clone());
                    events.push(updated);
                }
                (Some(_), Some(_)) => {}
                (Some(previous), None) => {
                    self.active.remove(&identity);
                    events.push(resolve_event(
                        row,
                        &previous,
                        "trigger conditions no longer hold",
                    ));
                }
                (None, None) => {}
            }
        }
        self.series.insert(
            series_key,
            SignalSeriesState {
                session_date: row.session_date.clone(),
                session_high: prior_session_high.max(row.high),
                previous: row.clone(),
            },
        );
        events
    }
}

fn evaluate_bar(
    row: &BarRow,
    previous: Option<&BarRow>,
    prior_session_high: f64,
) -> Vec<Candidate> {
    let mut candidates = Vec::new();
    let tape_direction = if row.tape_imbalance > 0.15 {
        Some("bullish")
    } else if row.tape_imbalance < -0.15 {
        Some("bearish")
    } else {
        None
    };
    if matches_timeframe(&row.timeframe, &["1s", "10s", "30s"])
        && row.trade_count_accel.abs() > 10.0
        && row.spread_bps_close < 80.0
    {
        if let Some(direction) = tape_direction {
            candidates.push(Candidate {
                key: "tape_acceleration_breakout",
                confirmation_timeframe: None,
                direction,
                score: weighted_score(&[
                    row.trade_count_accel.abs() / 50.0,
                    row.tape_imbalance.abs(),
                    row.price_change_pct.abs() / 5.0,
                ]),
                confidence: evidence_confidence(row),
                reason: "trade acceleration and directional tape pressure remain routeable",
                invalidation_price: Some(if direction == "bullish" {
                    row.low
                } else {
                    row.high
                }),
            });
        }
    }

    if matches_timeframe(&row.timeframe, &["10s", "30s", "1m"])
        && row.dollar_volume_accel.abs() > 250_000.0
        && row.price_change_pct.abs() > 0.25
    {
        let direction = if row.price_change_pct > 0.0 {
            "bullish"
        } else {
            "bearish"
        };
        candidates.push(Candidate {
            key: "volume_shock_momentum",
            confirmation_timeframe: None,
            direction,
            score: weighted_score(&[
                row.dollar_volume_accel.abs() / 2_000_000.0,
                row.price_change_pct.abs() / 5.0,
                row.trade_rate / 50.0,
            ]),
            confidence: evidence_confidence(row),
            reason: "dollar-volume expansion confirms directional price momentum",
            invalidation_price: Some(if direction == "bullish" {
                row.low
            } else {
                row.high
            }),
        });
    }

    if let Some(previous) = previous {
        if matches_timeframe(&row.timeframe, &["1s", "10s", "30s"])
            && row.spread_bps_close > 0.0
            && previous.spread_bps_close >= row.spread_bps_close * 1.5
            && row.quote_rate_accel > 0.0
            && row.liquidity_score > previous.liquidity_score
        {
            if let Some(direction) = tape_direction {
                candidates.push(Candidate {
                    key: "liquidity_recovery_after_spread_shock",
                    confirmation_timeframe: None,
                    direction,
                    score: weighted_score(&[
                        (previous.spread_bps_close - row.spread_bps_close) / 100.0,
                        row.quote_rate_accel / 50.0,
                        row.tape_imbalance.abs(),
                    ]),
                    confidence: evidence_confidence(row),
                    reason:
                        "spread recovered from the prior-bar shock while liquidity and tape agreed",
                    invalidation_price: Some(if direction == "bullish" {
                        row.low
                    } else {
                        row.high
                    }),
                });
            }
        }

        if matches_timeframe(&row.timeframe, &["10s", "30s", "1m"]) {
            let bullish = previous.close <= previous.vwap
                && row.close > row.vwap
                && row.vwap_distance_pct > 0.0
                && row.mid_vwap_distance_pct > 0.0
                && row.tape_imbalance > 0.0;
            let bearish = previous.close >= previous.vwap
                && row.close < row.vwap
                && row.vwap_distance_pct < 0.0
                && row.mid_vwap_distance_pct < 0.0
                && row.tape_imbalance < 0.0;
            if bullish || bearish {
                let direction = if bullish { "bullish" } else { "bearish" };
                candidates.push(Candidate {
                    key: "vwap_reclaim_momentum",
                    confirmation_timeframe: None,
                    direction,
                    score: weighted_score(&[
                        row.vwap_distance_pct.abs() / 2.0,
                        row.mid_vwap_distance_pct.abs() / 2.0,
                        row.tape_imbalance.abs(),
                    ]),
                    confidence: evidence_confidence(row),
                    reason:
                        "price crossed VWAP and the current midpoint and tape confirmed the reclaim",
                    invalidation_price: (row.vwap > 0.0).then_some(row.vwap),
                });
            }
        }
    }

    if matches_timeframe(&row.timeframe, &["10s", "30s", "1m"])
        && prior_session_high > 0.0
        && row.high > prior_session_high
        && row.close > prior_session_high
        && row.close > row.vwap
        && row.tape_imbalance > 0.0
        && row.close >= row.high * 0.995
        && row.trade_rate > 0.5
    {
        candidates.push(Candidate {
            key: "high_of_day_break",
            confirmation_timeframe: None,
            direction: "bullish",
            score: weighted_score(&[
                (row.close - prior_session_high) / prior_session_high * 100.0,
                row.trade_rate / 20.0,
                row.tape_imbalance.max(0.0),
            ]),
            confidence: evidence_confidence(row),
            reason: "price closed above the prior session high with confirming tape activity",
            invalidation_price: Some(prior_session_high),
        });
    }
    candidates
}

fn build_event(
    row: &BarRow,
    candidate: &Candidate,
    state: &str,
    resolution_reason: &str,
    signal_id: Option<&str>,
) -> MarketSignalEvent {
    let stable_signal_id = signal_id.map(str::to_string).unwrap_or_else(|| {
        format!(
            "{}:{}:{}:{}",
            row.sym,
            row.timeframe,
            candidate.key,
            row.bar_end.timestamp_millis()
        )
    });
    MarketSignalEvent {
        schema_version: MARKET_SIGNAL_SCHEMA_VERSION,
        engine_version: MARKET_SIGNAL_ENGINE_VERSION.to_string(),
        event_id: format!(
            "{}:{}:{}:{}:{}",
            row.sym,
            row.timeframe,
            candidate.key,
            row.bar_end.timestamp_millis(),
            state
        ),
        signal_id: stable_signal_id,
        signal_key: candidate.key.to_string(),
        producer: "qmd".to_string(),
        domain: market_domain(),
        ticker: row.sym.clone(),
        working_timeframe: row.timeframe.clone(),
        clock: SignalClock {
            input_basis: "bar_derived".to_string(),
            calculation_window: row.timeframe.clone(),
            evaluation_mode: "closed_only".to_string(),
            update_trigger: "bar_close".to_string(),
            publication_cadence: "bar_close".to_string(),
            publication_interval_ms: None,
        },
        confirmation_timeframe: candidate.confirmation_timeframe.map(str::to_string),
        observed_at: row.bar_end,
        effective_at: row.bar_end,
        state: state.to_string(),
        direction: candidate.direction.to_string(),
        score: if candidate.direction == "bearish" {
            -candidate.score.clamp(0.0, 1.0)
        } else {
            candidate.score.clamp(0.0, 1.0)
        },
        confidence: candidate.confidence.clamp(0.0, 1.0),
        trigger_reason: candidate.reason.to_string(),
        resolution_reason: resolution_reason.to_string(),
        reference_price: row.close,
        invalidation_price: candidate.invalidation_price,
        expires_at: None,
        evidence: evidence(row),
    }
}

fn market_domain() -> String {
    "market".to_string()
}

fn resolve_event(row: &BarRow, previous: &MarketSignalEvent, reason: &str) -> MarketSignalEvent {
    let mut event = previous.clone();
    event.event_id = format!(
        "{}:{}:{}:{}:resolved",
        row.sym,
        row.timeframe,
        previous.signal_key,
        row.bar_end.timestamp_millis()
    );
    event.observed_at = row.bar_end;
    event.effective_at = row.bar_end;
    event.state = "resolved".to_string();
    event.resolution_reason = reason.to_string();
    event.reference_price = row.close;
    event.evidence = evidence(row);
    event
}

fn evidence(row: &BarRow) -> MarketSignalEvidence {
    MarketSignalEvidence {
        close: row.close,
        high: row.high,
        low: row.low,
        vwap: row.vwap,
        price_change_pct: row.price_change_pct,
        volume: row.volume,
        dollar_volume: row.dollar_volume,
        trade_rate: row.trade_rate,
        quote_rate: row.quote_rate,
        tape_imbalance: row.tape_imbalance,
        spread_bps: row.spread_bps_close,
        liquidity_score: row.liquidity_score,
        estimated_luld_active: row.estimated_luld_active,
        estimated_luld_state: row.estimated_luld_state.clone(),
    }
}

fn evidence_confidence(row: &BarRow) -> f64 {
    weighted_score(&[
        row.trade_rate / 20.0,
        row.quote_rate / 50.0,
        row.tape_imbalance.abs(),
        row.liquidity_score.max(0.0).log10().max(0.0) / 8.0,
        if row.spread_bps_close > 0.0 {
            1.0 - (row.spread_bps_close / 100.0).clamp(0.0, 1.0)
        } else {
            0.0
        },
    ])
}

fn signal_keys_for_timeframe(timeframe: &str) -> &'static [&'static str] {
    match timeframe.to_ascii_lowercase().as_str() {
        "1s" => &[
            "tape_acceleration_breakout",
            "liquidity_recovery_after_spread_shock",
        ],
        "10s" | "30s" => &[
            "tape_acceleration_breakout",
            "volume_shock_momentum",
            "liquidity_recovery_after_spread_shock",
            "vwap_reclaim_momentum",
            "high_of_day_break",
        ],
        "1m" => &[
            "volume_shock_momentum",
            "vwap_reclaim_momentum",
            "high_of_day_break",
        ],
        _ => &[],
    }
}

fn signal_identity(ticker: &str, timeframe: &str, key: &str) -> String {
    format!(
        "{}:{}:{}",
        ticker.to_ascii_uppercase(),
        timeframe.to_ascii_lowercase(),
        key
    )
}

fn matches_timeframe(value: &str, allowed: &[&str]) -> bool {
    allowed
        .iter()
        .any(|candidate| candidate.eq_ignore_ascii_case(value))
}

fn weighted_score(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values
        .iter()
        .copied()
        .map(|value| value.clamp(0.0, 1.0))
        .sum::<f64>()
        / values.len() as f64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_time_is_bar_time_and_lifecycle_is_causal() {
        let mut engine = MarketSignalEngine::default();
        let mut bar = crate::scanner::tests::base_bar();
        bar.timeframe = "10s".to_string();
        let triggered = engine.update(&bar);
        assert!(triggered.iter().any(|event| {
            event.signal_key == "tape_acceleration_breakout"
                && event.state == "triggered"
                && event.effective_at == bar.bar_end
                && event.domain == "market"
                && event.clock.input_basis == "bar_derived"
                && event.clock.calculation_window == "10s"
                && event.clock.publication_cadence == "bar_close"
                && event.schema_version == MARKET_SIGNAL_SCHEMA_VERSION
        }));

        bar.bar_start = bar.bar_end;
        bar.bar_end += chrono::Duration::seconds(10);
        bar.trade_count_accel = 0.0;
        bar.dollar_volume_accel = 0.0;
        bar.vwap_distance_pct = 0.0;
        bar.mid_vwap_distance_pct = 0.0;
        bar.price_change_pct = 0.0;
        let resolved = engine.update(&bar);
        let triggered_signal = triggered
            .iter()
            .find(|event| event.signal_key == "tape_acceleration_breakout")
            .unwrap();
        let resolved_signal = resolved
            .iter()
            .find(|event| {
                event.signal_key == "tape_acceleration_breakout" && event.state == "resolved"
            })
            .unwrap();
        assert_eq!(resolved_signal.signal_id, triggered_signal.signal_id);
        assert_ne!(resolved_signal.event_id, triggered_signal.event_id);
    }

    #[test]
    fn tape_signal_is_symmetric() {
        let mut engine = MarketSignalEngine::default();
        let mut bar = crate::scanner::tests::base_bar();
        bar.timeframe = "10s".to_string();
        bar.tape_imbalance = -0.4;
        bar.price_change_pct = -2.0;
        let events = engine.update(&bar);
        assert!(events.iter().any(|event| {
            event.signal_key == "tape_acceleration_breakout"
                && event.direction == "bearish"
                && event.score < 0.0
        }));
    }

    #[test]
    fn vwap_reclaim_requires_a_causal_cross() {
        let mut engine = MarketSignalEngine::default();
        let mut prior = crate::scanner::tests::base_bar();
        prior.timeframe = "10s".to_string();
        prior.close = prior.vwap - 0.05;
        prior.vwap_distance_pct = -0.02;
        prior.mid_vwap_distance_pct = -0.02;
        assert!(engine
            .update(&prior)
            .iter()
            .all(|event| event.signal_key != "vwap_reclaim_momentum"));

        let mut crossed = prior.clone();
        crossed.bar_start = prior.bar_end;
        crossed.bar_end += chrono::Duration::seconds(10);
        crossed.close = crossed.vwap + 0.05;
        crossed.vwap_distance_pct = 0.02;
        crossed.mid_vwap_distance_pct = 0.02;
        crossed.tape_imbalance = 0.4;
        assert!(engine.update(&crossed).iter().any(|event| {
            event.signal_key == "vwap_reclaim_momentum"
                && event.direction == "bullish"
                && event.state == "triggered"
        }));
    }

    #[test]
    fn high_of_day_break_uses_the_prior_session_high() {
        let mut engine = MarketSignalEngine::default();
        let mut prior = crate::scanner::tests::base_bar();
        prior.timeframe = "10s".to_string();
        prior.high = 101.0;
        prior.close = 100.5;
        assert!(engine
            .update(&prior)
            .iter()
            .all(|event| event.signal_key != "high_of_day_break"));

        let mut breakout = prior.clone();
        breakout.bar_start = prior.bar_end;
        breakout.bar_end += chrono::Duration::seconds(10);
        breakout.high = 102.0;
        breakout.close = 102.0;
        breakout.vwap = 100.0;
        breakout.tape_imbalance = 0.4;
        breakout.trade_rate = 10.0;
        let event = engine
            .update(&breakout)
            .into_iter()
            .find(|event| event.signal_key == "high_of_day_break")
            .expect("session-high break should emit");
        assert_eq!(event.invalidation_price, Some(101.0));
        assert_eq!(event.effective_at, breakout.bar_end);
    }
}
