//! Mergeable, timeframe-native microstructure sufficient statistics.

use crate::bars::TradeAggregationRules;
use crate::event::{MarketEvent, QuoteEvent};
use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::VecDeque;

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct MicrostructureSignalArchitecture {
    pub action: &'static str,
    pub aggressive_flow: f64,
    pub confidence: f64,
    pub displayed_liquidity: f64,
    pub regime_reliability: f64,
    pub resiliency_response: f64,
    pub score: f64,
}

/// Additive and mergeable evidence for one causal interval. Public fields are
/// the strategy/UI contract; skipped fields are sufficient statistics used to
/// rebuild the same indicators at larger timeframes without averaging ratios.
#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct MicrostructureIntervalFeatures {
    pub aggressive_flow_score: f64,
    pub aggressor_persistence: f64,
    pub arrival_intensity_imbalance: f64,
    pub arrival_rate_per_second: f64,
    pub buy_trade_count: u64,
    pub buy_volume: f64,
    pub classified_trade_count: u64,
    pub displayed_liquidity_score: f64,
    pub eligible_trade_count: u64,
    pub level1_ofi_delta: f64,
    pub level1_ofi: f64,
    pub microprice_lean: f64,
    pub midpoint_return_bps: f64,
    pub queue_imbalance: f64,
    pub quote_count: u64,
    pub regime_reliability: f64,
    pub resiliency: f64,
    pub response_resiliency_score: f64,
    pub sell_trade_count: u64,
    pub sell_volume: f64,
    pub signed_volume_delta: f64,
    pub signed_volume_imbalance: f64,
    pub spread_bps: f64,
    pub trade_return_bps: f64,
    pub transaction_imbalance: f64,
    pub unified_action: &'static str,
    pub unified_confidence: f64,
    pub unified_signal: f64,
    #[serde(skip_serializing)]
    pub ofi_numerator: f64,
    #[serde(skip_serializing)]
    pub ofi_depth_exposure: f64,
    #[serde(skip_serializing)]
    pub queue_imbalance_sum: f64,
    #[serde(skip_serializing)]
    pub queue_sample_count: u64,
    #[serde(skip_serializing)]
    pub microprice_lean_sum: f64,
    #[serde(skip_serializing)]
    pub microprice_sample_count: u64,
    #[serde(skip_serializing)]
    pub aggressor_sign_sum: f64,
    #[serde(skip_serializing)]
    pub aggressor_sign_count: u64,
    #[serde(skip_serializing)]
    pub arrival_sign_sum: f64,
    #[serde(skip_serializing)]
    pub arrival_count: u64,
    #[serde(skip_serializing)]
    pub bid_depletion: f64,
    #[serde(skip_serializing)]
    pub bid_replenishment: f64,
    #[serde(skip_serializing)]
    pub ask_depletion: f64,
    #[serde(skip_serializing)]
    pub ask_replenishment: f64,
    #[serde(skip_serializing)]
    pub locked_crossed_quote_count: u64,
    #[serde(skip_serializing)]
    pub spread_bps_sum: f64,
    #[serde(skip_serializing)]
    pub spread_sample_count: u64,
    #[serde(skip_serializing)]
    pub duration_us: u64,
    #[serde(skip_serializing)]
    pub midpoint_log_return: f64,
    #[serde(skip_serializing)]
    pub trade_log_return: f64,
}

