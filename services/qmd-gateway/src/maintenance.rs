use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::BTreeSet;
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct SharedMaintenanceState {
    inner: Arc<RwLock<MaintenanceSnapshot>>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct MaintenanceSnapshot {
    pub active: bool,
    pub active_symbols: Vec<String>,
    pub completed_jobs: u64,
    pub completed_symbols: u64,
    pub current_interval_end_utc: Option<DateTime<Utc>>,
    pub current_interval_reason: String,
    pub current_interval_start_utc: Option<DateTime<Utc>>,
    pub current_symbol: String,
    pub errors: u64,
    pub finished_at_utc: Option<DateTime<Utc>>,
    pub last_completed_message: String,
    pub message: String,
    pub mode: String,
    pub page_limited_symbols: u64,
    pub phase: String,
    pub rows_written: u64,
    pub started_at_utc: Option<DateTime<Utc>>,
    pub status: String,
    pub total_intervals: u64,
    pub total_jobs: u64,
    pub total_symbols: u64,
    pub updated_at_utc: Option<DateTime<Utc>>,
    pub window_end_utc: Option<DateTime<Utc>>,
    pub window_start_utc: Option<DateTime<Utc>>,
}

impl SharedMaintenanceState {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(MaintenanceSnapshot {
                status: "idle".to_string(),
                message: "No maintenance task is running.".to_string(),
                ..MaintenanceSnapshot::default()
            })),
        }
    }

    pub async fn snapshot(&self) -> MaintenanceSnapshot {
        self.inner.read().await.clone()
    }

    pub async fn start(
        &self,
        phase: &str,
        mode: &str,
        message: &str,
        window_start: Option<DateTime<Utc>>,
        window_end: Option<DateTime<Utc>>,
    ) {
        let now = Utc::now();
        let last_completed_message = self.inner.read().await.last_completed_message.clone();
        *self.inner.write().await = MaintenanceSnapshot {
            active: true,
            last_completed_message,
            message: message.to_string(),
            mode: mode.to_string(),
            phase: phase.to_string(),
            started_at_utc: Some(now),
            status: "running".to_string(),
            updated_at_utc: Some(now),
            window_end_utc: window_end,
            window_start_utc: window_start,
            ..MaintenanceSnapshot::default()
        };
    }

    pub async fn configure_totals(&self, symbols: u64, intervals: u64) {
        let mut state = self.inner.write().await;
        state.total_symbols = symbols;
        state.total_intervals = intervals;
        state.total_jobs = symbols.saturating_mul(intervals);
        state.updated_at_utc = Some(Utc::now());
    }

    pub async fn set_message(&self, status: &str, message: &str) {
        let mut state = self.inner.write().await;
        state.status = status.to_string();
        state.message = message.to_string();
        state.updated_at_utc = Some(Utc::now());
    }

    pub async fn start_interval(
        &self,
        symbol: &str,
        reason: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) {
        let mut state = self.inner.write().await;
        let mut active = state
            .active_symbols
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();
        active.insert(symbol.to_string());
        state.active_symbols = active.into_iter().collect();
        state.current_symbol = symbol.to_string();
        state.current_interval_reason = reason.to_string();
        state.current_interval_start_utc = Some(start);
        state.current_interval_end_utc = Some(end);
        state.message = format!("{symbol}: repairing {reason} from {start} to {end}");
        state.status = "running".to_string();
        state.updated_at_utc = Some(Utc::now());
    }

    pub async fn complete_interval(&self, rows: u64, error: bool, page_limit_hit: bool) {
        let mut state = self.inner.write().await;
        state.completed_jobs = state.completed_jobs.saturating_add(1);
        state.rows_written = state.rows_written.saturating_add(rows);
        if error {
            state.errors = state.errors.saturating_add(1);
        }
        if page_limit_hit {
            state.page_limited_symbols = state.page_limited_symbols.saturating_add(1);
        }
        state.updated_at_utc = Some(Utc::now());
    }

    pub async fn complete_symbol(&self, symbol: &str) {
        let mut state = self.inner.write().await;
        state.completed_symbols = state.completed_symbols.saturating_add(1);
        state.active_symbols.retain(|value| value != symbol);
        state.updated_at_utc = Some(Utc::now());
    }

    pub async fn finish(&self, status: &str, message: &str) {
        let now = Utc::now();
        let mut state = self.inner.write().await;
        state.active = false;
        state.active_symbols.clear();
        state.current_symbol.clear();
        state.finished_at_utc = Some(now);
        state.last_completed_message = message.to_string();
        state.message = message.to_string();
        state.status = status.to_string();
        state.updated_at_utc = Some(now);
    }
}
