use crate::classify::{classify_news, filter_version};
use crate::clickhouse::NewsClickHouse;
use crate::config::NewsGatewayConfig;
use crate::extract::{extract_content, stable_hash, url_domain};
use crate::intelligence::NewsIntelligenceClient;
use crate::metrics::SharedMetrics;
use crate::model::{parse_benzinga, parse_dt_opt, NewsArticle, NormalizedNewsInput, PollResponse, NEWS_SCHEMA_VERSION};
use crate::state::SharedNewsState;
use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;
use chrono::{DateTime, Datelike, Duration as ChronoDuration, Utc};
use reqwest::Client;
use serde_json::Value;
use std::path::PathBuf;
use tokio::sync::{broadcast, mpsc};
use tokio::time::{interval, Duration};

pub async fn run_news_pollers(
    config: NewsGatewayConfig,
    state: SharedNewsState,
    writer_sender: mpsc::Sender<NewsArticle>,
    article_sender: broadcast::Sender<crate::model::NewsArticleSummary>,
    metrics: SharedMetrics,
) {
    if config.massive_api_key.is_empty() {
        eprintln!("News gateway running without MASSIVE_API_KEY; polling disabled.");
        return;
    }
    if config.benzinga_enabled {
        tokio::spawn(run_source_poller(
            SourceSpec::benzinga(config.clone()),
            config,
            state,
            writer_sender,
            article_sender,
            metrics,
        ));
    }
}

#[derive(Clone)]
struct SourceSpec {
    interval_ms: u64,
    published_filter: &'static str,
    sort_params: Vec<(&'static str, &'static str)>,
    source: &'static str,
    url: String,
}

impl SourceSpec {
    fn benzinga(config: NewsGatewayConfig) -> Self {
        Self {
            interval_ms: config.benzinga_poll_interval_ms,
            published_filter: "published.gte",
            sort_params: vec![("sort", "published.asc")],
            source: "benzinga",
            url: config.benzinga_url,
        }
    }
}

async fn run_source_poller(
    spec: SourceSpec,
    config: NewsGatewayConfig,
    state: SharedNewsState,
    writer_sender: mpsc::Sender<NewsArticle>,
    article_sender: broadcast::Sender<crate::model::NewsArticleSummary>,
    metrics: SharedMetrics,
) {
    let client = Client::new();
    let intelligence = NewsIntelligenceClient::new(config.clone());
    let clickhouse = NewsClickHouse::new(config.clone());
    let mut cursor = initial_cursor(&clickhouse, &config, spec.source).await;
    let mut timer = interval(Duration::from_millis(spec.interval_ms));
    loop {
        timer.tick().await;
        metrics.inc_poll_run();
        match poll_once(
            &client,
            &intelligence,
            &spec,
            &config,
            cursor,
            state.clone(),
            writer_sender.clone(),
            article_sender.clone(),
            metrics.clone(),
        )
        .await
        {
            Ok(Some(next_cursor)) => cursor = next_cursor,
            Ok(None) => {}
            Err(error) => {
                metrics.inc_poll_failure(spec.source);
                eprintln!("News poll failed for {}: {error}", spec.source);
            }
        }
    }
}

async fn initial_cursor(clickhouse: &NewsClickHouse, config: &NewsGatewayConfig, source: &str) -> DateTime<Utc> {
    if let Ok(Some(text)) = clickhouse.latest_published_at(source).await {
        if let Some(value) = parse_dt_opt(&text) {
            return value - ChronoDuration::seconds(config.poll_overlap_seconds);
        }
    }
    Utc::now() - ChronoDuration::minutes(config.live_lookback_minutes)
}

