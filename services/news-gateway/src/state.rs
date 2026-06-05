use crate::model::{NewsArticleSummary, NewsSnapshot, TickerNewsSnapshot};
use chrono::{Duration as ChronoDuration, Utc};
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct SharedNewsState {
    inner: Arc<RwLock<NewsState>>,
}

struct NewsState {
    history: VecDeque<NewsArticleSummary>,
    history_limit: usize,
    latest_by_id: HashMap<String, NewsArticleSummary>,
    ticker_history: HashMap<String, VecDeque<NewsArticleSummary>>,
}

impl SharedNewsState {
    pub fn new(history_limit: usize) -> Self {
        Self {
            inner: Arc::new(RwLock::new(NewsState {
                history: VecDeque::with_capacity(history_limit.min(10_000)),
                history_limit,
                latest_by_id: HashMap::new(),
                ticker_history: HashMap::new(),
            })),
        }
    }

    pub async fn apply(&self, row: NewsArticleSummary) -> bool {
        let mut state = self.inner.write().await;
        let key = format!("{}:{}", row.source, row.provider_article_id);
        let is_update = state.latest_by_id.contains_key(&key);
        state.latest_by_id.insert(key, row.clone());
        state.history.push_back(row.clone());
        while state.history.len() > state.history_limit {
            state.history.pop_front();
        }
        for ticker in &row.tickers {
            let rows = state
                .ticker_history
                .entry(ticker.clone())
                .or_insert_with(|| VecDeque::with_capacity(1_000));
            rows.push_back(row.clone());
            while rows.len() > 1_000 {
                rows.pop_front();
            }
        }
        is_update
    }

    pub async fn recent_snapshot(&self, limit: usize) -> NewsSnapshot {
        let state = self.inner.read().await;
        let mut rows = state.history.iter().cloned().collect::<Vec<_>>();
        rows.sort_by(|left, right| right.published_at.cmp(&left.published_at));
        rows.truncate(limit);
        NewsSnapshot {
            as_of: Utc::now(),
            row_count: rows.len(),
            rows,
            total_articles: state.latest_by_id.len(),
        }
    }

    pub async fn ticker_snapshot(&self, ticker: &str, limit: usize) -> TickerNewsSnapshot {
        let normalized = ticker.to_ascii_uppercase();
        let state = self.inner.read().await;
        let mut rows = state
            .ticker_history
            .get(&normalized)
            .map(|items| items.iter().cloned().collect::<Vec<_>>())
            .unwrap_or_default();
        rows.sort_by(|left, right| right.published_at.cmp(&left.published_at));
        let now = Utc::now();
        let news_count_5m = rows
            .iter()
            .filter(|row| row.published_at.clone() >= now - ChronoDuration::minutes(5))
            .count();
        let news_count_30m = rows
            .iter()
            .filter(|row| row.published_at.clone() >= now - ChronoDuration::minutes(30))
            .count();
        let news_count_session = rows
            .iter()
            .filter(|row| row.published_at.date_naive() == now.date_naive())
            .count();
        rows.truncate(limit);
        TickerNewsSnapshot {
            as_of: now,
            news_count_5m,
            news_count_30m,
            news_count_session,
            rows,
            ticker: normalized,
        }
    }
}
