use arte_core::{Error, Result};
use serde_json::Value;
use std::time::Duration;

pub fn identifier(value: &str) -> Result<&str> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'_')
        || value.as_bytes()[0].is_ascii_digit()
    {
        return Err(Error::Invalid("invalid SQL identifier".into()));
    }
    Ok(value)
}
pub struct ClickHouse {
    http: reqwest::Client,
    url: reqwest::Url,
    database: String,
    user: String,
    password: String,
}
impl ClickHouse {
    pub fn new(url: &str, database: &str, user: String, password: String) -> Result<Self> {
        identifier(database)?;
        if matches!(database, "default" | "q_live" | "market_sip_compact") {
            return Err(Error::Invalid(
                "legacy/default database is forbidden".into(),
            ));
        }
        let url = reqwest::Url::parse(url)
            .map_err(|_| Error::Invalid("invalid ClickHouse URL".into()))?;
        if !matches!(url.scheme(), "http" | "https")
            || !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(Error::Invalid(
                "ClickHouse URL must not embed credentials or query".into(),
            ));
        }
        Ok(Self {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(30))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .map_err(|_| Error::Invalid("HTTP client configuration".into()))?,
            url,
            database: database.into(),
            user,
            password,
        })
    }
    async fn request(&self, query: &str, body: String) -> Result<String> {
        let response = self
            .http
            .post(self.url.clone())
            .basic_auth(&self.user, Some(&self.password))
            .query(&[
                ("database", self.database.as_str()),
                ("query", query),
                ("async_insert", "0"),
                ("wait_end_of_query", "1"),
            ])
            .body(body)
            .send()
            .await
            .map_err(|_| {
                Error::Unready("ClickHouse transport failure; credentials redacted".into())
            })?;
        if !response.status().is_success() {
            return Err(Error::Unready(format!(
                "ClickHouse HTTP {}",
                response.status().as_u16()
            )));
        }
        response
            .text()
            .await
            .map_err(|_| Error::Unready("ClickHouse response incomplete".into()))
    }
    /// Read-only storage preflight. Does not create or migrate tables.
    pub async fn verify_storage(&self, table: &str) -> Result<()> {
        identifier(table)?;
        let policy_sql="SELECT arrayJoin(disks) AS disk FROM system.storage_policies WHERE policy_name='live_market_ssd' FORMAT JSONEachRow";
        let policy = self.request(policy_sql, String::new()).await?;
        if policy.trim().is_empty() {
            return Err(Error::Unready("live_market_ssd policy absent".into()));
        }
        for line in policy.lines() {
            let row: Value = serde_json::from_str(line)
                .map_err(|_| Error::Invalid("invalid storage policy response".into()))?;
            let disk = row
                .get("disk")
                .and_then(Value::as_str)
                .ok_or_else(|| Error::Invalid("missing policy disk".into()))?;
            if matches!(disk, "default" | "hdd") || disk.is_empty() {
                return Err(Error::Unready(
                    "backup/default disk in operational policy".into(),
                ));
            }
        }
        let sql=format!("SELECT storage_policy FROM system.tables WHERE database='{}' AND name='{}' FORMAT JSONEachRow",self.database,table);
        let rows = self.request(&sql, String::new()).await?;
        let row: Value = serde_json::from_str(rows.trim())
            .map_err(|_| Error::Unready("required table missing or ambiguous".into()))?;
        if row.get("storage_policy").and_then(Value::as_str) != Some("live_market_ssd") {
            return Err(Error::Unready(
                "required live_market_ssd policy not configured".into(),
            ));
        }
        let sql=format!("SELECT count() AS bad FROM system.parts WHERE active AND database='{}' AND table='{}' AND disk_name NOT IN (SELECT arrayJoin(disks) FROM system.storage_policies WHERE policy_name='live_market_ssd') FORMAT JSONEachRow",self.database,table);
        let row: Value = serde_json::from_str(self.request(&sql, String::new()).await?.trim())
            .map_err(|_| Error::Invalid("invalid part placement response".into()))?;
        let bad = row
            .get("bad")
            .and_then(|v| {
                v.as_u64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .ok_or_else(|| Error::Invalid("missing part count".into()))?;
        if bad != 0 {
            return Err(Error::Unready(
                "operational parts outside approved disks".into(),
            ));
        }
        Ok(())
    }
    /// Synchronous insert acknowledgment only. Power-loss durability needs deployment checks.
    pub async fn insert(&self, table: &str, rows: &[Value]) -> Result<()> {
        identifier(table)?;
        if rows.is_empty() {
            return Ok(());
        }
        self.verify_storage(table).await?;
        let mut body = String::new();
        for row in rows {
            body.push_str(
                &serde_json::to_string(row)
                    .map_err(|_| Error::Invalid("invalid insert row".into()))?,
            );
            body.push('\n');
        }
        self.request(
            &format!("INSERT INTO {}.{} FORMAT JSONEachRow", self.database, table),
            body,
        )
        .await?;
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn identifiers_cannot_escape_database() {
        assert!(identifier("orders; DROP TABLE x").is_err());
        assert!(identifier("arte_events").is_ok());
        assert!(ClickHouse::new(
            "http://localhost:8123",
            "market_sip_compact",
            "u".into(),
            "p".into()
        )
        .is_err());
    }
}
