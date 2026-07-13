use crate::config::GatewayConfig;
use chrono::{DateTime, Datelike, NaiveDate, TimeZone, Timelike, Utc, Weekday};
use chrono_tz::America::New_York;
use reqwest::Client;
use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::{Arc, RwLock};

#[derive(Clone, Debug, Default)]
struct Holiday {
    close_utc: Option<DateTime<Utc>>,
    name: String,
    status: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct MarketSnapshot {
    pub active_collection_window: bool,
    pub market_closed: bool,
    pub status_age_seconds: Option<i64>,
    pub stale: bool,
    pub source: String,
    pub reason: String,
}

pub async fn run_market_calendar_refresh(client: MarketCalendarClient) {
    loop {
        client.refresh(Utc::now()).await;
        tokio::time::sleep(std::time::Duration::from_secs(
            client.config.market_status_refresh_seconds.max(1),
        ))
        .await;
    }
}

#[derive(Clone, Default)]
struct CalendarCache {
    after_hours: bool,
    early_hours: bool,
    holidays: HashMap<NaiveDate, Holiday>,
    market: String,
    status_fetched_at: Option<DateTime<Utc>>,
    holidays_fetched_at: Option<DateTime<Utc>>,
}

#[derive(Clone)]
pub struct MarketCalendarClient {
    cache: Arc<RwLock<CalendarCache>>,
    client: Client,
    config: GatewayConfig,
}

impl MarketCalendarClient {
    pub fn new(config: GatewayConfig) -> Self {
        Self {
            cache: Arc::new(RwLock::new(CalendarCache::default())),
            client: Client::new(),
            config,
        }
    }

    pub async fn refresh(&self, now: DateTime<Utc>) {
        if !self.config.market_status_enabled || self.config.massive_api_key.is_empty() {
            return;
        }
        let (refresh_status, refresh_holidays) = self
            .cache
            .read()
            .map(|cache| {
                (
                    cache
                        .status_fetched_at
                        .map(|value| {
                            (now - value).num_seconds()
                                >= self.config.market_status_refresh_seconds as i64
                        })
                        .unwrap_or(true),
                    cache
                        .holidays_fetched_at
                        .map(|value| {
                            (now - value).num_seconds()
                                >= self.config.market_holidays_refresh_seconds as i64
                        })
                        .unwrap_or(true),
                )
            })
            .unwrap_or((true, true));
        if refresh_holidays {
            match self.fetch_json(&self.config.market_holidays_url).await {
                Ok(payload) => {
                    let rows = payload
                        .as_array()
                        .cloned()
                        .or_else(|| payload.get("results").and_then(Value::as_array).cloned())
                        .or_else(|| payload.get("response").and_then(Value::as_array).cloned())
                        .unwrap_or_default();
                    let mut holidays = HashMap::new();
                    for row in rows {
                        let exchange = row
                            .get("exchange")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_ascii_uppercase();
                        if !matches!(exchange.as_str(), "NYSE" | "NASDAQ") {
                            continue;
                        }
                        let Some(date) =
                            row.get("date").and_then(Value::as_str).and_then(|value| {
                                NaiveDate::parse_from_str(&value[..value.len().min(10)], "%Y-%m-%d")
                                    .ok()
                            })
                        else {
                            continue;
                        };
                        let status = row
                            .get("status")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_ascii_lowercase();
                        let holiday = Holiday {
                            close_utc: row
                                .get("close")
                                .and_then(Value::as_str)
                                .and_then(parse_datetime),
                            name: row
                                .get("name")
                                .and_then(Value::as_str)
                                .unwrap_or_default()
                                .to_string(),
                            status,
                        };
                        holidays
                            .entry(date)
                            .and_modify(|existing: &mut Holiday| {
                                if holiday.status == "closed"
                                    || holiday.close_utc > existing.close_utc
                                {
                                    *existing = holiday.clone();
                                }
                            })
                            .or_insert(holiday);
                    }
                    if let Ok(mut cache) = self.cache.write() {
                        cache.holidays = holidays;
                        cache.holidays_fetched_at = Some(now);
                    }
                }
                Err(error) => {
                    eprintln!("Massive market-holiday refresh failed; retaining cached/local calendar: {error}");
                    if let Ok(mut cache) = self.cache.write() {
                        let refresh = self.config.market_holidays_refresh_seconds as i64;
                        let retry = refresh.min(300);
                        cache.holidays_fetched_at =
                            Some(now - chrono::Duration::seconds(refresh.saturating_sub(retry)));
                    }
                }
            }
        }
        if refresh_status {
            match self.fetch_json(&self.config.market_status_url).await {
                Ok(payload) => {
                    if let Ok(mut cache) = self.cache.write() {
                        cache.market = payload.get("market").and_then(Value::as_str).unwrap_or_default().to_ascii_lowercase();
                        cache.early_hours = json_bool(payload.get("earlyHours"));
                        cache.after_hours = json_bool(payload.get("afterHours"));
                        cache.status_fetched_at = Some(now);
                    }
                }
                Err(error) => eprintln!("Massive market-status refresh failed; retaining cached/local schedule: {error}"),
            }
        }
    }

