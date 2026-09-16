use arte_core::publication::SeedManifest;
use arte_core::seed_storage::{Bundle, Object};
use arte_core::v7_seed::HistoricalSeed;
use arte_core::{Error, Result};
use serde_json::Value;
use std::collections::BTreeMap;
use std::time::Duration;
mod coverage_index;
mod event_batches;
mod execution_checkpoints;
mod fills;
mod order_authorizations;
mod portfolio_checkpoints;
mod quote_policies;
mod references;
mod rejections;
mod run_checkpoints;
mod run_manifests;
pub use execution_checkpoints::{execution_checkpoint_scope, ExecutionRecovery};
pub use fills::FillPublisher;
pub use order_authorizations::OrderPublisher;
pub use portfolio_checkpoints::portfolio_checkpoint_scope;
#[cfg(test)]
pub(crate) use rejections::exercise as rejection_roundtrip_test;
pub use rejections::RejectionPublisher;
pub use run_checkpoints::backtest_checkpoint_scope;
#[cfg(test)]
pub(crate) use run_checkpoints::tests::publication as checkpoint_publication_test;
#[cfg(test)]
pub(crate) use run_checkpoints::tests::roundtrip as checkpoint_roundtrip_test;
pub use run_manifests::run_manifest_scope;

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
    /// Caller must hold exclusive journal scope ownership. This acknowledges
    /// synchronous insert plus readback, NOT verified power-loss durability.
    pub async fn append_decisions(
        &self,
        batch: &arte_core::journal::Batch,
    ) -> Result<Vec<arte_core::journal::Record>> {
        let records = batch.records();
        let first = &records[0];
        let last = records.last().unwrap().sequence;
        self.verify_storage("decision_journal_v1").await?;
        if first.sequence > 1 {
            let previous = self
                .read_decisions(&first.scope_hash, first.sequence - 1, first.sequence - 1)
                .await?;
            if previous.is_empty() {
                return Err(Error::Unready("journal predecessor missing".into()));
            }
            for row in &previous {
                row.decode()?;
            }
            if previous
                .iter()
                .any(|r| r.payload_json != previous[0].payload_json)
            {
                return Err(Error::Conflict("journal predecessor conflict".into()));
            }
        }
        let existing = self
            .read_decisions(&first.scope_hash, first.sequence, last)
            .await?;
        for row in &existing {
            row.decode()?;
            let index = (row.sequence - first.sequence) as usize;
            if records[index].payload_json != row.payload_json {
                return Err(Error::Conflict(
                    "journal slot already has a different decision".into(),
                ));
            }
        }
        let rows: Vec<Value> = records
            .iter()
            .filter(|r| !existing.iter().any(|e| e.sequence == r.sequence))
            .map(|r| serde_json::to_value(r).map_err(|e| Error::Serialization(e.to_string())))
            .collect::<Result<_>>()?;
        self.insert("decision_journal_v1", &rows).await?;
        let readback = self
            .read_decisions(&first.scope_hash, first.sequence, last)
            .await?;
        batch.verify_readback(&readback)?;
        Ok(readback)
    }
    /// Leave a failed or ambiguous append prepared for an exact retry.
    pub async fn commit_strategy<S: Clone + serde::Serialize>(
        &self,
        runtime: &mut arte_core::strategy_transaction::Runtime<S>,
    ) -> Result<arte_core::strategy_transaction::Committed> {
        let batch = runtime
            .pending_batch()
            .ok_or_else(|| Error::Unready("no prepared strategy journal batch".into()))?;
        let readback = self.append_decisions(batch).await?;
        runtime.acknowledge(&readback)
    }
    pub async fn read_decisions(
        &self,
        scope_hash: &str,
        first: u64,
        last: u64,
    ) -> Result<Vec<arte_core::journal::Record>> {
        if scope_hash.len() != 64
            || !scope_hash.bytes().all(|b| b.is_ascii_hexdigit())
            || first == 0
            || last < first
            || last - first >= arte_core::journal::MAX_BATCH as u64
        {
            return Err(Error::Invalid("invalid bounded journal cursor".into()));
        }
        let query=format!("SELECT DISTINCT scope_hash, sequence, payload_json FROM {}.decision_journal_v1 WHERE scope_hash='{scope_hash}' AND sequence BETWEEN {first} AND {last} ORDER BY sequence LIMIT {} FORMAT JSONEachRow",self.database,2*arte_core::journal::MAX_BATCH+1);
        let body = self.request(&query, String::new()).await?;
        let mut records = Vec::new();
        for line in body.lines().filter(|l| !l.trim().is_empty()) {
            let mut value: Value =
                serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
            if let Some(s) = value.get("sequence").and_then(Value::as_str) {
                value["sequence"] = Value::from(
                    s.parse::<u64>()
                        .map_err(|_| Error::Invalid("invalid journal sequence".into()))?,
                );
            }
            let record: arte_core::journal::Record =
                serde_json::from_value(value).map_err(|e| Error::Serialization(e.to_string()))?;
            if record.scope_hash != scope_hash || record.sequence < first || record.sequence > last
            {
                return Err(Error::Conflict("journal response escaped cursor".into()));
            }
            record.decode()?;
            records.push(record);
        }
        if records.len() > 2 * arte_core::journal::MAX_BATCH {
            return Err(Error::Capacity(
                "journal conflicts exceed bounded read".into(),
            ));
        }
        Ok(records)
    }
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
        let mut response = self
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
        const MAX_RESPONSE: usize = 32 * 1024 * 1024;
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| Error::Unready("ClickHouse response incomplete".into()))?
        {
            if chunk.len() > MAX_RESPONSE - bytes.len() {
                return Err(Error::Capacity(
                    "ClickHouse response exceeds 32 MiB; use a bounded page".into(),
                ));
            }
            bytes.extend_from_slice(&chunk);
        }
        String::from_utf8(bytes)
            .map_err(|_| Error::Invalid("ClickHouse response is not UTF-8".into()))
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
    async fn immutable_value(
        &self,
        table: &str,
        key_column: &str,
        key: &str,
        value_column: &str,
    ) -> Result<Option<String>> {
        identifier(table)?;
        identifier(key_column)?;
        identifier(value_column)?;
        if key.len() != 64 || !key.bytes().all(|b| b.is_ascii_hexdigit()) {
            return Err(Error::Invalid("invalid immutable SHA-256 key".into()));
        }
        let query=format!("SELECT DISTINCT {value_column} AS value FROM {}.{table} WHERE {key_column}='{key}' LIMIT 2 FORMAT JSONEachRow",self.database);
        let body = self.request(&query, String::new()).await?;
        decode_immutable_rows(&body)
    }
    /// No DDL and no background writer is started. Caller must have installed
    /// the reviewed schema and hold exclusive publication ownership for a seed.
    pub async fn publish_seed(&self, bundle: &Bundle, now_ns: u64) -> Result<()> {
        let seed = bundle.hydrate()?;
        if now_ns < bundle.manifest.available_at_ns {
            return Err(Error::Unready(
                "seed publication precedes availability".into(),
            ));
        }
        for table in ["seed_objects_v1", "seed_manifests_v1"] {
            self.verify_storage(table).await?;
        }
        if let Some(previous) = &seed.previous_seed {
            let start = seed
                .source
                .start_second
                .checked_mul(1_000_000_000)
                .ok_or_else(|| Error::Invalid("seed start overflow".into()))?;
            self.load_seed(previous, seed.source.instrument, seed.source.session, start)
                .await?;
        }
        for object in bundle.objects.values() {
            let payload = std::str::from_utf8(&object.payload)
                .map_err(|_| Error::Invalid("seed object is not UTF-8 JSON".into()))?;
            if let Some(existing) = self
                .immutable_value("seed_objects_v1", "object_hash", &object.id, "payload_json")
                .await?
            {
                if existing.as_bytes() != object.payload {
                    return Err(Error::Conflict("stored seed object differs".into()));
                }
            } else {
                self.insert(
                    "seed_objects_v1",
                    &[serde_json::json!({"object_hash":object.id,"payload_json":payload})],
                )
                .await?;
            }
            let verified = self
                .immutable_value("seed_objects_v1", "object_hash", &object.id, "payload_json")
                .await?
                .ok_or_else(|| Error::Unready("seed object readback missing".into()))?;
            if verified.as_bytes() != object.payload {
                return Err(Error::Conflict("seed object readback mismatch".into()));
            }
        }
        let serialized = serde_json::to_string(&bundle.manifest)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if let Some(existing) = self
            .immutable_value(
                "seed_manifests_v1",
                "seed_hash",
                &bundle.manifest.id,
                "manifest_json",
            )
            .await?
        {
            if existing != serialized {
                return Err(Error::Conflict("seed manifest already differs".into()));
            }
        } else {
            self.insert(
                "seed_manifests_v1",
                &[serde_json::json!({"seed_hash":bundle.manifest.id,"manifest_json":serialized})],
            )
            .await?;
        }
        let verified = self
            .immutable_value(
                "seed_manifests_v1",
                "seed_hash",
                &bundle.manifest.id,
                "manifest_json",
            )
            .await?;
        if verified.as_deref() != Some(serialized.as_str()) {
            return Err(Error::Unready(
                "seed manifest readback missing or different".into(),
            ));
        }
        Ok(())
    }
    pub async fn load_seed(
        &self,
        id: &str,
        instrument: u64,
        session: u32,
        start_ns: u64,
    ) -> Result<HistoricalSeed> {
        let payload = self
            .immutable_value("seed_manifests_v1", "seed_hash", id, "manifest_json")
            .await?
            .ok_or_else(|| Error::Unready("historical seed is not published".into()))?;
        let manifest: SeedManifest =
            serde_json::from_str(&payload).map_err(|e| Error::Serialization(e.to_string()))?;
        if manifest.id != id
            || manifest.instrument != instrument
            || manifest.session >= session
            || manifest.available_at_ns > start_ns
        {
            return Err(Error::Unready(
                "seed identity or availability mismatch".into(),
            ));
        }
        let mut objects = BTreeMap::new();
        for id in &manifest.objects {
            let payload = self
                .immutable_value("seed_objects_v1", "object_hash", id, "payload_json")
                .await?
                .ok_or_else(|| Error::Unready("published seed references missing object".into()))?;
            objects.insert(
                id.clone(),
                Object {
                    id: id.clone(),
                    payload: payload.into_bytes(),
                },
            );
        }
        Bundle { manifest, objects }.hydrate()
    }
}
fn decode_immutable_rows(body: &str) -> Result<Option<String>> {
    let mut value = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        let row: Value = serde_json::from_str(line)
            .map_err(|_| Error::Invalid("invalid immutable readback row".into()))?;
        let current = row
            .get("value")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Invalid("missing immutable value".into()))?;
        if value.as_ref().is_some_and(|previous| previous != current) {
            return Err(Error::Conflict(
                "multiple payloads for immutable identity".into(),
            ));
        }
        value = Some(current.to_owned());
    }
    Ok(value)
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
    #[test]
    fn immutable_readback_accepts_retries_but_not_conflicts() {
        assert_eq!(decode_immutable_rows("").unwrap(), None);
        assert_eq!(
            decode_immutable_rows("{\"value\":\"same\"}\n{\"value\":\"same\"}").unwrap(),
            Some("same".into())
        );
        assert!(decode_immutable_rows("{\"value\":\"first\"}\n{\"value\":\"second\"}").is_err());
        assert!(decode_immutable_rows("{\"missing\":1}").is_err());
    }
}