impl MicrostructureIntervalFeatures {
    pub fn merge(&mut self, other: &Self) {
        self.buy_trade_count += other.buy_trade_count;
        self.sell_trade_count += other.sell_trade_count;
        self.classified_trade_count += other.classified_trade_count;
        self.eligible_trade_count += other.eligible_trade_count;
        self.buy_volume += other.buy_volume;
        self.sell_volume += other.sell_volume;
        self.quote_count += other.quote_count;
        self.ofi_numerator += other.ofi_numerator;
        self.ofi_depth_exposure += other.ofi_depth_exposure;
        self.queue_imbalance_sum += other.queue_imbalance_sum;
        self.queue_sample_count += other.queue_sample_count;
        self.microprice_lean_sum += other.microprice_lean_sum;
        self.microprice_sample_count += other.microprice_sample_count;
        self.aggressor_sign_sum += other.aggressor_sign_sum;
        self.aggressor_sign_count += other.aggressor_sign_count;
        self.arrival_sign_sum += other.arrival_sign_sum;
        self.arrival_count += other.arrival_count;
        self.bid_depletion += other.bid_depletion;
        self.bid_replenishment += other.bid_replenishment;
        self.ask_depletion += other.ask_depletion;
        self.ask_replenishment += other.ask_replenishment;
        self.locked_crossed_quote_count += other.locked_crossed_quote_count;
        self.spread_bps_sum += other.spread_bps_sum;
        self.spread_sample_count += other.spread_sample_count;
        self.midpoint_log_return += other.midpoint_log_return;
        self.trade_log_return += other.trade_log_return;
        self.duration_us += other.duration_us;
    }

    pub fn refresh(&mut self, coverage: f64) {
        self.signed_volume_delta = self.buy_volume - self.sell_volume;
        self.level1_ofi_delta = self.ofi_numerator;
        self.transaction_imbalance = safe_ratio(
            self.buy_trade_count as f64 - self.sell_trade_count as f64,
            self.classified_trade_count as f64,
        );
        self.signed_volume_imbalance = safe_ratio(
            self.buy_volume - self.sell_volume,
            self.buy_volume + self.sell_volume,
        );
        self.level1_ofi = safe_ratio(self.ofi_numerator, self.ofi_depth_exposure);
        self.queue_imbalance = safe_ratio(self.queue_imbalance_sum, self.queue_sample_count as f64);
        self.microprice_lean = safe_ratio(
            self.microprice_lean_sum,
            self.microprice_sample_count as f64,
        );
        self.aggressor_persistence =
            safe_ratio(self.aggressor_sign_sum, self.aggressor_sign_count as f64);
        self.arrival_intensity_imbalance =
            safe_ratio(self.arrival_sign_sum, self.arrival_count as f64);
        self.arrival_rate_per_second = if self.duration_us > 0 {
            self.arrival_count as f64 / (self.duration_us as f64 / 1_000_000.0)
        } else {
            0.0
        };
        let bid_recovery = recovery_ratio(self.bid_replenishment, self.bid_depletion);
        let ask_recovery = recovery_ratio(self.ask_replenishment, self.ask_depletion);
        self.resiliency = clamp(bid_recovery - ask_recovery, -1.0, 1.0);
        self.spread_bps = safe_ratio(self.spread_bps_sum, self.spread_sample_count as f64);

        self.midpoint_return_bps = self.midpoint_log_return.exp_m1() * 10_000.0;
        self.trade_return_bps = self.trade_log_return.exp_m1() * 10_000.0;
        let spread_scale = self.spread_bps.max(0.01);
        let midpoint_return_signal = clamp(self.midpoint_return_bps / spread_scale, -1.0, 1.0);
        let trade_return_signal = clamp(self.trade_return_bps / spread_scale, -1.0, 1.0);
        let absorption_response = if self.signed_volume_imbalance.abs() >= 0.35 {
            -self.signed_volume_imbalance * (1.0 - midpoint_return_signal.abs())
        } else {
            0.0
        };
        let aggressive_flow = clamp(
            0.30 * self.transaction_imbalance
                + 0.25 * self.signed_volume_imbalance
                + 0.20 * self.aggressor_persistence
                + 0.15 * trade_return_signal
                + 0.10 * self.arrival_intensity_imbalance,
            -1.0,
            1.0,
        );
        let displayed_liquidity = clamp(
            0.35 * self.level1_ofi
                + 0.25 * self.queue_imbalance
                + 0.20 * self.microprice_lean
                + 0.20 * self.arrival_intensity_imbalance,
            -1.0,
            1.0,
        );
        let response_resiliency = clamp(
            0.45 * midpoint_return_signal + 0.30 * self.resiliency + 0.25 * absorption_response,
            -1.0,
            1.0,
        );
        let score = clamp(
            0.45 * aggressive_flow + 0.35 * displayed_liquidity + 0.20 * response_resiliency,
            -1.0,
            1.0,
        );
        let blocks = [aggressive_flow, displayed_liquidity, response_resiliency];
        let directional_blocks = blocks
            .into_iter()
            .filter(|value| value.abs() >= 0.05)
            .collect::<Vec<_>>();
        let agreement = if directional_blocks.is_empty() || score.abs() < 1e-9 {
            0.5
        } else {
            directional_blocks
                .iter()
                .filter(|value| value.signum() == score.signum())
                .count() as f64
                / directional_blocks.len() as f64
        };
        let quote_quality = 1.0
            - safe_ratio(
                self.locked_crossed_quote_count as f64,
                self.quote_count.max(1) as f64,
            )
            .clamp(0.0, 0.75);
        let classification_quality = if self.eligible_trade_count == 0 {
            0.75
        } else {
            0.5 + 0.5
                * safe_ratio(
                    self.classified_trade_count as f64,
                    self.eligible_trade_count as f64,
                )
                .clamp(0.0, 1.0)
        };
        let evidence_quality = ((self.quote_count + self.classified_trade_count) as f64 / 4.0)
            .sqrt()
            .clamp(0.25, 1.0);
        let reliability = coverage.clamp(0.0, 1.0)
            * quote_quality
            * classification_quality
            * evidence_quality
            * (0.5 + 0.5 * agreement);
        let confidence = clamp(
            100.0 * reliability * (0.55 + 0.45 * score.abs()),
            0.0,
            100.0,
        );
        let action = if confidence < 35.0 || score.abs() < 0.15 {
            "wait"
        } else if score > 0.0 {
            "buy"
        } else {
            "sell"
        };
        self.aggressive_flow_score = round4(aggressive_flow);
        self.displayed_liquidity_score = round4(displayed_liquidity);
        self.response_resiliency_score = round4(response_resiliency);
        self.regime_reliability = round4(reliability);
        self.unified_signal = round4(score);
        self.unified_confidence = round2(confidence);
        self.unified_action = action;
        self.transaction_imbalance = round4(self.transaction_imbalance);
        self.signed_volume_delta = round2(self.signed_volume_delta);
        self.signed_volume_imbalance = round4(self.signed_volume_imbalance);
        self.level1_ofi_delta = round2(self.level1_ofi_delta);
        self.level1_ofi = round4(self.level1_ofi);
        self.queue_imbalance = round4(self.queue_imbalance);
        self.microprice_lean = round4(self.microprice_lean);
        self.aggressor_persistence = round4(self.aggressor_persistence);
        self.arrival_intensity_imbalance = round4(self.arrival_intensity_imbalance);
        self.resiliency = round4(self.resiliency);
        self.midpoint_return_bps = round4(self.midpoint_return_bps);
        self.trade_return_bps = round4(self.trade_return_bps);
        self.spread_bps = round4(self.spread_bps);
        self.arrival_rate_per_second = round2(self.arrival_rate_per_second);
    }

