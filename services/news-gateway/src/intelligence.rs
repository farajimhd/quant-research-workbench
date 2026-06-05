use crate::config::NewsGatewayConfig;
use crate::model::{IntelligenceResponse, NewsArticle};
use reqwest::Client;
use serde_json::json;
use tokio::time::{timeout, Duration};

#[derive(Clone)]
pub struct NewsIntelligenceClient {
    client: Client,
    config: NewsGatewayConfig,
}

impl NewsIntelligenceClient {
    pub fn new(config: NewsGatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
        }
    }

    pub async fn enrich(&self, article: &mut NewsArticle) {
        if !self.config.intelligence_enabled {
            article.mark_intelligence_disabled();
            return;
        }
        let request = self.client.post(format!("{}/classify", self.config.intelligence_url)).json(&json!({
            "source": &article.source,
            "provider_article_id": &article.provider_article_id,
            "canonical_article_id": &article.canonical_article_id,
            "published_at": article.published_at.to_rfc3339(),
            "title": &article.title,
            "teaser": &article.teaser,
            "body_text": &article.body_text,
            "extracted_text": &article.extracted_text,
            "article_url": &article.article_url,
            "publisher_name": &article.publisher_name,
            "tickers": &article.tickers,
            "channels": &article.channels,
            "tags": &article.tags,
            "keywords": &article.keywords,
            "content_scope": &article.content_scope,
            "scanner_relevance": &article.scanner_relevance,
            "model_relevance": &article.model_relevance,
            "catalyst_labels": &article.catalyst_labels,
            "insight_tickers": &article.insight_tickers,
            "insight_sentiments": &article.insight_sentiments,
            "insight_reasons": &article.insight_reasons,
        }));
        let result = timeout(Duration::from_millis(self.config.intelligence_timeout_ms), request.send()).await;
        let response = match result {
            Ok(Ok(response)) => response,
            Ok(Err(error)) => {
                article.mark_intelligence_failed(error.to_string());
                return;
            }
            Err(_) => {
                article.mark_intelligence_failed("timeout".to_string());
                return;
            }
        };
        let status = response.status();
        let text = match response.text().await {
            Ok(text) => text,
            Err(error) => {
                article.mark_intelligence_failed(error.to_string());
                return;
            }
        };
        if !status.is_success() {
            article.mark_intelligence_failed(format!("HTTP {status}: {text}"));
            return;
        }
        match serde_json::from_str::<IntelligenceResponse>(&text) {
            Ok(labels) => article.apply_intelligence(labels),
            Err(error) => article.mark_intelligence_failed(format!("decode_failed: {error}: {text}")),
        }
    }
}
