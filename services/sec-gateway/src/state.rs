use crate::model::SecFilingSummary;
use std::collections::{HashSet, VecDeque};
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct SharedSecState {
    inner: Arc<RwLock<SecState>>,
    recent_limit: usize,
}

#[derive(Default)]
struct SecState {
    seen_accessions: HashSet<String>,
    recent: VecDeque<SecFilingSummary>,
}

impl SharedSecState {
    pub fn new(recent_limit: usize) -> Self {
        Self {
            inner: Arc::new(RwLock::new(SecState::default())),
            recent_limit,
        }
    }

    pub async fn mark_seen(&self, accession_number: &str) -> bool {
        let mut guard = self.inner.write().await;
        guard.seen_accessions.insert(accession_number.to_string())
    }

    pub async fn has_seen(&self, accession_number: &str) -> bool {
        let guard = self.inner.read().await;
        guard.seen_accessions.contains(accession_number)
    }

    pub async fn push_recent(&self, summary: SecFilingSummary) {
        let mut guard = self.inner.write().await;
        guard.recent.push_front(summary);
        while guard.recent.len() > self.recent_limit {
            guard.recent.pop_back();
        }
    }

    pub async fn recent(&self, limit: usize) -> Vec<SecFilingSummary> {
        let guard = self.inner.read().await;
        guard.recent.iter().take(limit).cloned().collect()
    }
}
