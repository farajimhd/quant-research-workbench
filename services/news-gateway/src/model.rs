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
    pub reject_reason: String,
    pub content_hash: String,
    pub raw_json: String,
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
        }
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
        source: "massive_benzinga".to_string(),
        source_endpoint: "/benzinga/v2/news".to_string(),
        tags: string_array(value.get("tags")),
        teaser: string_field(value, "teaser"),
        tickers: normalize_tickers(string_array(value.get("tickers"))),
        title: string_field(value, "title"),
    })
}

pub fn parse_general(value: &Value) -> Result<NormalizedNewsInput, String> {
    let published_raw = string_field(value, "published_utc");
    let published_at = parse_dt(&published_raw)?;
    let publisher = value.get("publisher").cloned().unwrap_or(Value::Null);
    let insights = value.get("insights").and_then(Value::as_array).cloned().unwrap_or_default();
    Ok(NormalizedNewsInput {
        article_url: string_field(value, "article_url"),
        author: string_field(value, "author"),
        body_html: String::new(),
        channels: Vec::new(),
        image_urls: optional_string_field(value, "image_url").into_iter().collect(),
        insight_reasons: insights.iter().map(|item| string_field(item, "sentiment_reasoning")).collect(),
        insight_sentiments: insights.iter().map(|item| string_field(item, "sentiment")).collect(),
        insight_tickers: normalize_tickers(insights.iter().map(|item| string_field(item, "ticker")).collect()),
        keywords: string_array(value.get("keywords")),
        last_updated_at: None,
        last_updated_raw: String::new(),
        provider_article_id: value
            .get("id")
            .map(value_to_id)
            .filter(|item| !item.is_empty())
            .ok_or_else(|| "missing id".to_string())?,
        published_at,
        published_raw,
        publisher_favicon_url: string_field(&publisher, "favicon_url"),
        publisher_homepage_url: string_field(&publisher, "homepage_url"),
        publisher_logo_url: string_field(&publisher, "logo_url"),
        publisher_name: string_field(&publisher, "name"),
        publisher_raw: publisher.to_string(),
        raw_json: value.to_string(),
        source: "massive_general".to_string(),
        source_endpoint: "/v2/reference/news".to_string(),
        tags: Vec::new(),
        teaser: string_field(value, "description"),
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

fn optional_string_field(value: &Value, key: &str) -> Option<String> {
    let text = string_field(value, key);
    if text.is_empty() {
        None
    } else {
        Some(text)
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