async fn poll_once(
    client: &Client,
    intelligence: &NewsIntelligenceClient,
    spec: &SourceSpec,
    config: &NewsGatewayConfig,
    cursor: DateTime<Utc>,
    state: SharedNewsState,
    writer_sender: mpsc::Sender<NewsArticle>,
    article_sender: broadcast::Sender<crate::model::NewsArticleSummary>,
    metrics: SharedMetrics,
) -> Result<Option<DateTime<Utc>>, String> {
    let mut next_url = Some(build_url(spec, config, cursor));
    let mut pages = 0usize;
    let mut max_published = None::<DateTime<Utc>>;
    while let Some(url) = next_url.take() {
        if pages >= config.max_pages_per_poll {
            break;
        }
        pages += 1;
        let payload = client.get(url).send().await.map_err(|error| error.to_string())?;
        let status = payload.status();
        let text = payload.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("Massive HTTP {status}: {text}"));
        }
        let response: PollResponse = serde_json::from_str(&text).map_err(|error| error.to_string())?;
        let results = response.results.unwrap_or_default();
        metrics.inc_provider_rows(results.len() as u64);
        for item in results {
            match normalize_article(client, config, spec.source, item).await {
                Ok(mut article) => {
                    intelligence.enrich(&mut article).await;
                    let article_published_at = article.published_at.clone();
                    max_published = Some(
                        max_published
                            .map(|current| current.max(article_published_at.clone()))
                            .unwrap_or(article_published_at.clone()),
                    );
                    metrics.observe_article(article_published_at);
                    let summary = article.summary();
                    if state.apply(summary.clone()).await {
                        metrics.inc_duplicate();
                    }
                    if writer_sender.try_send(article).is_err() {
                        metrics.inc_writer_drop();
                    } else {
                        metrics.inc_persist_queued();
                    }
                    let _ = article_sender.send(summary);
                }
                Err(error) => {
                    metrics.inc_malformed();
                    eprintln!("News row skipped for {}: {error}", spec.source);
                }
            }
        }
        next_url = response.next_url.map(|url| append_api_key(&url, &config.massive_api_key));
    }
    Ok(max_published.map(|value| value - ChronoDuration::seconds(config.poll_overlap_seconds)))
}

async fn normalize_article(
    client: &Client,
    config: &NewsGatewayConfig,
    _source: &str,
    value: Value,
) -> Result<NewsArticle, String> {
    let mut input = parse_benzinga(&value)?;
    match save_raw_payload(config, &input).await {
        Ok((path, hash)) => {
            input.raw_artifact_path = path;
            input.raw_payload_hash = hash;
        }
        Err(error) => {
            eprintln!("Failed to save raw Benzinga payload {}: {error}", input.provider_article_id);
        }
    }
    build_article(client, config, input).await
}

async fn build_article(
    client: &Client,
    config: &NewsGatewayConfig,
    input: NormalizedNewsInput,
) -> Result<NewsArticle, String> {
    if input.provider_article_id.trim().is_empty() || input.title.trim().is_empty() {
        return Err("missing stable id or title".to_string());
    }
    let gateway_seen_at = Utc::now();
    let extraction = extract_content(client, config, &input.body_html, &input.article_url).await;
    let body_text = if extraction.body_text.is_empty() && !input.teaser.trim().is_empty() {
        input.teaser.clone()
    } else {
        extraction.body_text.clone()
    };
    let extracted_text = if extraction.extracted_text.is_empty() {
        body_text.clone()
    } else {
        extraction.extracted_text.clone()
    };
    let has_pdf = !extraction.pdf_urls.is_empty();
    let classification = classify_news(
        &input.title,
        &body_text,
        &extracted_text,
        &input.tickers,
        &input.channels,
        &input.tags,
        &input.keywords,
        &input.insight_sentiments,
        has_pdf,
        extraction.url_enriched,
    );
    let canonical_article_id = stable_hash(&[
        &input.source,
        &input.provider_article_id,
        &input.article_url,
        &input.title,
        &input.published_raw,
    ]);
    let content_hash = stable_hash(&[&input.title, &body_text, &extracted_text, &input.article_url]);
    let provider_ingest_delay_ns = Some(to_unix_ns(&gateway_seen_at) - to_unix_ns(&input.published_at));
    Ok(NewsArticle {
        session_date: input.published_at.date_naive().to_string(),
        schema_version: NEWS_SCHEMA_VERSION,
        filter_version: filter_version(),
        source: input.source,
        source_endpoint: input.source_endpoint,
        provider_article_id: input.provider_article_id,
        canonical_article_id,
        published_at: input.published_at,
        published_raw: input.published_raw,
        last_updated_at: input.last_updated_at,
        last_updated_raw: input.last_updated_raw,
        gateway_seen_at,
        provider_ingest_delay_ns,
        title: input.title,
        teaser: input.teaser,
        body_html: input.body_html,
        body_text: body_text.clone(),
        extracted_text,
        extraction_status: extraction.extraction_status,
        extraction_error: extraction.extraction_error,
        article_url: input.article_url.clone(),
        url_domain: url_domain(&input.article_url),
        author: input.author,
        publisher_name: input.publisher_name,
        publisher_homepage_url: input.publisher_homepage_url,
        publisher_logo_url: input.publisher_logo_url,
        publisher_favicon_url: input.publisher_favicon_url,
        publisher_raw: input.publisher_raw,
        tickers: input.tickers,
        channels: input.channels,
        tags: input.tags,
        keywords: input.keywords,
        image_urls: input.image_urls,
        has_body: if !body_text.trim().is_empty() { 1 } else { 0 },
        is_title_only: if body_text.trim().is_empty() { 1 } else { 0 },
        has_pdf: if has_pdf { 1 } else { 0 },
        pdf_urls: extraction.pdf_urls,
        pdf_texts: extraction.pdf_texts,
        insight_tickers: input.insight_tickers,
        insight_sentiments: input.insight_sentiments,
        insight_reasons: input.insight_reasons,
        content_scope: classification.content_scope,
        scanner_relevance: classification.scanner_relevance,
        model_relevance: classification.model_relevance,
        content_completeness: classification.content_completeness,
        quality_outcome: classification.quality_outcome,
        catalyst_labels: classification.catalyst_labels,
        intelligence_status: "pending".to_string(),
        intelligence_version: String::new(),
        intelligence_model_stack: Vec::new(),
        intelligence_processed_at: None,
        intelligence_taxonomy_version: String::new(),
        intelligence_prompt_version: String::new(),
        sentiment_label: String::new(),
        sentiment_score: 0.0,
        sentiment_confidence: 0.0,
        event_type: String::new(),
        event_subtype: String::new(),
        materiality_score: 0.0,
        novelty_score: 0.0,
        urgency_score: 0.0,
        time_horizon: String::new(),
        affected_tickers: Vec::new(),
        ticker_sentiment_labels: Vec::new(),
        ticker_direction_scores: Vec::new(),
        ticker_confidences: Vec::new(),
        intelligence_labels: Vec::new(),
        intelligence_rationale: String::new(),
        intelligence_raw_json: String::new(),
        intelligence_error: String::new(),
        reject_reason: String::new(),
        content_hash,
        raw_json: input.raw_json,
        raw_artifact_path: input.raw_artifact_path,
        raw_payload_hash: input.raw_payload_hash,
    })
}

