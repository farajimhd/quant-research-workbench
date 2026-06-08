use crate::config::SecGatewayConfig;
use crate::model::{SecFilingDocument, SecFilingEvent, SecGatewayMessage};
use chrono::{DateTime, Utc};
use reqwest::Client;
use serde_json::json;
use tokio::sync::mpsc;
use tokio::time::{interval, Duration};

#[derive(Clone)]
pub struct SecClickHouse {
    client: Client,
    config: SecGatewayConfig,
}

impl SecClickHouse {
    pub fn new(config: SecGatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
        }
    }

    pub async fn initialize(&self) -> Result<(), String> {
        self.execute(&format!("CREATE DATABASE IF NOT EXISTS `{}`", self.config.clickhouse_database), false)
            .await?;
        let settings = merge_tree_settings(&self.config.clickhouse_storage_policy);
        self.execute(&event_table_sql(&self.config.event_table, &settings), true).await?;
        self.execute(&document_table_sql(&self.config.document_table, &settings), true).await?;
        Ok(())
    }

    pub async fn run(self, mut receiver: mpsc::Receiver<SecGatewayMessage>) {
        if let Err(error) = self.initialize().await {
            eprintln!("SEC ClickHouse initialization failed: {error}");
        }
        let mut events = Vec::with_capacity(self.config.max_batch);
        let mut documents = Vec::with_capacity(self.config.max_batch);
        let mut flush_interval = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                item = receiver.recv() => {
                    match item {
                        Some(message) => {
                            events.push(message.event);
                            documents.extend(message.documents);
                        }
                        None => {
                            self.flush(&mut events, &mut documents).await;
                            return;
                        }
                    }
                    if events.len() >= self.config.max_batch || documents.len() >= self.config.max_batch {
                        self.flush(&mut events, &mut documents).await;
                    }
                }
                _ = flush_interval.tick() => {
                    self.flush(&mut events, &mut documents).await;
                }
            }
        }
    }

    async fn flush(&self, events: &mut Vec<SecFilingEvent>, documents: &mut Vec<SecFilingDocument>) {
        if !events.is_empty() {
            let batch = std::mem::take(events);
            if let Err(error) = self.insert_events(&batch).await {
                eprintln!("SEC event insert failed: {error}");
            }
        }
        if !documents.is_empty() {
            let batch = std::mem::take(documents);
            if let Err(error) = self.insert_documents(&batch).await {
                eprintln!("SEC document insert failed: {error}");
            }
        }
    }

    async fn insert_events(&self, rows: &[SecFilingEvent]) -> Result<(), String> {
        let body = rows
            .iter()
            .map(|row| {
                json!({
                    "session_date": row.session_date.to_string(),
                    "schema_version": row.schema_version,
                    "provider": &row.provider,
                    "event_type": &row.event_type,
                    "event_id": &row.event_id,
                    "cik": &row.cik,
                    "company_name": &row.company_name,
                    "accession_number": &row.accession_number,
                    "accession_number_compact": &row.accession_number_compact,
                    "form_type": &row.form_type,
                    "filing_date": row.filing_date.as_ref().map(|value| value.to_string()),
                    "accepted_at_utc": row.accepted_at_utc.as_ref().map(format_datetime64_utc),
                    "feed_updated_at_utc": row.feed_updated_at_utc.as_ref().map(format_datetime64_utc),
                    "gateway_seen_at_utc": format_datetime64_utc(&row.gateway_seen_at_utc),
                    "feed_url": &row.feed_url,
                    "detail_url": &row.detail_url,
                    "primary_document": &row.primary_document,
                    "primary_document_url": &row.primary_document_url,
                    "document_count": row.document_count,
                    "parsed_document_count": row.parsed_document_count,
                    "extraction_status": &row.extraction_status,
                    "extraction_error": &row.extraction_error,
                    "artifact_root": &row.artifact_root,
                    "raw_feed_artifact_path": &row.raw_feed_artifact_path,
                    "detail_artifact_path": &row.detail_artifact_path,
                    "raw_feed_json": &row.raw_feed_json,
                })
                .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.insert_json_each_row(&self.config.event_table, body).await
    }

    async fn insert_documents(&self, rows: &[SecFilingDocument]) -> Result<(), String> {
        let body = rows
            .iter()
            .map(|row| {
                json!({
                    "session_date": row.session_date.to_string(),
                    "schema_version": row.schema_version,
                    "event_id": &row.event_id,
                    "cik": &row.cik,
                    "accession_number": &row.accession_number,
                    "sequence": row.sequence,
                    "document_name": &row.document_name,
                    "document_type": &row.document_type,
                    "description": &row.description,
                    "document_url": &row.document_url,
                    "content_type": &row.content_type,
                    "byte_length": row.byte_length,
                    "content_sha256": &row.content_sha256,
                    "artifact_path": &row.artifact_path,
                    "text_hash": &row.text_hash,
                    "extracted_text": &row.extracted_text,
                    "extraction_status": &row.extraction_status,
                    "extraction_error": &row.extraction_error,
                    "downloaded_at_utc": format_datetime64_utc(&row.downloaded_at_utc),
                })
                .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.insert_json_each_row(&self.config.document_table, body).await
    }

    async fn insert_json_each_row(&self, table: &str, body: String) -> Result<(), String> {
        let sql = format!(
            "INSERT INTO `{}`.`{}` FORMAT JSONEachRow",
            self.config.clickhouse_database, table
        );
        self.query_with_body(&sql, body, true).await.map(|_| ())
    }

    async fn execute(&self, sql: &str, database: bool) -> Result<(), String> {
        self.query_with_body(sql, String::new(), database).await.map(|_| ())
    }

    async fn query_with_body(&self, sql: &str, body: String, database: bool) -> Result<String, String> {
        let mut request = self.client.post(&self.config.clickhouse_url).query(&[("query", sql)]);
        if database {
            request = request.query(&[("database", self.config.clickhouse_database.as_str())]);
        }
        request = request.basic_auth(&self.config.clickhouse_user, Some(self.config.clickhouse_password()));
        let response = request
            .body(body)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        let status = response.status();
        let text = response.text().await.map_err(|error| error.to_string())?;
        if status.is_success() {
            Ok(text)
        } else {
            Err(format!("ClickHouse HTTP {status}: {text}"))
        }
    }
}

fn format_datetime64_utc(value: &DateTime<Utc>) -> String {
    value.format("%Y-%m-%d %H:%M:%S.%9f").to_string()
}

fn merge_tree_settings(storage_policy: &str) -> String {
    if storage_policy.trim().is_empty() {
        "index_granularity = 8192".to_string()
    } else {
        format!("storage_policy = '{}', index_granularity = 8192", storage_policy.replace('\'', "''"))
    }
}

fn event_table_sql(table: &str, settings: &str) -> String {
    format!(
        r#"
CREATE TABLE IF NOT EXISTS `{table}`
(
    session_date Date,
    schema_version UInt16,
    provider LowCardinality(String),
    event_type LowCardinality(String),
    event_id String,
    cik String,
    company_name String,
    accession_number String,
    accession_number_compact String,
    form_type LowCardinality(String),
    filing_date Nullable(Date),
    accepted_at_utc Nullable(DateTime64(9, 'UTC')),
    feed_updated_at_utc Nullable(DateTime64(9, 'UTC')),
    gateway_seen_at_utc DateTime64(9, 'UTC'),
    feed_url String,
    detail_url String,
    primary_document String,
    primary_document_url String,
    document_count UInt16,
    parsed_document_count UInt16,
    extraction_status LowCardinality(String),
    extraction_error String,
    artifact_root String,
    raw_feed_artifact_path String,
    detail_artifact_path String,
    raw_feed_json String
)
ENGINE = ReplacingMergeTree(gateway_seen_at_utc)
PARTITION BY session_date
ORDER BY (session_date, provider, accession_number, cik)
SETTINGS {settings}
"#
    )
}

fn document_table_sql(table: &str, settings: &str) -> String {
    format!(
        r#"
CREATE TABLE IF NOT EXISTS `{table}`
(
    session_date Date,
    schema_version UInt16,
    event_id String,
    cik String,
    accession_number String,
    sequence UInt16,
    document_name String,
    document_type LowCardinality(String),
    description String,
    document_url String,
    content_type LowCardinality(String),
    byte_length UInt64,
    content_sha256 String,
    artifact_path String,
    text_hash String,
    extracted_text String,
    extraction_status LowCardinality(String),
    extraction_error String,
    downloaded_at_utc DateTime64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(downloaded_at_utc)
PARTITION BY session_date
ORDER BY (session_date, event_id, sequence, document_name)
SETTINGS {settings}
"#
    )
}