    pub fn architecture(&self) -> MicrostructureSignalArchitecture {
        MicrostructureSignalArchitecture {
            action: self.unified_action,
            aggressive_flow: self.aggressive_flow_score,
            confidence: self.unified_confidence,
            displayed_liquidity: self.displayed_liquidity_score,
            regime_reliability: self.regime_reliability,
            resiliency_response: self.response_resiliency_score,
            score: self.unified_signal,
        }
    }
}

fn safe_ratio(numerator: f64, denominator: f64) -> f64 {
    if !numerator.is_finite() || !denominator.is_finite() || denominator.abs() <= f64::EPSILON {
        0.0
    } else {
        numerator / denominator
    }
}

fn recovery_ratio(replenishment: f64, depletion: f64) -> f64 {
    if depletion <= f64::EPSILON {
        0.0
    } else {
        (replenishment / depletion).clamp(0.0, 1.0)
    }
}

#[derive(Clone, Debug, Default)]
pub struct MicrostructureIntervalWindow {
    events: VecDeque<MarketEvent>,
}

impl MicrostructureIntervalWindow {
    pub fn apply_event(&mut self, event: &MarketEvent) {
        self.events.push_back(event.clone());
        while self.events.len() > 8_192 {
            self.events.pop_front();
        }
    }