async fn save_raw_payload(config: &NewsGatewayConfig, input: &NormalizedNewsInput) -> Result<(String, String), String> {
    let mut path = PathBuf::from(&config.benzinga_artifact_root_win);
    path.push("raw");
    path.push(format!("{:04}", input.published_at.year()));
    path.push(format!("{:02}", input.published_at.month()));
    path.push(format!("{:02}", input.published_at.day()));
    tokio::fs::create_dir_all(&path).await.map_err(|error| error.to_string())?;
    path.push(format!("benzinga_{}.json", safe_filename(&input.provider_article_id)));
    tokio::fs::write(&path, &input.raw_json).await.map_err(|error| error.to_string())?;
    Ok((path.to_string_lossy().to_string(), blake2b_128_hex(&input.raw_json)))
}

fn safe_filename(value: &str) -> String {
    let mut output = String::new();
    for ch in value.chars().take(120) {
        if ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.') {
            output.push(ch);
        } else {
            output.push('_');
        }
    }
    if output.trim_matches(['_', '.', '-']).is_empty() {
        "artifact".to_string()
    } else {
        output
    }
}

fn blake2b_128_hex(value: &str) -> String {
    let mut hasher = Blake2bVar::new(16).expect("valid blake2b output size");
    hasher.update(value.as_bytes());
    let mut output = [0_u8; 16];
    hasher
        .finalize_variable(&mut output)
        .expect("blake2b output buffer has valid size");
    output.iter().map(|byte| format!("{byte:02x}")).collect::<String>()
}

fn build_url(spec: &SourceSpec, config: &NewsGatewayConfig, cursor: DateTime<Utc>) -> String {
    let mut params = vec![
        (spec.published_filter.to_string(), cursor.to_rfc3339()),
        ("limit".to_string(), config.poll_limit.to_string()),
        ("apiKey".to_string(), config.massive_api_key.clone()),
    ];
    for (key, value) in &spec.sort_params {
        params.push(((*key).to_string(), (*value).to_string()));
    }
    let query = params
        .iter()
        .map(|(key, value)| format!("{}={}", urlencoding::encode(key), urlencoding::encode(value)))
        .collect::<Vec<_>>()
        .join("&");
    format!("{}?{}", spec.url, query)
}

fn append_api_key(url: &str, api_key: &str) -> String {
    if url.contains("apiKey=") {
        url.to_string()
    } else if url.contains('?') {
        format!("{url}&apiKey={}", urlencoding::encode(api_key))
    } else {
        format!("{url}?apiKey={}", urlencoding::encode(api_key))
    }
}

fn to_unix_ns(value: &DateTime<Utc>) -> i64 {
    value.timestamp()
        .saturating_mul(1_000_000_000)
        .saturating_add(i64::from(value.timestamp_subsec_nanos()))
}