    pub fn is_session_date(&self, date: NaiveDate) -> bool {
        if matches!(date.weekday(), Weekday::Sat | Weekday::Sun) {
            return false;
        }
        !self
            .cache
            .read()
            .ok()
            .and_then(|cache| cache.holidays.get(&date).cloned())
            .map(|holiday| holiday.status == "closed")
            .unwrap_or(false)
    }

    pub fn prior_sessions(&self, anchor: NaiveDate, count: usize) -> Vec<NaiveDate> {
        let mut cursor = anchor;
        let mut out = Vec::new();
        while out.len() < count {
            if self.is_session_date(cursor) {
                out.push(cursor);
            }
            cursor -= chrono::Duration::days(1);
        }
        out.reverse();
        out
    }

    pub fn collection_window_utc(
        &self,
        date: NaiveDate,
        now: DateTime<Utc>,
    ) -> Option<(DateTime<Utc>, DateTime<Utc>)> {
        if !self.is_session_date(date) {
            return None;
        }
        let start = New_York
            .with_ymd_and_hms(date.year(), date.month(), date.day(), 4, 0, 0)
            .single()?
            .with_timezone(&Utc);
        let default_end = New_York
            .with_ymd_and_hms(date.year(), date.month(), date.day(), 20, 0, 0)
            .single()?
            .with_timezone(&Utc);
        let scheduled_end = self
            .cache
            .read()
            .ok()
            .and_then(|cache| {
                cache
                    .holidays
                    .get(&date)
                    .and_then(|holiday| holiday.close_utc)
            })
            .unwrap_or(default_end);
        if now <= start {
            return None;
        }
        let end = now.min(scheduled_end);
        (end > start).then_some((start, end))
    }

