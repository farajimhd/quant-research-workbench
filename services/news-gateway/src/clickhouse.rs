use crate::config::NewsGatewayConfig;
use crate::model::NewsArticle;
use reqwest::Client;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio::time::{interval, Duration};

#[derive(Clone)]
pub struct NewsClickHouse {
    client: Client,
    config: NewsGatewayConfig,
}

impl NewsClickHouse {
    pub fn new(config: NewsGatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
        }
    }

    pub async fn initialize(&self) -> Result<(), String> {
        self.execute(&format!("CREATE DATABASE IF NOT EXISTS `{}`", self.config.clickhouse_database), false)
            .await?;
        self.execute(
            r#"
            CREATE TABLE IF NOT EXISTS live_news_articles
            (
                session_date Date,
                schema_version UInt16,
                filter_version UInt16,
                source LowCardinality(String),
                source_endpoint LowCardinality(String),
                provider_article_id String,
                canonical_article_id String,
                published_at DateTime64(9, 'UTC'),
                published_raw String,
                last_updated_at Nullable(DateTime64(9, 'UTC')),
                last_updated_raw String,
                gateway_seen_at DateTime64(9, 'UTC'),
                provider_ingest_delay_ns Nullable(Int64),
                title String,
                teaser String,
                body_html String,
                body_text String,
                extracted_text String,
                extraction_status LowCardinality(String),
                extraction_error String,
                article_url String,
                url_domain LowCardinality(String),
                author String,
                publisher_name LowCardinality(String),
                publisher_homepage_url String,
                publisher_logo_url String,
                publisher_favicon_url String,
                publisher_raw String,
                tickers Array(String),
                channels Array(String),
                tags Array(String),
                keywords Array(String),
                image_urls Array(String),
                has_body UInt8,
                is_title_only UInt8,
                has_pdf UInt8,
                pdf_urls Array(String),
                pdf_texts Array(String),
                insight_tickers Array(String),
                insight_sentiments Array(String),
                insight_reasons Array(String),
                content_scope LowCardinality(String),
                scanner_relevance LowCardinality(String),
                model_relevance LowCardinality(String),
                content_completeness LowCardinality(String),
                quality_outcome LowCardinality(String),
                catalyst_labels Array(String),
                intelligence_status LowCardinality(String),
                intelligence_version String,
                intelligence_model_stack Array(String),
                intelligence_processed_at Nullable(DateTime64(9, 'UTC')),
                intelligence_taxonomy_version String,
                intelligence_prompt_version String,
                sentiment_label LowCardinality(String),
                sentiment_score Float32,
                sentiment_confidence Float32,
                event_type LowCardinality(String),
                event_subtype LowCardinality(String),
                materiality_score Float32,
                novelty_score Float32,
                urgency_score Float32,
                time_horizon LowCardinality(String),
                affected_tickers Array(String),
                ticker_sentiment_labels Array(String),
                ticker_direction_scores Array(Float32),
                ticker_confidences Array(Float32),
                intelligence_labels Array(String),
                intelligence_rationale String,
                intelligence_raw_json String,
                intelligence_error String,
                reject_reason String,
                content_hash String,
                raw_json String
            )
            ENGINE = ReplacingMergeTree(gateway_seen_at)
            PARTITION BY session_date
            ORDER BY (session_date, source, provider_article_id)
            "#,
            true,
        )
        .await?;
        for statement in intelligence_column_migrations() {
            self.execute(statement, true).await?;
        }
        Ok(())
    }

    pub async fn run(self, mut receiver: mpsc::Receiver<NewsArticle>) {
        if let Err(error) = self.initialize().await {
            eprintln!("News ClickHouse initialization failed: {error}");
        }
        let mut batch = Vec::with_capacity(self.config.max_batch);
        let mut flush_interval = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                item = receiver.recv() => {
                    match item {
                        Some(article) => batch.push(article),
                        None => {
                            self.flush(&mut batch).await;
                            return;
                        }
                    }
                    if batch.len() >= self.config.max_batch {
                        self.flush(&mut batch).await;
                    }
                }
                _ = flush_interval.tick() => {
                    self.flush(&mut batch).await;
                }
            }
        }
    }

    pub async fn latest_published_at(&self, source: &str) -> Result<Option<String>, String> {
        self.initialize().await?;
        let sql = format!(
            "SELECT max(published_at) AS ts FROM live_news_articles WHERE source = '{}' FORMAT JSONEachRow",
            source.replace('\'', "''")
        );
        let text = self.query(&sql, true).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
        let text = value.get("ts").and_then(Value::as_str).unwrap_or_default();
        if text.is_empty() || text.starts_with("1970-") {
            Ok(None)
        } else {
            Ok(Some(text.to_string()))
        }
    }

    async fn flush(&self, rows: &mut Vec<NewsArticle>) {
        if rows.is_empty() {
            return;
        }
        let rows = std::mem::take(rows);
        if let Err(error) = self.insert_articles(&rows).await {
            eprintln!("News ClickHouse insert failed: {error}");
        }
    }

    async fn insert_articles(&self, rows: &[NewsArticle]) -> Result<(), String> {
        let body = rows
            .iter()
            .map(|row| {
                json!({
                    "session_date": &row.session_date,
                    "schema_version": row.schema_version,
                    "filter_version": row.filter_version,
                    "source": &row.source,
                    "source_endpoint": &row.source_endpoint,
                    "provider_article_id": &row.provider_article_id,
                    "canonical_article_id": &row.canonical_article_id,
                    "published_at": row.published_at.to_rfc3339(),
                    "published_raw": &row.published_raw,
                    "last_updated_at": row.last_updated_at.as_ref().map(|value| value.to_rfc3339()),
                    "last_updated_raw": &row.last_updated_raw,
                    "gateway_seen_at": row.gateway_seen_at.to_rfc3339(),
                    "provider_ingest_delay_ns": row.provider_ingest_delay_ns,
                    "title": &row.title,
                    "teaser": &row.teaser,
                    "body_html": &row.body_html,
                    "body_text": &row.body_text,
                    "extracted_text": &row.extracted_text,
                    "extraction_status": &row.extraction_status,
                    "extraction_error": &row.extraction_error,
                    "article_url": &row.article_url,
                    "url_domain": &row.url_domain,
                    "author": &row.author,
                    "publisher_name": &row.publisher_name,
                    "publisher_homepage_url": &row.publisher_homepage_url,
                    "publisher_logo_url": &row.publisher_logo_url,
                    "publisher_favicon_url": &row.publisher_favicon_url,
                    "publisher_raw": &row.publisher_raw,
                    "tickers": &row.tickers,
                    "channels": &row.channels,
                    "tags": &row.tags,
                    "keywords": &row.keywords,
                    "image_urls": &row.image_urls,
                    "has_body": row.has_body,
                    "is_title_only": row.is_title_only,
                    "has_pdf": row.has_pdf,
                    "pdf_urls": &row.pdf_urls,
                    "pdf_texts": &row.pdf_texts,
                    "insight_tickers": &row.insight_tickers,
                    "insight_sentiments": &row.insight_sentiments,
                    "insight_reasons": &row.insight_reasons,
                    "content_scope": &row.content_scope,
                    "scanner_relevance": &row.scanner_relevance,
                    "model_relevance": &row.model_relevance,
                    "content_completeness": &row.content_completeness,
                    "quality_outcome": &row.quality_outcome,
                    "catalyst_labels": &row.catalyst_labels,
                    "intelligence_status": &row.intelligence_status,
                    "intelligence_version": &row.intelligence_version,
                    "intelligence_model_stack": &row.intelligence_model_stack,
                    "intelligence_processed_at": row.intelligence_processed_at.as_ref().map(|value| value.to_rfc3339()),
                    "intelligence_taxonomy_version": &row.intelligence_taxonomy_version,
                    "intelligence_prompt_version": &row.intelligence_prompt_version,
                    "sentiment_label": &row.sentiment_label,
                    "sentiment_score": row.sentiment_score,
                    "sentiment_confidence": row.sentiment_confidence,
                    "event_type": &row.event_type,
                    "event_subtype": &row.event_subtype,
                    "materiality_score": row.materiality_score,
                    "novelty_score": row.novelty_score,
                    "urgency_score": row.urgency_score,
                    "time_horizon": &row.time_horizon,
                    "affected_tickers": &row.affected_tickers,
                    "ticker_sentiment_labels": &row.ticker_sentiment_labels,
                    "ticker_direction_scores": &row.ticker_direction_scores,
                    "ticker_confidences": &row.ticker_confidences,
                    "intelligence_labels": &row.intelligence_labels,
                    "intelligence_rationale": &row.intelligence_rationale,
                    "intelligence_raw_json": &row.intelligence_raw_json,
                    "intelligence_error": &row.intelligence_error,
                    "reject_reason": &row.reject_reason,
                    "content_hash": &row.content_hash,
                    "raw_json": &row.raw_json,
                }).to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query(&format!("INSERT INTO live_news_articles FORMAT JSONEachRow\n{body}"), true)
            .await
            .map(|_| ())
    }

    async fn execute(&self, sql: &str, use_database: bool) -> Result<(), String> {
        self.query(sql, use_database).await.map(|_| ())
    }

    async fn query(&self, body: &str, use_database: bool) -> Result<String, String> {
        let url = if use_database {
            format!(
                "{}/?database={}",
                self.config.clickhouse_url,
                urlencoding::encode(&self.config.clickhouse_database)
            )
        } else {
            format!("{}/", self.config.clickhouse_url)
        };
        let mut request = self
            .client
            .post(url)
            .header("Content-Type", "text/plain; charset=utf-8")
            .header("X-ClickHouse-User", &self.config.clickhouse_user)
            .body(body.to_string());
        let password = self.config.clickhouse_password();
        if !password.is_empty() {
            request = request.header("X-ClickHouse-Key", password);
        }
        let response = request.send().await.map_err(|error| error.to_string())?;
        let status = response.status();
        let text = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("ClickHouse HTTP {status}: {text}"));
        }
        Ok(text)
    }
}

