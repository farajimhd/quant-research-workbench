use chrono::{DateTime, TimeZone, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum MarketEvent {
    Trade(TradeEvent),
    Quote(QuoteEvent),
}

#[derive(Clone, Debug, Serialize)]
pub struct TradeEvent {
    pub conditions: Vec<u16>,
    pub exchange: u16,
    pub ingest_ts: DateTime<Utc>,
    pub participant_ts: Option<DateTime<Utc>>,
    pub price: f64,
    pub raw: Value,
    pub sequence: u64,
    pub size: f64,
    pub tape: u8,
    pub ticker: String,
    pub trade_id: String,
    pub trf_id: u16,
    pub trf_ts: Option<DateTime<Utc>>,
    pub ts: DateTime<Utc>,
}

#[derive(Clone, Debug, Serialize)]
pub struct QuoteEvent {
    pub ask_exchange: u16,
    pub ask_price: f64,
    pub ask_size: u32,
    pub bid_exchange: u16,
    pub bid_price: f64,
    pub bid_size: u32,
    pub conditions: Vec<u16>,
    pub indicators: Vec<u16>,
    pub ingest_ts: DateTime<Utc>,
    pub raw: Value,
    pub sequence: u64,
    pub tape: u8,
    pub ticker: String,
    pub ts: DateTime<Utc>,
}

#[derive(Debug, Deserialize)]
pub struct MassiveMessage {
    #[serde(flatten)]
    pub value: Value,
}

impl MarketEvent {
    pub fn ticker(&self) -> &str {
        match self {
            MarketEvent::Trade(event) => &event.ticker,
            MarketEvent::Quote(event) => &event.ticker,
        }
    }

    pub fn ts(&self) -> DateTime<Utc> {
        match self {
            MarketEvent::Trade(event) => event.ts,
            MarketEvent::Quote(event) => event.ts,
        }
    }
}

pub fn parse_massive_payload(text: &str) -> Result<Vec<MarketEvent>, serde_json::Error> {
    let payload: Value = serde_json::from_str(text)?;
    let items = match payload {
        Value::Array(items) => items,
        item => vec![item],
    };
    let now = Utc::now();
    Ok(items
        .into_iter()
        .filter_map(|item| parse_massive_item(item, now))
        .collect())
}

pub fn massive_status_message(text: &str) -> Option<String> {
    let payload: Value = serde_json::from_str(text).ok()?;
    let items = match payload {
        Value::Array(items) => items,
        item => vec![item],
    };
    let mut messages = Vec::new();
    for item in items {
        let event_type = string_field(&item, "ev");
        let status = string_field(&item, "status");
        if event_type == "status" || !status.is_empty() {
            let message = string_field(&item, "message");
            messages.push(if message.is_empty() { status } else { message });
        }
    }
    if messages.is_empty() {
        None
    } else {
        Some(messages.join("; "))
    }
}

fn parse_massive_item(item: Value, ingest_ts: DateTime<Utc>) -> Option<MarketEvent> {
    match string_field(&item, "ev").as_str() {
        "T" => Some(MarketEvent::Trade(TradeEvent {
            conditions: u16_array_field(&item, "c"),
            exchange: u16_field(&item, "x"),
            ingest_ts,
            participant_ts: optional_millis_field(&item, "pt"),
            price: f64_field(&item, "p"),
            raw: item.clone(),
            sequence: u64_field(&item, "q"),
            size: f64_field(&item, "s").max(f64_field(&item, "ds")),
            tape: u8_field(&item, "z"),
            ticker: string_field(&item, "sym").to_ascii_uppercase(),
            trade_id: string_field(&item, "i"),
            trf_id: u16_field(&item, "trfi"),
            trf_ts: optional_millis_field(&item, "trft"),
            ts: optional_millis_field(&item, "t").unwrap_or(ingest_ts),
        })),
        "Q" => Some(MarketEvent::Quote(QuoteEvent {
            ask_exchange: u16_field(&item, "ax"),
            ask_price: f64_field(&item, "ap"),
            ask_size: u32_field(&item, "as"),
            bid_exchange: u16_field(&item, "bx"),
            bid_price: f64_field(&item, "bp"),
            bid_size: u32_field(&item, "bs"),
            conditions: u16_array_field(&item, "c"),
            indicators: u16_array_field(&item, "i"),
            ingest_ts,
            raw: item.clone(),
            sequence: u64_field(&item, "q"),
            tape: u8_field(&item, "z"),
            ticker: string_field(&item, "sym").to_ascii_uppercase(),
            ts: optional_millis_field(&item, "t").unwrap_or(ingest_ts),
        })),
        _ => None,
    }
}

fn optional_millis_field(item: &Value, key: &str) -> Option<DateTime<Utc>> {
    let millis = u64_field(item, key);
    if millis == 0 {
        return None;
    }
    Utc.timestamp_millis_opt(millis as i64).single()
}

fn string_field(item: &Value, key: &str) -> String {
    item.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn f64_field(item: &Value, key: &str) -> f64 {
    item.get(key).and_then(Value::as_f64).unwrap_or_default()
}

fn u64_field(item: &Value, key: &str) -> u64 {
    item.get(key).and_then(Value::as_u64).unwrap_or_default()
}

fn u32_field(item: &Value, key: &str) -> u32 {
    u64_field(item, key).min(u32::MAX as u64) as u32
}

fn u16_field(item: &Value, key: &str) -> u16 {
    u64_field(item, key).min(u16::MAX as u64) as u16
}

fn u8_field(item: &Value, key: &str) -> u8 {
    u64_field(item, key).min(u8::MAX as u64) as u8
}

fn u16_array_field(item: &Value, key: &str) -> Vec<u16> {
    item.get(key)
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_u64)
                .map(|value| value.min(u16::MAX as u64) as u16)
                .collect()
        })
        .unwrap_or_default()
}