    pub fn snapshot(&self, now: DateTime<Utc>) -> MarketSnapshot {
        let local = now.with_timezone(&New_York);
        let date = local.date_naive();
        let minute = local.hour() * 60 + local.minute();
        let cache = self.cache.read().ok();
        let holiday = cache.as_ref().and_then(|value| value.holidays.get(&date));
        if let Some(holiday) = holiday {
            if holiday.status == "closed" {
                return MarketSnapshot {
                    active_collection_window: false,
                    market_closed: true,
                    status_age_seconds: cache.as_ref().and_then(|value| {
                        value
                            .status_fetched_at
                            .map(|time| (now - time).num_seconds())
                    }),
                    stale: false,
                    source: "massive_market_calendar".into(),
                    reason: format!("holiday_closed:{}", holiday.name),
                };
            }
            if holiday.status == "early-close"
                && holiday.close_utc.map(|close| now >= close).unwrap_or(false)
            {
                return MarketSnapshot {
                    active_collection_window: false,
                    market_closed: true,
                    status_age_seconds: cache.as_ref().and_then(|value| {
                        value
                            .status_fetched_at
                            .map(|time| (now - time).num_seconds())
                    }),
                    stale: false,
                    source: "massive_market_calendar".into(),
                    reason: format!("early_close_elapsed:{}", holiday.name),
                };
            }
        }
        if matches!(date.weekday(), Weekday::Sat | Weekday::Sun) {
            return MarketSnapshot {
                active_collection_window: false,
                market_closed: true,
                status_age_seconds: cache.as_ref().and_then(|value| {
                    value
                        .status_fetched_at
                        .map(|time| (now - time).num_seconds())
                }),
                stale: false,
                source: "local_schedule".into(),
                reason: "weekend".into(),
            };
        }
        if let Some(cache) = cache.as_ref() {
            let age = cache
                .status_fetched_at
                .map(|time| (now - time).num_seconds());
            if age
                .map(|value| value <= (self.config.market_status_refresh_seconds * 3) as i64)
                .unwrap_or(false)
            {
                let active = cache.early_hours
                    || cache.after_hours
                    || matches!(
                        cache.market.as_str(),
                        "open"
                            | "early-hours"
                            | "after-hours"
                            | "early_hours"
                            | "after_hours"
                            | "extended-hours"
                            | "extended_hours"
                    );
                return MarketSnapshot {
                    active_collection_window: active,
                    market_closed: !active,
                    status_age_seconds: age,
                    stale: false,
                    source: "massive_market_calendar".into(),
                    reason: if active {
                        "massive_status_active".into()
                    } else {
                        "massive_status_closed".into()
                    },
                };
            }
        }
        let active = (4 * 60..20 * 60).contains(&minute);
        MarketSnapshot {
            active_collection_window: active,
            market_closed: !active,
            status_age_seconds: cache.as_ref().and_then(|value| {
                value
                    .status_fetched_at
                    .map(|time| (now - time).num_seconds())
            }),
            stale: cache
                .as_ref()
                .and_then(|value| value.status_fetched_at)
                .is_some(),
            source: "local_schedule_fallback".into(),
            reason: if cache
                .as_ref()
                .and_then(|value| value.status_fetched_at)
                .is_some()
            {
                "massive_status_stale".into()
            } else {
                "massive_status_unavailable".into()
            },
        }
    }

    async fn fetch_json(&self, url: &str) -> Result<Value, String> {
        let separator = if url.contains('?') { '&' } else { '?' };
        let response = self
            .client
            .get(format!(
                "{url}{separator}apiKey={}",
                urlencoding::encode(&self.config.massive_api_key)
            ))
            .header("User-Agent", "qmd-gateway-market-hours/1.0")
            .send()
            .await
            .map_err(|error| error.to_string())?;
        let status = response.status();
        let text = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("HTTP {status}: {text}"));
        }
        serde_json::from_str(&text).map_err(|error| error.to_string())
    }
}

fn json_bool(value: Option<&Value>) -> bool {
    value
        .and_then(Value::as_bool)
        .or_else(|| {
            value.and_then(Value::as_str).map(|value| {
                matches!(
                    value.to_ascii_lowercase().as_str(),
                    "1" | "true" | "yes" | "on"
                )
            })
        })
        .unwrap_or(false)
}

fn parse_datetime(value: &str) -> Option<DateTime<Utc>> {
    DateTime::parse_from_rfc3339(&value.replace('Z', "+00:00"))
        .ok()
        .map(|value| value.with_timezone(&Utc))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prior_sessions_exclude_weekends_and_cached_full_holidays() {
        let client = MarketCalendarClient::new(GatewayConfig::from_env());
        let holiday_date = NaiveDate::from_ymd_opt(2026, 7, 3).unwrap();
        client.cache.write().unwrap().holidays.insert(
            holiday_date,
            Holiday {
                close_utc: None,
                name: "Independence Day".to_string(),
                status: "closed".to_string(),
            },
        );
        let monday = NaiveDate::from_ymd_opt(2026, 7, 6).unwrap();
        assert_eq!(
            client.prior_sessions(monday, 3),
            vec![
                NaiveDate::from_ymd_opt(2026, 7, 1).unwrap(),
                NaiveDate::from_ymd_opt(2026, 7, 2).unwrap(),
                monday,
            ]
        );
    }
}
