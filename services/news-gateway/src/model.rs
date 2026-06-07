use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

pub const NEWS_SCHEMA_VERSION: u16 = 1;
pub const NEWS_FILTER_VERSION: u16 = 1;

#[derive(Clone, Debug, Serialize)]
pub struct NewsArticle {
    pub session_date: String,
    pub schema_version: u16,
    pub filter_version: u16,
    pub source: String,
    pub source_endpoint: String,
    pub provider_article_id: String,
    pub canonical_article_id: String,
    pub published_at: DateTime<Utc>,
    pub published_raw: String,
    pub last_updated_at: Option<DateTime<Utc>>,
    pub last_updated_raw: String,
    pub gateway_seen_at: DateTime<Utc>,
    pub provider_ingest_delay_ns: Option<i64>,
    pub title: String,
    pub teaser: String,
    pub body_html: String,
    pub body_text: String,
    pub extracted_text: String,
    pub extraction_status: String,
    pub extraction_error: String,
    pub article_url: String,
    pub url_domain: String,
    pub author: String,
    pub publisher_name: String,
    pub publisher_homepage_url: String,
    pub publisher_logo_url: String,
    pub publisher_favicon_url: String,
    pub publisher_raw: String,
    pub tickers: Vec<String>,
    pub channels: Vec<String>,
    pub tags: Vec<String>,
    pub keywords: Vec<String>,
    pub image_urls: Vec<String>,
    pub has_body: u8,
    pub is_title_only: u8,
    pub has_pdf: u8,
    pub pdf_urls: Vec<String>,
    pub pdf_texts: Vec<String>,
    pub insight_tickers: Vec<String>,
    pub insight_sentiments: Vec<String>,
    pub insight_reasons: Vec<String>,
    pub content_scope: String,
    pub scanner_relevance: String,
    pub model_relevance: String,
    pub content_completeness: String,
    pub quality_outcome: String,
    pub catalyst_labels: Vec<String>,
    pub intelligence_status: String,
    pub intelligence_version: String,
    pub intelligence_model_stack: Vec<String>,
    pub intelligence_processed_at: Option<DateTime<Utc>>,
    pub intelligence_taxonomy_version: String,
    pub intelligence_prompt_version: String,
    pub sentiment_label: String,
    pub sentiment_score: f32,
    pub sentiment_confidence: f32,
    pub event_type: String,
    pub event_subtype: String,
    pub materiality_score: f32,
    pub novelty_score: f32,
    pub urgency_score: f32,
    pub time_horizon: String,
    pub affected_tickers: Vec<String>,
    pub ticker_sentiment_labels: Vec<String>,
    pub ticker_direction_scores: Vec<f32>,
    pub ticker_confidences: Vec<f32>,
    pub intelligence_labels: Vec<String>,
    pub intelligence_rationale: String,
    pub intelligence_raw_json: String,
    pub intelligence_error: String,
    pub reject_reason: String,
    pub content_hash: String,
    pub raw_json: String,
    pub raw_artifact_path: String,
    pub raw_payload_hash: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct NewsArticleSummary {
    pub source: String,
    pub provider_article_id: String,
    pub published_at: DateTime<Utc>,
    pub last_updated_at: Option<DateTime<Utc>>,
    pub gateway_seen_at: DateTime<Utc>,
    pub provider_ingest_delay_ms: Option<i64>,
    pub title: String,
    pub teaser: String,
    pub article_url: String,
    pub publisher_name: String,
    pub tickers: Vec<String>,
    pub channels: Vec<String>,
    pub tags: Vec<String>,
    pub content_scope: String,
    pub scanner_relevance: String,
    pub model_relevance: String,
    pub content_completeness: String,
    pub quality_outcome: String,
    pub catalyst_labels: Vec<String>,
    pub intelligence_status: String,
    pub intelligence_version: String,
    pub sentiment_label: String,
    pub sentiment_score: f32,
    pub sentiment_confidence: f32,
    pub event_type: String,
    pub materiality_score: f32,
    pub urgency_score: f32,
    pub time_horizon: String,
    pub affected_tickers: Vec<String>,
    pub intelligence_labels: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct NewsSnapshot {
    pub as_of: DateTime<Utc>,
    pub row_count: usize,
    pub rows: Vec<NewsArticleSummary>,
    pub total_articles: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct TickerNewsSnapshot {
    pub as_of: DateTime<Utc>,
    pub news_count_5m: usize,
    pub news_count_30m: usize,
    pub news_count_session: usize,
    pub rows: Vec<NewsArticleSummary>,
    pub ticker: String,
}

#[derive(Clone, Debug)]
pub struct Classification {
    pub catalyst_labels: Vec<String>,
    pub content_completeness: String,
    pub content_scope: String,
    pub model_relevance: String,
    pub quality_outcome: String,
    pub scanner_relevance: String,
}

#[derive(Clone, Debug)]
pub struct NormalizedNewsInput {
    pub article_url: String,
    pub author: String,
    pub body_html: String,
    pub channels: Vec<String>,
    pub image_urls: Vec<String>,
    pub insight_reasons: Vec<String>,
    pub insight_sentiments: Vec<String>,
    pub insight_tickers: Vec<String>,
    pub keywords: Vec<String>,
    pub last_updated_at: Option<DateTime<Utc>>,
    pub last_updated_raw: String,
    pub provider_article_id: String,
    pub published_at: DateTime<Utc>,
    pub published_raw: String,
    pub publisher_favicon_url: String,
    pub publisher_homepage_url: String,
    pub publisher_logo_url: String,
    pub publisher_name: String,
    pub publisher_raw: String,
    pub raw_json: String,
    pub raw_artifact_path: String,
    pub raw_payload_hash: String,
    pub source: String,
    pub source_endpoint: String,
    pub tags: Vec<String>,
    pub teaser: String,
    pub tickers: Vec<String>,
    pub title: String,
}

impl NewsArticle {
    pub fn summary(&self) -> NewsArticleSummary {
        NewsArticleSummary {
            source: self.source.clone(),
            provider_article_id: self.provider_article_id.clone(),
            published_at: self.published_at.clone(),
            last_updated_at: self.last_updated_at.clone(),
            gateway_seen_at: self.gateway_seen_at.clone(),
            provider_ingest_delay_ms: self.provider_ingest_delay_ns.map(|value| value / 1_000_000),
            title: self.title.clone(),
            teaser: self.teaser.clone(),
            article_url: self.article_url.clone(),
            publisher_name: self.publisher_name.clone(),
            tickers: self.tickers.clone(),
            channels: self.channels.clone(),
            tags: self.tags.clone(),
            content_scope: self.content_scope.clone(),
            scanner_relevance: self.scanner_relevance.clone(),
            model_relevance: self.model_relevance.clone(),
            content_completeness: self.content_completeness.clone(),
            quality_outcome: self.quality_outcome.clone(),
            catalyst_labels: self.catalyst_labels.clone(),
            intelligence_status: self.intelligence_status.clone(),
            intelligence_version: self.intelligence_version.clone(),
            sentiment_label: self.sentiment_label.clone(),
            sentiment_score: self.sentiment_score,
            sentiment_confidence: self.sentiment_confidence,
            event_type: self.event_type.clone(),
            materiality_score: self.materiality_score,
            urgency_score: self.urgency_score,
            time_horizon: self.time_horizon.clone(),
            affected_tickers: self.affected_tickers.clone(),
            intelligence_labels: self.intelligence_labels.clone(),
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct IntelligenceTickerImpact {
    pub ticker: String,
    pub sentiment_label: String,
    pub direction_score: f32,
    pub confidence: f32,
    pub rationale: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct IntelligenceResponse {
    pub status: String,
    pub processed_at: String,
    pub stack_version: String,
    pub taxonomy_version: String,
    pub prompt_version: String,
    pub model_stack: Vec<String>,
    pub sentiment_label: String,
    pub sentiment_score: f32,
    pub sentiment_confidence: f32,
    pub event_type: String,
    pub event_subtype: String,
    pub materiality_score: f32,
    pub novelty_score: f32,
    pub urgency_score: f32,
    pub time_horizon: String,
    pub affected_tickers: Vec<IntelligenceTickerImpact>,
    pub labels: Vec<String>,
    pub rationale: String,
    pub raw_outputs: Value,
    pub error: String,
}

impl NewsArticle {
    pub fn mark_intelligence_disabled(&mut self) {
        self.intelligence_status = "disabled".to_string();
    }

    pub fn mark_intelligence_failed(&mut self, error: String) {
        self.intelligence_status = "failed".to_string();
        self.intelligence_error = error;
    }

    pub fn apply_intelligence(&mut self, response: IntelligenceResponse) {
        self.intelligence_status = response.status;
        self.intelligence_version = response.stack_version;
        self.intelligence_model_stack = response.model_stack;
        self.intelligence_processed_at = parse_dt_opt(&response.processed_at);
        self.intelligence_taxonomy_version = response.taxonomy_version;
        self.intelligence_prompt_version = response.prompt_version;
        self.sentiment_label = response.sentiment_label;
        self.sentiment_score = response.sentiment_score;
        self.sentiment_confidence = response.sentiment_confidence;
        self.event_type = response.event_type;
        self.event_subtype = response.event_subtype;
        self.materiality_score = response.materiality_score;
        self.novelty_score = response.novelty_score;
        self.urgency_score = response.urgency_score;
        self.time_horizon = response.time_horizon;
        self.affected_tickers = response.affected_tickers.iter().map(|item| item.ticker.clone()).collect();
        self.ticker_sentiment_labels = response
            .affected_tickers
            .iter()
            .map(|item| item.sentiment_label.clone())
            .collect();
        self.ticker_direction_scores = response.affected_tickers.iter().map(|item| item.direction_score).collect();
        self.ticker_confidences = response.affected_tickers.iter().map(|item| item.confidence).collect();
        self.intelligence_labels = response.labels;
        self.intelligence_rationale = response.rationale;
        self.intelligence_raw_json = response.raw_outputs.to_string();
        self.intelligence_error = response.error;
    }
}

pub fn parse_benzinga(value: &Value) -> Result<NormalizedNewsInput, String> {
    let published_raw = string_field(value, "published");
    let published_at = parse_dt(&published_raw)?;
    let last_updated_raw = string_field(value, "last_updated");
    Ok(NormalizedNewsInput {
        article_url: string_field(value, "url"),
        author: string_field(value, "author"),
        body_html: string_field(value, "body"),
        channels: string_array(value.get("channels")),
        image_urls: string_array(value.get("images")),
        insight_reasons: Vec::new(),
        insight_sentiments: Vec::new(),
        insight_tickers: Vec::new(),
        keywords: Vec::new(),
        last_updated_at: parse_dt_opt(&last_updated_raw),
        last_updated_raw,
        provider_article_id: value
            .get("benzinga_id")
            .map(value_to_id)
            .filter(|item| !item.is_empty())
            .ok_or_else(|| "missing benzinga_id".to_string())?,
        published_at,
        published_raw,
        publisher_favicon_url: String::new(),
        publisher_homepage_url: "https://www.benzinga.com".to_string(),
        publisher_logo_url: String::new(),
        publisher_name: "Benzinga".to_string(),
        publisher_raw: "{}".to_string(),
        raw_json: value.to_string(),
        raw_artifact_path: String::new(),
        raw_payload_hash: String::new(),
        source: "benzinga".to_string(),
        source_endpoint: "/benzinga/v2/news".to_string(),
        tags: string_array(value.get("tags")),
        teaser: string_field(value, "teaser"),
        tickers: normalize_tickers(string_array(value.get("tickers"))),
        title: string_field(value, "title"),
    })
}

pub fn normalize_tickers(values: Vec<String>) -> Vec<String> {
    let mut seen = BTreeSet::new();
    for value in values {
        let ticker = value.trim().to_ascii_uppercase();
        if !ticker.is_empty() {
            seen.insert(ticker);
        }
    }
    seen.into_iter().collect()
}

pub fn parse_dt(text: &str) -> Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(text)
        .map(|value| value.with_timezone(&Utc))
        .map_err(|error| format!("invalid datetime {text:?}: {error}"))
}

pub fn parse_dt_opt(text: &str) -> Option<DateTime<Utc>> {
    if text.trim().is_empty() {
        None
    } else {
        parse_dt(text).ok()
    }
}

fn string_field(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string()
}

fn string_array(value: Option<&Value>) -> Vec<String> {
    match value {
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(Value::as_str)
            .map(|item| item.trim().to_string())
            .filter(|item| !item.is_empty())
            .collect(),
        Some(Value::String(item)) if !item.trim().is_empty() => vec![item.trim().to_string()],
        _ => Vec::new(),
    }
}

fn value_to_id(value: &Value) -> String {
    match value {
        Value::String(item) => item.trim().to_string(),
        Value::Number(item) => item.to_string(),
        _ => String::new(),
    }
}

#[derive(Debug, Deserialize)]
pub struct PollResponse {
    pub next_url: Option<String>,
    pub results: Option<Vec<Value>>,
}