fn intelligence_column_migrations() -> Vec<&'static str> {
    vec![
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_status LowCardinality(String) AFTER catalyst_labels",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_version String AFTER intelligence_status",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_model_stack Array(String) AFTER intelligence_version",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_processed_at Nullable(DateTime64(9, 'UTC')) AFTER intelligence_model_stack",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_taxonomy_version String AFTER intelligence_processed_at",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_prompt_version String AFTER intelligence_taxonomy_version",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS sentiment_label LowCardinality(String) AFTER intelligence_prompt_version",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS sentiment_score Float32 AFTER sentiment_label",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS sentiment_confidence Float32 AFTER sentiment_score",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS event_type LowCardinality(String) AFTER sentiment_confidence",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS event_subtype LowCardinality(String) AFTER event_type",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS materiality_score Float32 AFTER event_subtype",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS novelty_score Float32 AFTER materiality_score",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS urgency_score Float32 AFTER novelty_score",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS time_horizon LowCardinality(String) AFTER urgency_score",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS affected_tickers Array(String) AFTER time_horizon",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS ticker_sentiment_labels Array(String) AFTER affected_tickers",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS ticker_direction_scores Array(Float32) AFTER ticker_sentiment_labels",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS ticker_confidences Array(Float32) AFTER ticker_direction_scores",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_labels Array(String) AFTER ticker_confidences",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_rationale String AFTER intelligence_labels",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_raw_json String AFTER intelligence_rationale",
        "ALTER TABLE live_news_articles ADD COLUMN IF NOT EXISTS intelligence_error String AFTER intelligence_raw_json",
    ]
}