    /// Calculate the closed 100 ms interval contract used by chart bars.
    ///
    /// This walks each event in the requested bucket once and looks
    /// backward only far enough to recover the preceding quote and eligible
    /// trade needed for causal classification and chained returns.
    pub fn interval_at(
        &self,
        as_of: DateTime<Utc>,
        trade_rules: &TradeAggregationRules,
    ) -> MicrostructureIntervalFeatures {
        let (interval_start_us, interval_end_us) = closed_100ms_bounds(as_of);
        let mut window = Vec::new();
        let mut seed_quote = None;
        let mut seed_trade_price = None;
        for event in self.events.iter().rev() {
            let timestamp_us = event.ts().timestamp_micros();
            if timestamp_us >= interval_end_us {
                continue;
            }
            if timestamp_us >= interval_start_us {
                window.push(event.clone());
                continue;
            }
            match event {
                MarketEvent::Quote(quote) if seed_quote.is_none() && valid_quote(quote) => {
                    seed_quote = Some(quote.clone());
                }
                MarketEvent::Trade(trade) if seed_trade_price.is_none() => {
                    let rule = trade_rules.resolve(&trade.conditions, trade.ts);
                    if rule.update_last
                        && rule.update_volume
                        && trade.price > 0.0
                        && trade.size > 0.0
                    {
                        seed_trade_price = Some(trade.price);
                    }
                }
                _ => {}
            }
            if seed_quote.is_some() && seed_trade_price.is_some() {
                break;
            }
        }
        window.reverse();
        calculate_interval_features(
            &window,
            seed_quote,
            seed_trade_price,
            trade_rules,
            100_000,
            1.0,
        )
    }
}

fn closed_100ms_bounds(as_of: DateTime<Utc>) -> (i64, i64) {
    let as_of_us = as_of.timestamp_micros();
    let interval_end_us = if as_of_us.rem_euclid(100_000) == 0 {
        as_of_us
    } else {
        as_of_us.div_euclid(100_000).saturating_add(1) * 100_000
    };
    (interval_end_us.saturating_sub(100_000), interval_end_us)
}

