use crate::bars::TradeAggregationRules;
use crate::compact_event::{CompactEventDecoder, LiveCompactEvent};
use crate::event::{MarketEvent, QuoteEvent};
use serde::Serialize;

pub const MICROSTRUCTURE_FORECAST_SCHEMA_VERSION: u16 = 1;
pub const MICROSTRUCTURE_FORECAST_METHOD: &str = "deterministic_microstructure_v1";
pub const MICROSTRUCTURE_FORECAST_HORIZONS: [usize; 3] = [25, 100, 500];

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MicrostructureForecastSnapshot {
    pub as_of_timestamp_us: u64,
    pub horizons: Vec<MicrostructureForecastHorizon>,
    pub method: &'static str,
    pub schema_version: u16,
    pub source: String,
    pub target: &'static str,
    pub ticker: String,
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
    pub microprice_lean: f64,
    pub persistence: f64,
    pub price_response: f64,
    pub quote_flow_imbalance: f64,
    pub trade_flow_imbalance: f64,
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
    let ticker = events
        .last()
        .map(MarketEvent::ticker)
        .unwrap_or_default()
        .to_ascii_uppercase();
    let as_of_timestamp_us = events
        .last()
        .map(|event| event.ts().timestamp_micros().max(0) as u64)
        .unwrap_or(0);
    let horizons = MICROSTRUCTURE_FORECAST_HORIZONS
        .into_iter()
        .map(|horizon| calculate_horizon(events, horizon, trade_rules))
        .collect();
    MicrostructureForecastSnapshot {
        as_of_timestamp_us,
        horizons,
        method: MICROSTRUCTURE_FORECAST_METHOD,
        schema_version: MICROSTRUCTURE_FORECAST_SCHEMA_VERSION,
        source: source.into(),
        target: "next_midpoint_move",
        ticker,
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
    let mut current_quote = seed_quote.clone();
    let mut quote_path = seed_quote.into_iter().collect::<Vec<_>>();
    let mut quote_count = 0usize;
    let mut quote_impulses = Vec::new();
    let mut directional_impulses = Vec::new();
    let mut buy_volume = 0.0;
    let mut sell_volume = 0.0;
    let mut eligible_trade_count = 0usize;
    let mut locked_crossed_quotes = 0usize;

    for event in window {
        match event {
            MarketEvent::Quote(quote) if valid_quote(quote) => {
                if quote.bid_price >= quote.ask_price {
                    locked_crossed_quotes += 1;
                }
                if let Some(previous) = current_quote.as_ref() {
                    let impulse = normalized_quote_flow(previous, quote);
                    quote_impulses.push(impulse);
                    directional_impulses.push(impulse.signum());
                }
                current_quote = Some(quote.clone());
                quote_path.push(quote.clone());
                quote_count += 1;
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
                eligible_trade_count += 1;
                if let Some(quote) = current_quote.as_ref() {
                    let epsilon = quote.ask_price.abs().max(quote.bid_price.abs()).max(1.0) * 1e-9;
                    if trade.price >= quote.ask_price - epsilon {
                        buy_volume += trade.size;
                        directional_impulses.push(1.0);
                    } else if trade.price <= quote.bid_price + epsilon {
                        sell_volume += trade.size;
                        directional_impulses.push(-1.0);
                    }
                }
            }
            _ => {}
        }
    }

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
    if quote_path.len() < 2 || current_quote.is_none() {
        return unavailable_horizon(
            horizon_events,
            observed_events,
            observed_duration_ms,
            quote_count,
            eligible_trade_count,
        );
    }

    let quote_flow_imbalance = recency_weighted_average(&quote_impulses);
    let latest_quote = current_quote.as_ref().expect("checked above");
    let microprice_lean = normalized_microprice_lean(latest_quote);
    let directional_volume = buy_volume + sell_volume;
    let trade_flow_imbalance = if directional_volume > 0.0 {
        (buy_volume - sell_volume) / directional_volume
    } else {
        0.0
    };
    let persistence = recency_weighted_average(&directional_impulses);
    let price_response = normalized_price_response(&quote_path);
    let components = MicrostructureForecastComponents {
        microprice_lean: clean(microprice_lean),
        persistence: clean(persistence),
        price_response: clean(price_response),
        quote_flow_imbalance: clean(quote_flow_imbalance),
        trade_flow_imbalance: clean(trade_flow_imbalance),
    };
    let score = clamp(
        0.35 * components.quote_flow_imbalance
            + 0.20 * components.microprice_lean
            + 0.20 * components.trade_flow_imbalance
            + 0.15 * components.persistence
            + 0.10 * components.price_response,
        -1.0,
        1.0,
    );
    let absorption =
        components.trade_flow_imbalance.abs() >= 0.35 && components.price_response.abs() <= 0.15;
    let agreement = directional_agreement(score, &components);
    let coverage = (observed_events as f64 / horizon_events as f64).clamp(0.0, 1.0);
    let quote_quality =
        1.0 - (locked_crossed_quotes as f64 / quote_count.max(1) as f64).clamp(0.0, 0.75);
    let confidence = clamp(
        100.0
            * coverage
            * quote_quality
            * (0.45 + 0.55 * agreement)
            * (0.50 + 0.50 * score.abs())
            * if absorption { 0.75 } else { 1.0 },
        0.0,
        100.0,
    );
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
        quote_count,
        regime,
        score: round4(score),
        status: "ready",
        strength: round2(score.abs() * 100.0),
        trade_count: eligible_trade_count,
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
            microprice_lean: 0.0,
            persistence: 0.0,
            price_response: 0.0,
            quote_flow_imbalance: 0.0,
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

fn normalized_quote_flow(previous: &QuoteEvent, current: &QuoteEvent) -> f64 {
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
    clamp(flow / depth.max(1.0), -1.0, 1.0)
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

fn normalized_price_response(quotes: &[QuoteEvent]) -> f64 {
    let first = &quotes[0];
    let last = quotes.last().expect("non-empty quote list");
    let first_midpoint = (first.bid_price + first.ask_price) / 2.0;
    let last_midpoint = (last.bid_price + last.ask_price) / 2.0;
    let mut spreads = quotes
        .iter()
        .map(|quote| (quote.ask_price - quote.bid_price).max(0.0))
        .filter(|spread| *spread > 0.0)
        .collect::<Vec<_>>();
    spreads.sort_by(f64::total_cmp);
    let reference_spread = spreads
        .get(spreads.len() / 2)
        .copied()
        .unwrap_or(0.01)
        .max(0.0001);
    clamp(
        (last_midpoint - first_midpoint) / reference_spread,
        -1.0,
        1.0,
    )
}

fn recency_weighted_average(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let half_life = (values.len() as f64 / 3.0).max(1.0);
    let mut weighted = 0.0;
    let mut weights = 0.0;
    for (index, value) in values.iter().enumerate() {
        let age = (values.len() - 1 - index) as f64;
        let weight = 0.5_f64.powf(age / half_life);
        weighted += value * weight;
        weights += weight;
    }
    clamp(weighted / weights.max(f64::EPSILON), -1.0, 1.0)
}

fn directional_agreement(score: f64, components: &MicrostructureForecastComponents) -> f64 {
    if score.abs() < 1e-9 {
        return 0.5;
    }
    let sign = score.signum();
    let values = [
        components.quote_flow_imbalance,
        components.microprice_lean,
        components.trade_flow_imbalance,
        components.persistence,
        components.price_response,
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

fn clean(value: f64) -> f64 {
    round4(clamp(
        if value.is_finite() { value } else { 0.0 },
        -1.0,
        1.0,
    ))
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

    fn rules() -> TradeAggregationRules {
        TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap()
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
