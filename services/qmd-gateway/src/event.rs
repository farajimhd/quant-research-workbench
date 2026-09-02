use chrono::{DateTime, TimeZone, Utc};
use serde::Serialize;
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

#[derive(Clone, Debug, Serialize)]
pub struct LuldEvent {
    pub high_price: f64,
    pub indicators: Vec<u16>,
    pub low_price: f64,
    pub raw: Value,
    pub sequence: u64,
    pub tape: u8,
    pub ticker: String,
    pub ts: DateTime<Utc>,
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

    /// Timestamp at which the event became available to a causal consumer.
    pub fn availability_ts(&self) -> DateTime<Utc> {
        self.ts()
    }

    /// Exchange execution timestamp when supplied, otherwise the SIP timestamp.
    pub fn execution_ts(&self) -> DateTime<Utc> {
        match self {
            MarketEvent::Trade(event) => event.participant_ts.unwrap_or(event.ts),
            MarketEvent::Quote(event) => event.ts,
        }
    }

    /// True when a trade belongs to a one-second bucket that was already complete
    /// before the report became available. Such reports remain canonical audit
    /// events but must not revise causal bars, indicators, structure, or gates.
    pub fn is_delayed_trade_report(&self) -> bool {
        match self {
            MarketEvent::Trade(event) => event
                .participant_ts
                .is_some_and(|execution| execution.timestamp() < event.ts.timestamp()),
            MarketEvent::Quote(_) => false,
        }
    }

    /// Clone an event for retrospective chart projection. Availability remains
    /// present in `raw`; only the chart bucket clock moves to execution time.
    pub fn for_execution_time_chart(&self) -> Self {
        let mut projected = self.clone();
        if let MarketEvent::Trade(event) = &mut projected {
            if let Some(execution) = event.participant_ts {
                event.ts = execution;
            }
        }
        projected
    }

    pub fn arrival_sequence(&self) -> u64 {
        let raw = match self {
            MarketEvent::Trade(event) => &event.raw,
            MarketEvent::Quote(event) => &event.raw,
        };
        raw.get("arrival_sequence")
            .and_then(Value::as_u64)
            .unwrap_or_default()
    }
}

#[cfg(test)]
mod clock_tests {
    use super::*;
    use serde_json::json;

    fn trade(sip_ms: i64, participant_ms: i64) -> MarketEvent {
        let sip = Utc.timestamp_millis_opt(sip_ms).single().unwrap();
        MarketEvent::Trade(TradeEvent {
            conditions: vec![12],
            exchange: 4,
            ingest_ts: sip,
            participant_ts: Some(Utc.timestamp_millis_opt(participant_ms).single().unwrap()),
            price: 3.5,
            raw: json!({"sip_timestamp_ms": sip_ms}),
            sequence: 1,
            size: 100.0,
            tape: 3,
            ticker: "TEST".to_string(),
            trade_id: "1".to_string(),
            trf_id: 0,
            trf_ts: None,
            ts: sip,
        })
    }

    #[test]
    fn same_second_form_t_remains_current_state_eligible() {
        let event = trade(1_750_000_000_900, 1_750_000_000_100);
        assert!(!event.is_delayed_trade_report());
    }

    #[test]
    fn prior_second_report_is_audit_only_for_current_state() {
        let event = trade(1_750_000_001_001, 1_750_000_000_999);
        assert!(event.is_delayed_trade_report());
        assert_eq!(event.for_execution_time_chart().ts(), event.execution_ts());
        assert_eq!(
            event.availability_ts().timestamp_millis(),
            1_750_000_001_001
        );
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

pub fn parse_massive_luld_payload(text: &str) -> Result<Vec<LuldEvent>, serde_json::Error> {
    let payload: Value = serde_json::from_str(text)?;
    let items = match payload {
        Value::Array(items) => items,
        item => vec![item],
    };
    Ok(items
        .into_iter()
        .filter(|item| string_field(item, "ev") == "LULD")
        .filter_map(|item| {
            Some(LuldEvent {
                high_price: f64_field(&item, "h"),
                indicators: u16_array_field(&item, "i"),
                low_price: f64_field(&item, "l"),
                raw: item.clone(),
                sequence: u64_field(&item, "q"),
                tape: u8_field(&item, "z"),
                ticker: string_field(&item, "T").to_ascii_uppercase(),
                ts: optional_epoch_field(&item, "t")?,
            })
        })
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
    let ts = optional_millis_field(&item, "t")?;
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
            ts,
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
            ts,
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

fn optional_epoch_field(item: &Value, key: &str) -> Option<DateTime<Utc>> {
    let value = u64_field(item, key);
    if value == 0 {
        return None;
    }
    if value >= 100_000_000_000_000_000 {
        let seconds = i64::try_from(value / 1_000_000_000).ok()?;
        let nanos = u32::try_from(value % 1_000_000_000).ok()?;
        Utc.timestamp_opt(seconds, nanos).single()
    } else if value >= 100_000_000_000_000 {
        Utc.timestamp_micros(i64::try_from(value).ok()?).single()
    } else {
        Utc.timestamp_millis_opt(i64::try_from(value).ok()?)
            .single()
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_sip_timestamp_is_structurally_rejected() {
        let payload = r#"[{"ev":"T","sym":"TEST","q":1,"p":10.0,"s":1}]"#;
        assert!(parse_massive_payload(payload).unwrap().is_empty());
    }

    #[test]
    fn zero_sequence_reaches_compact_structural_validation() {
        let payload = r#"[{"ev":"T","sym":"TEST","t":1700000000000,"q":0,"p":10.0,"s":1}]"#;
        let rows = parse_massive_payload(payload).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(
            match &rows[0] {
                MarketEvent::Trade(row) => row.sequence,
                _ => 1,
            },
            0
        );
    }

    #[test]
    fn luld_parser_preserves_nanosecond_halt_timestamp_and_indicator() {
        let payload = r#"[{"ev":"LULD","T":"HALT","h":10.5,"l":9.5,"i":[17],"z":3,"t":1764086430905642800,"q":42}]"#;
        let rows = parse_massive_luld_payload(payload).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].ticker, "HALT");
        assert_eq!(rows[0].indicators, vec![17]);
        assert_eq!(rows[0].ts.timestamp_nanos_opt(), Some(1764086430905642800));
    }
}
