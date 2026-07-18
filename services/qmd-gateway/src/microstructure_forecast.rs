use crate::bars::TradeAggregationRules;
use crate::compact_event::{CompactEventDecoder, LiveCompactEvent};
use crate::event::{MarketEvent, QuoteEvent};
use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::VecDeque;

pub const MICROSTRUCTURE_FORECAST_SCHEMA_VERSION: u16 = 3;
pub const MICROSTRUCTURE_FORECAST_METHOD: &str = "deterministic_microstructure_v2";
pub const MICROSTRUCTURE_FORECAST_HORIZONS: [usize; 3] = [25, 100, 500];
pub const MICROSTRUCTURE_FORECAST_HORIZON_WEIGHTS: [f64; 3] = [0.50, 0.30, 0.20];

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MicrostructureForecastSnapshot {
    pub as_of_timestamp_us: u64,
    pub horizons: Vec<MicrostructureForecastHorizon>,
    pub method: &'static str,
    pub schema_version: u16,
    pub source: String,
    pub target: &'static str,
    pub ticker: String,
    pub interval: MicrostructureIntervalFeatures,
    pub unified: MicrostructureUnifiedForecast,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MicrostructureUnifiedForecast {
    pub action: &'static str,
    pub agreement: f64,
    pub confidence: f64,
    pub direction: &'static str,
    pub score: f64,
    pub status: &'static str,
    pub strength: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MicrostructureForecastHorizon {
    pub absorption: bool,
    pub confidence: f64,
    pub direction: &'static str,
    pub horizon_events: usize,
    pub observed_duration_ms: f64,
    pub observed_events: usize,
    pub quote_count: usize,
    pub regime: &'static str,
    pub score: f64,
    pub status: &'static str,
    pub strength: f64,
    pub trade_count: usize,
    pub components: MicrostructureForecastComponents,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MicrostructureForecastComponents {
    pub aggressive_flow: f64,
    pub arrival_intensity_imbalance: f64,
    pub displayed_liquidity: f64,
    pub level1_ofi: f64,
    pub microprice_lean: f64,
    pub midpoint_return: f64,
    pub persistence: f64,
    pub price_response: f64,
    pub queue_imbalance: f64,
    pub quote_flow_imbalance: f64,
    pub regime_reliability: f64,
    pub resiliency: f64,
    pub response_resiliency: f64,
    pub signed_volume_imbalance: f64,
    pub trade_return: f64,
    pub transaction_imbalance: f64,
    pub trade_flow_imbalance: f64,
}

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
        self.signed_volume_imbalance = round4(self.signed_volume_imbalance);
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
pub struct MicrostructureForecastWindow {
    events: VecDeque<MarketEvent>,
}

impl MicrostructureForecastWindow {
    pub fn apply_event(&mut self, event: &MarketEvent) {
        self.events.push_back(event.clone());
        while self.events.len() > 8_192 {
            self.events.pop_front();
        }
    }

    pub fn snapshot(
        &self,
        trade_rules: &TradeAggregationRules,
        source: impl Into<String>,
    ) -> MicrostructureForecastSnapshot {
        let events = self
            .events
            .iter()
            .rev()
            .take(4_096)
            .cloned()
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>();
        forecast_market_events(&events, trade_rules, source)
    }

    pub fn snapshot_at(
        &self,
        as_of: DateTime<Utc>,
        trade_rules: &TradeAggregationRules,
        source: impl Into<String>,
    ) -> MicrostructureForecastSnapshot {
        let events = self
            .events
            .iter()
            .rev()
            .filter(|event| event.ts() <= as_of)
            .take(4_096)
            .cloned()
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>();
        forecast_market_events_at(&events, as_of, trade_rules, source)
    }
}

pub fn forecast_compact_events(
    compact_events: &[LiveCompactEvent],
    decoder: &CompactEventDecoder,
    trade_rules: &TradeAggregationRules,
    source: impl Into<String>,
) -> MicrostructureForecastSnapshot {
    let events = compact_events
        .iter()
        .map(|event| decoder.decode(event))
        .collect::<Vec<_>>();
    forecast_market_events(&events, trade_rules, source)
}

pub fn forecast_market_events(
    events: &[MarketEvent],
    trade_rules: &TradeAggregationRules,
    source: impl Into<String>,
) -> MicrostructureForecastSnapshot {
    let as_of = events
        .last()
        .map(MarketEvent::ts)
        .unwrap_or(DateTime::<Utc>::UNIX_EPOCH);
    forecast_market_events_at(events, as_of, trade_rules, source)
}

fn forecast_market_events_at(
    events: &[MarketEvent],
    as_of: DateTime<Utc>,
    trade_rules: &TradeAggregationRules,
    source: impl Into<String>,
) -> MicrostructureForecastSnapshot {
    let ticker = events
        .last()
        .map(MarketEvent::ticker)
        .unwrap_or_default()
        .to_ascii_uppercase();
    let as_of_timestamp_us = as_of.timestamp_micros().max(0) as u64;
    let horizons = MICROSTRUCTURE_FORECAST_HORIZONS
        .into_iter()
        .map(|horizon| calculate_horizon(events, horizon, trade_rules))
        .collect::<Vec<_>>();
    let unified = unified_forecast(&horizons);
    let interval = calculate_100ms_interval(events, as_of, trade_rules);
    MicrostructureForecastSnapshot {
        as_of_timestamp_us,
        horizons,
        method: MICROSTRUCTURE_FORECAST_METHOD,
        schema_version: MICROSTRUCTURE_FORECAST_SCHEMA_VERSION,
        source: source.into(),
        target: "next_midpoint_move",
        ticker,
        interval,
        unified,
    }
}

fn calculate_100ms_interval(
    events: &[MarketEvent],
    as_of: DateTime<Utc>,
    trade_rules: &TradeAggregationRules,
) -> MicrostructureIntervalFeatures {
    let as_of_us = as_of.timestamp_micros();
    let interval_end_us = if as_of_us.rem_euclid(100_000) == 0 {
        as_of_us
    } else {
        as_of_us.div_euclid(100_000).saturating_add(1) * 100_000
    };
    let interval_start_us = interval_end_us.saturating_sub(100_000);
    let start = events.partition_point(|event| event.ts().timestamp_micros() < interval_start_us);
    let end = events.partition_point(|event| event.ts().timestamp_micros() < interval_end_us);
    let seed_quote = events[..start].iter().rev().find_map(|event| match event {
        MarketEvent::Quote(quote) if valid_quote(quote) => Some(quote.clone()),
        _ => None,
    });
    let seed_trade_price = preceding_eligible_trade_price(&events[..start], trade_rules);
    calculate_interval_features(
        &events[start..end],
        seed_quote,
        seed_trade_price,
        trade_rules,
        100_000,
        1.0,
    )
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

fn preceding_eligible_trade_price(
    events: &[MarketEvent],
    trade_rules: &TradeAggregationRules,
) -> Option<f64> {
    events.iter().rev().find_map(|event| match event {
        MarketEvent::Trade(trade) => {
            let rule = trade_rules.resolve(&trade.conditions, trade.ts);
            (rule.update_last && rule.update_volume && trade.price > 0.0 && trade.size > 0.0)
                .then_some(trade.price)
        }
        _ => None,
    })
}

fn unified_forecast(horizons: &[MicrostructureForecastHorizon]) -> MicrostructureUnifiedForecast {
    let mut weighted_score = 0.0;
    let mut effective_weight = 0.0;
    let mut base_confidence = 0.0;
    for (index, horizon) in horizons.iter().enumerate() {
        let prior = MICROSTRUCTURE_FORECAST_HORIZON_WEIGHTS
            .get(index)
            .copied()
            .unwrap_or(0.0);
        if horizon.status != "ready" {
            continue;
        }
        let evidence_weight = prior * horizon.confidence / 100.0;
        weighted_score += evidence_weight * horizon.score;
        effective_weight += evidence_weight;
        base_confidence += prior * horizon.confidence;
    }
    if effective_weight <= f64::EPSILON {
        return MicrostructureUnifiedForecast {
            action: "wait",
            agreement: 0.0,
            confidence: 0.0,
            direction: "neutral",
            score: 0.0,
            status: "insufficient_data",
            strength: 0.0,
        };
    }
    let score = clamp(weighted_score / effective_weight, -1.0, 1.0);
    let disagreement = horizons
        .iter()
        .enumerate()
        .filter(|(_, horizon)| horizon.status == "ready")
        .map(|(index, horizon)| {
            let prior = MICROSTRUCTURE_FORECAST_HORIZON_WEIGHTS
                .get(index)
                .copied()
                .unwrap_or(0.0);
            let evidence_weight = prior * horizon.confidence / 100.0;
            evidence_weight * (horizon.score - score).abs() / 2.0
        })
        .sum::<f64>()
        / effective_weight;
    let agreement = clamp(1.0 - disagreement, 0.0, 1.0);
    let confidence = clamp(base_confidence * (0.5 + 0.5 * agreement), 0.0, 100.0);
    let action = if confidence < 35.0 || score.abs() < 0.15 {
        "wait"
    } else if score > 0.0 {
        "buy"
    } else {
        "sell"
    };
    MicrostructureUnifiedForecast {
        action,
        agreement: round2(agreement * 100.0),
        confidence: round2(confidence),
        direction: direction(score),
        score: round4(score),
        status: "ready",
        strength: round2(score.abs() * 100.0),
    }
}

fn calculate_horizon(
    events: &[MarketEvent],
    horizon_events: usize,
    trade_rules: &TradeAggregationRules,
) -> MicrostructureForecastHorizon {
    let start = events.len().saturating_sub(horizon_events);
    let window = &events[start..];
    let seed_quote = events[..start].iter().rev().find_map(|event| match event {
        MarketEvent::Quote(quote) if valid_quote(quote) => Some(quote.clone()),
        _ => None,
    });
    let seed_trade_price = preceding_eligible_trade_price(&events[..start], trade_rules);
    let observed_events = window.len();
    let observed_duration_ms = match (window.first(), window.last()) {
        (Some(first), Some(last)) => {
            (last.ts() - first.ts())
                .num_microseconds()
                .unwrap_or(0)
                .max(0) as f64
                / 1_000.0
        }
        _ => 0.0,
    };
    let coverage = (observed_events as f64 / horizon_events as f64).clamp(0.0, 1.0);
    let features = calculate_interval_features(
        window,
        seed_quote,
        seed_trade_price,
        trade_rules,
        (observed_duration_ms * 1_000.0).round().max(0.0) as u64,
        coverage,
    );
    if features.quote_count < 2 {
        return unavailable_horizon(
            horizon_events,
            observed_events,
            observed_duration_ms,
            features.quote_count as usize,
            features.eligible_trade_count as usize,
        );
    }
    let components = MicrostructureForecastComponents {
        aggressive_flow: features.aggressive_flow_score,
        arrival_intensity_imbalance: features.arrival_intensity_imbalance,
        displayed_liquidity: features.displayed_liquidity_score,
        level1_ofi: features.level1_ofi,
        microprice_lean: features.microprice_lean,
        midpoint_return: normalized_return_signal(
            features.midpoint_return_bps,
            features.spread_bps,
        ),
        persistence: features.aggressor_persistence,
        price_response: normalized_return_signal(features.midpoint_return_bps, features.spread_bps),
        queue_imbalance: features.queue_imbalance,
        quote_flow_imbalance: features.level1_ofi,
        regime_reliability: features.regime_reliability,
        resiliency: features.resiliency,
        response_resiliency: features.response_resiliency_score,
        signed_volume_imbalance: features.signed_volume_imbalance,
        trade_return: normalized_return_signal(features.trade_return_bps, features.spread_bps),
        transaction_imbalance: features.transaction_imbalance,
        trade_flow_imbalance: features.signed_volume_imbalance,
    };
    let score = features.unified_signal;
    let absorption =
        components.trade_flow_imbalance.abs() >= 0.35 && components.price_response.abs() <= 0.15;
    let agreement = directional_agreement(score, &components);
    let confidence = features.unified_confidence;
    let direction = direction(score);
    let regime = if absorption {
        "absorption"
    } else if agreement < 0.55 && score.abs() >= 0.15 {
        "conflicted"
    } else if score.abs() < 0.15 {
        "neutral"
    } else {
        "continuation"
    };
    MicrostructureForecastHorizon {
        absorption,
        confidence: round2(confidence),
        direction,
        horizon_events,
        observed_duration_ms: round2(observed_duration_ms),
        observed_events,
        quote_count: features.quote_count as usize,
        regime,
        score: round4(score),
        status: "ready",
        strength: round2(score.abs() * 100.0),
        trade_count: features.eligible_trade_count as usize,
        components,
    }
}

fn unavailable_horizon(
    horizon_events: usize,
    observed_events: usize,
    observed_duration_ms: f64,
    quote_count: usize,
    trade_count: usize,
) -> MicrostructureForecastHorizon {
    MicrostructureForecastHorizon {
        absorption: false,
        confidence: 0.0,
        direction: "neutral",
        horizon_events,
        observed_duration_ms: round2(observed_duration_ms),
        observed_events,
        quote_count,
        regime: "insufficient_data",
        score: 0.0,
        status: "insufficient_data",
        strength: 0.0,
        trade_count,
        components: MicrostructureForecastComponents {
            aggressive_flow: 0.0,
            arrival_intensity_imbalance: 0.0,
            displayed_liquidity: 0.0,
            level1_ofi: 0.0,
            microprice_lean: 0.0,
            midpoint_return: 0.0,
            persistence: 0.0,
            price_response: 0.0,
            queue_imbalance: 0.0,
            quote_flow_imbalance: 0.0,
            regime_reliability: 0.0,
            resiliency: 0.0,
            response_resiliency: 0.0,
            signed_volume_imbalance: 0.0,
            trade_return: 0.0,
            transaction_imbalance: 0.0,
            trade_flow_imbalance: 0.0,
        },
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

fn normalized_return_signal(return_bps: f64, spread_bps: f64) -> f64 {
    clamp(return_bps / spread_bps.max(0.01), -1.0, 1.0)
}

fn directional_agreement(score: f64, components: &MicrostructureForecastComponents) -> f64 {
    if score.abs() < 1e-9 {
        return 0.5;
    }
    let sign = score.signum();
    let values = [
        components.aggressive_flow,
        components.displayed_liquidity,
        components.response_resiliency,
    ];
    let directional = values
        .into_iter()
        .filter(|value| value.abs() >= 0.05)
        .collect::<Vec<_>>();
    if directional.is_empty() {
        return 0.5;
    }
    directional
        .iter()
        .filter(|value| value.signum() == sign)
        .count() as f64
        / directional.len() as f64
}

fn direction(score: f64) -> &'static str {
    if score >= 0.35 {
        "up"
    } else if score >= 0.15 {
        "weak_up"
    } else if score <= -0.35 {
        "down"
    } else if score <= -0.15 {
        "weak_down"
    } else {
        "neutral"
    }
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
    use crate::bars::TradeUpdateRule;
    use crate::event::{QuoteEvent, TradeEvent};
    use chrono::{TimeZone, Utc};
    use serde_json::json;

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
        assert!((first.signed_volume_imbalance - 0.1667).abs() < 1e-4);
        assert!((first.level1_ofi - 0.125).abs() < 1e-9);
        assert!((first.queue_imbalance - 0.125).abs() < 1e-9);
        assert!((first.midpoint_return_bps - 0.0).abs() < 1e-6);
        assert!((first.trade_return_bps - 1.0).abs() < 0.01);
        assert_eq!(first.duration_us, 200_000);
    }

    #[test]
    fn aligned_bid_improvements_and_at_ask_trades_forecast_up() {
        let events = bullish_events(false);
        let forecast = forecast_market_events(&events, &rules(), "test");
        let fast = &forecast.horizons[0];
        assert_eq!(fast.status, "ready");
        assert!(matches!(fast.direction, "up" | "weak_up"));
        assert!(fast.score > 0.15);
        assert!(fast.components.quote_flow_imbalance > 0.0);
        assert!(fast.components.trade_flow_imbalance > 0.0);
    }

    #[test]
    fn aligned_ask_declines_and_at_bid_trades_forecast_down() {
        let mut events = bullish_events(false);
        for event in &mut events {
            match event {
                MarketEvent::Quote(quote) => {
                    let offset = quote.ts.timestamp_micros() as f64 / 1_000_000.0;
                    quote.bid_price = 100.20 - offset * 0.01;
                    quote.ask_price = 100.21 - offset * 0.01;
                    quote.bid_size = 80;
                    quote.ask_size = 300;
                }
                MarketEvent::Trade(trade) => trade.price = 100.0,
            }
        }
        let forecast = forecast_market_events(&events, &rules(), "test");
        assert!(forecast.horizons[0].score < -0.15);
        assert!(matches!(
            forecast.horizons[0].direction,
            "down" | "weak_down"
        ));
    }

    #[test]
    fn one_sided_flow_without_midpoint_response_is_absorption() {
        let events = bullish_events(true);
        let forecast = forecast_market_events(&events, &rules(), "test");
        assert!(forecast.horizons[0].absorption);
        assert_eq!(forecast.horizons[0].regime, "absorption");
    }

    #[test]
    fn same_events_produce_identical_snapshot() {
        let events = bullish_events(false);
        assert_eq!(
            forecast_market_events(&events, &rules(), "test"),
            forecast_market_events(&events, &rules(), "test")
        );
    }

    #[test]
    fn bar_snapshot_excludes_events_after_its_close() {
        let events = bullish_events(false);
        let as_of = events.last().unwrap().ts();
        let expected = forecast_market_events(&events, &rules(), "test");
        let mut window = MicrostructureForecastWindow::default();
        for event in &events {
            window.apply_event(event);
        }
        let mut future = events[0].clone();
        match &mut future {
            MarketEvent::Quote(quote) => {
                quote.ts = as_of + chrono::Duration::seconds(1);
                quote.ingest_ts = quote.ts;
                quote.bid_price = 90.0;
                quote.ask_price = 90.01;
            }
            MarketEvent::Trade(_) => unreachable!(),
        }
        window.apply_event(&future);
        assert_eq!(window.snapshot_at(as_of, &rules(), "test"), expected);
    }

    #[test]
    fn unified_forecast_weights_unique_horizons_and_emits_buy_only_with_evidence() {
        let horizons = vec![
            ready_horizon(25, 0.60, 80.0),
            ready_horizon(100, 0.40, 70.0),
            ready_horizon(500, 0.20, 60.0),
        ];
        let unified = unified_forecast(&horizons);
        assert_eq!(unified.action, "buy");
        assert!(unified.score > 0.4);
        assert!(unified.confidence >= 35.0);
    }

    #[test]
    fn unified_forecast_waits_when_horizons_conflict() {
        let horizons = vec![
            ready_horizon(25, 0.55, 45.0),
            ready_horizon(100, -0.50, 45.0),
            ready_horizon(500, 0.05, 45.0),
        ];
        let unified = unified_forecast(&horizons);
        assert_eq!(unified.action, "wait");
        assert!(unified.score.abs() < 0.15);
    }

    fn rules() -> TradeAggregationRules {
        TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap()
    }

    fn ready_horizon(
        horizon_events: usize,
        score: f64,
        confidence: f64,
    ) -> MicrostructureForecastHorizon {
        MicrostructureForecastHorizon {
            absorption: false,
            confidence,
            direction: direction(score),
            horizon_events,
            observed_duration_ms: 1_000.0,
            observed_events: horizon_events,
            quote_count: horizon_events,
            regime: "continuation",
            score,
            status: "ready",
            strength: score.abs() * 100.0,
            trade_count: 0,
            components: MicrostructureForecastComponents {
                aggressive_flow: score,
                arrival_intensity_imbalance: score,
                displayed_liquidity: score,
                level1_ofi: score,
                microprice_lean: score,
                midpoint_return: score,
                persistence: score,
                price_response: score,
                queue_imbalance: score,
                quote_flow_imbalance: score,
                regime_reliability: score.abs(),
                resiliency: score,
                response_resiliency: score,
                signed_volume_imbalance: score,
                trade_return: score,
                transaction_imbalance: score,
                trade_flow_imbalance: score,
            },
        }
    }

    fn bullish_events(flat: bool) -> Vec<MarketEvent> {
        let mut events = Vec::new();
        for index in 0..8u32 {
            let ts = Utc.timestamp_micros(index as i64 * 100_000).unwrap();
            let bid = 100.0 + if flat { 0.0 } else { index as f64 * 0.01 };
            events.push(MarketEvent::Quote(QuoteEvent {
                ask_exchange: 1,
                ask_price: bid + 0.01,
                ask_size: 80,
                bid_exchange: 2,
                bid_price: bid,
                bid_size: 300 + index * 20,
                conditions: Vec::new(),
                indicators: Vec::new(),
                ingest_ts: ts,
                raw: json!({}),
                sequence: index as u64,
                tape: 1,
                ticker: "AAPL".into(),
                ts,
            }));
            events.push(MarketEvent::Trade(TradeEvent {
                conditions: Vec::new(),
                exchange: 1,
                ingest_ts: ts,
                participant_ts: None,
                price: bid + 0.01,
                raw: json!({}),
                sequence: 100 + index as u64,
                size: 100.0,
                tape: 1,
                ticker: "AAPL".into(),
                trade_id: format!("trade-{index}"),
                trf_id: 0,
                trf_ts: None,
                ts,
            }));
        }
        events
    }
}