fn calculate_interval_features(
    window: &[MarketEvent],
    seed_quote: Option<QuoteEvent>,
    seed_trade_price: Option<f64>,
    trade_rules: &TradeAggregationRules,
    duration_us: u64,
    coverage: f64,
) -> MicrostructureIntervalFeatures {
    let mut features = MicrostructureIntervalFeatures {
        duration_us,
        unified_action: "wait",
        ..Default::default()
    };
    let mut current_quote = seed_quote;
    let mut first_midpoint = current_quote
        .as_ref()
        .map(|quote| (quote.bid_price + quote.ask_price) / 2.0);
    let mut last_midpoint = None;
    let mut first_trade_price = seed_trade_price;
    let mut last_trade_price = None;

    for event in window {
        match event {
            MarketEvent::Quote(quote) if valid_quote(quote) => {
                features.quote_count += 1;
                if quote.bid_price >= quote.ask_price {
                    features.locked_crossed_quote_count += 1;
                }
                let midpoint = (quote.bid_price + quote.ask_price) / 2.0;
                first_midpoint.get_or_insert(midpoint);
                last_midpoint = Some(midpoint);
                let spread = quote.ask_price - quote.bid_price;
                if midpoint > 0.0 && spread >= 0.0 {
                    features.spread_bps_sum += spread / midpoint * 10_000.0;
                    features.spread_sample_count += 1;
                }
                let total_size = quote.bid_size as f64 + quote.ask_size as f64;
                if total_size > 0.0 {
                    features.queue_imbalance_sum +=
                        (quote.bid_size as f64 - quote.ask_size as f64) / total_size;
                    features.queue_sample_count += 1;
                    features.microprice_lean_sum += normalized_microprice_lean(quote);
                    features.microprice_sample_count += 1;
                }
                if let Some(previous) = current_quote.as_ref() {
                    let (ofi, depth) = raw_level1_ofi(previous, quote);
                    features.ofi_numerator += ofi;
                    features.ofi_depth_exposure += depth;
                    let (bid_depletion, bid_replenishment, ask_depletion, ask_replenishment) =
                        quote_liquidity_changes(previous, quote);
                    features.bid_depletion += bid_depletion;
                    features.bid_replenishment += bid_replenishment;
                    features.ask_depletion += ask_depletion;
                    features.ask_replenishment += ask_replenishment;
                    let bullish = bid_replenishment + ask_depletion;
                    let bearish = bid_depletion + ask_replenishment;
                    if bullish > bearish + f64::EPSILON {
                        features.arrival_sign_sum += 1.0;
                        features.arrival_count += 1;
                    } else if bearish > bullish + f64::EPSILON {
                        features.arrival_sign_sum -= 1.0;
                        features.arrival_count += 1;
                    }
                }
                current_quote = Some(quote.clone());
            }
            MarketEvent::Trade(trade) => {
                let rule = trade_rules.resolve(&trade.conditions, trade.ts);
                if !rule.update_last
                    || !rule.update_volume
                    || trade.price <= 0.0
                    || trade.size <= 0.0
                {
                    continue;
                }
                features.eligible_trade_count += 1;
                first_trade_price.get_or_insert(trade.price);
                last_trade_price = Some(trade.price);
                if let Some(quote) = current_quote.as_ref() {
                    let epsilon = quote.ask_price.abs().max(quote.bid_price.abs()).max(1.0) * 1e-9;
                    if trade.price >= quote.ask_price - epsilon {
                        features.buy_trade_count += 1;
                        features.buy_volume += trade.size;
                        features.classified_trade_count += 1;
                        features.aggressor_sign_sum += 1.0;
                        features.aggressor_sign_count += 1;
                        features.arrival_sign_sum += 1.0;
                        features.arrival_count += 1;
                    } else if trade.price <= quote.bid_price + epsilon {
                        features.sell_trade_count += 1;
                        features.sell_volume += trade.size;
                        features.classified_trade_count += 1;
                        features.aggressor_sign_sum -= 1.0;
                        features.aggressor_sign_count += 1;
                        features.arrival_sign_sum -= 1.0;
                        features.arrival_count += 1;
                    }
                }
            }
            _ => {}
        }
    }
    features.midpoint_log_return = log_return(first_midpoint, last_midpoint);
    features.trade_log_return = log_return(first_trade_price, last_trade_price);
    features.refresh(coverage);
    features
}

fn raw_level1_ofi(previous: &QuoteEvent, current: &QuoteEvent) -> (f64, f64) {
    let mut flow = 0.0;
    if current.bid_price >= previous.bid_price {
        flow += current.bid_size as f64;
    }
    if current.bid_price <= previous.bid_price {
        flow -= previous.bid_size as f64;
    }
    if current.ask_price <= previous.ask_price {
        flow -= current.ask_size as f64;
    }
    if current.ask_price >= previous.ask_price {
        flow += previous.ask_size as f64;
    }
    let depth = 0.5
        * (previous.bid_size as f64
            + previous.ask_size as f64
            + current.bid_size as f64
            + current.ask_size as f64);
    (flow, depth.max(1.0))
}

fn quote_liquidity_changes(previous: &QuoteEvent, current: &QuoteEvent) -> (f64, f64, f64, f64) {
    let (bid_depletion, bid_replenishment) = if current.bid_price < previous.bid_price {
        (previous.bid_size as f64, 0.0)
    } else if current.bid_price > previous.bid_price {
        (0.0, current.bid_size as f64)
    } else {
        (
            previous.bid_size.saturating_sub(current.bid_size) as f64,
            current.bid_size.saturating_sub(previous.bid_size) as f64,
        )
    };
    let (ask_depletion, ask_replenishment) = if current.ask_price > previous.ask_price {
        (previous.ask_size as f64, 0.0)
    } else if current.ask_price < previous.ask_price {
        (0.0, current.ask_size as f64)
    } else {
        (
            previous.ask_size.saturating_sub(current.ask_size) as f64,
            current.ask_size.saturating_sub(previous.ask_size) as f64,
        )
    };
    (
        bid_depletion,
        bid_replenishment,
        ask_depletion,
        ask_replenishment,
    )
}

