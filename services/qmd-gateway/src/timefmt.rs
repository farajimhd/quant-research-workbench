use chrono::{DateTime, Utc};

pub fn clickhouse_datetime64(value: &DateTime<Utc>) -> String {
    value.format("%Y-%m-%d %H:%M:%S%.3f").to_string()
}

pub fn clickhouse_datetime64_opt(value: Option<&DateTime<Utc>>) -> Option<String> {
    value.map(clickhouse_datetime64)
}