fn log_return(first: Option<f64>, last: Option<f64>) -> f64 {
    match (first, last) {
        (Some(first), Some(last)) if first > 0.0 && last > 0.0 => (last / first).ln(),
        _ => 0.0,
    }
}

fn valid_quote(quote: &QuoteEvent) -> bool {
    quote.bid_price.is_finite()
        && quote.ask_price.is_finite()
        && quote.bid_price > 0.0
        && quote.ask_price > 0.0
}

fn normalized_microprice_lean(quote: &QuoteEvent) -> f64 {
    let total = quote.bid_size as f64 + quote.ask_size as f64;
    let spread = quote.ask_price - quote.bid_price;
    if total <= 0.0 || spread <= 0.0 {
        return 0.0;
    }
    let midpoint = (quote.ask_price + quote.bid_price) / 2.0;
    let microprice =
        (quote.ask_price * quote.bid_size as f64 + quote.bid_price * quote.ask_size as f64) / total;
    clamp((microprice - midpoint) / (spread / 2.0), -1.0, 1.0)
}

fn clamp(value: f64, minimum: f64, maximum: f64) -> f64 {
    value.max(minimum).min(maximum)
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn interval_merge_recomputes_ratios_from_additive_statistics() {
        let mut first = MicrostructureIntervalFeatures {
            buy_trade_count: 3,
            sell_trade_count: 1,
            classified_trade_count: 4,
            eligible_trade_count: 4,
            buy_volume: 300.0,
            sell_volume: 100.0,
            aggressor_sign_sum: 2.0,
            aggressor_sign_count: 4,
            arrival_sign_sum: 3.0,
            arrival_count: 5,
            quote_count: 4,
            queue_imbalance_sum: 2.0,
            queue_sample_count: 4,
            microprice_lean_sum: 1.0,
            microprice_sample_count: 4,
            ofi_numerator: 200.0,
            ofi_depth_exposure: 400.0,
            spread_bps_sum: 4.0,
            spread_sample_count: 4,
            midpoint_log_return: (100.01_f64 / 100.0).ln(),
            trade_log_return: (100.02_f64 / 100.0).ln(),
            duration_us: 100_000,
            unified_action: "wait",
            ..Default::default()
        };
        let second = MicrostructureIntervalFeatures {
            buy_trade_count: 1,
            sell_trade_count: 3,
            classified_trade_count: 4,
            eligible_trade_count: 4,
            buy_volume: 50.0,
            sell_volume: 150.0,
            aggressor_sign_sum: -2.0,
            aggressor_sign_count: 4,
            arrival_sign_sum: -1.0,
            arrival_count: 5,
            quote_count: 4,
            queue_imbalance_sum: -1.0,
            queue_sample_count: 4,
            microprice_lean_sum: -0.5,
            microprice_sample_count: 4,
            ofi_numerator: -100.0,
            ofi_depth_exposure: 400.0,
            spread_bps_sum: 4.0,
            spread_sample_count: 4,
            midpoint_log_return: (100.0_f64 / 100.01).ln(),
            trade_log_return: (100.01_f64 / 100.02).ln(),
            duration_us: 100_000,
            unified_action: "wait",
            ..Default::default()
        };

        first.merge(&second);
        first.refresh(1.0);

        assert_eq!(first.buy_trade_count, 4);
        assert_eq!(first.sell_trade_count, 4);
        assert_eq!(first.classified_trade_count, 8);
        assert!((first.transaction_imbalance - 0.0).abs() < 1e-9);
        assert!((first.signed_volume_delta - 100.0).abs() < 1e-9);
        assert!((first.signed_volume_imbalance - 0.1667).abs() < 1e-4);
        assert!((first.level1_ofi_delta - 100.0).abs() < 1e-9);
        assert!((first.level1_ofi - 0.125).abs() < 1e-9);
        assert!((first.queue_imbalance - 0.125).abs() < 1e-9);
        assert!((first.midpoint_return_bps - 0.0).abs() < 1e-6);
        assert!((first.trade_return_bps - 1.0).abs() < 0.01);
        assert_eq!(first.duration_us, 200_000);
    }
}
