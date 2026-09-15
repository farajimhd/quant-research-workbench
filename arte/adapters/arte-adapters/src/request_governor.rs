//! Shared, bounded admission for one provider credential scope. No automatic retries.
use arte_core::{Error, Result};
use serde::{Deserialize, Serialize};
use std::{sync::Arc, time::Duration};
use tokio::{
    sync::{Mutex, OwnedSemaphorePermit, Semaphore},
    time::Instant,
};

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub maximum_inflight: usize,
    pub minimum_interval_ms: u64,
    pub default_cooldown_ms: u64,
    pub maximum_cooldown_ms: u64,
}
struct State {
    next: Instant,
    cooldown: Instant,
    halted: bool,
    admitted: u64,
}
#[derive(Debug, Clone, Serialize)]
pub struct Status {
    pub halted: bool,
    pub admitted: u64,
    pub cooldown_remaining_ms: u128,
}
pub struct Governor {
    policy: Policy,
    slots: Arc<Semaphore>,
    state: Mutex<State>,
}
impl Governor {
    pub fn new(policy: Policy) -> Result<Self> {
        if policy.maximum_inflight == 0
            || policy.maximum_inflight > 1024
            || policy.minimum_interval_ms == 0
            || policy.minimum_interval_ms > 60_000
            || policy.default_cooldown_ms == 0
            || policy.default_cooldown_ms > policy.maximum_cooldown_ms
            || policy.maximum_cooldown_ms > 86_400_000
        {
            return Err(Error::Invalid(
                "explicit bounded REST pacing policy required".into(),
            ));
        }
        let now = Instant::now();
        Ok(Self {
            policy,
            slots: Arc::new(Semaphore::new(policy.maximum_inflight)),
            state: Mutex::new(State {
                next: now,
                cooldown: now,
                halted: false,
                admitted: 0,
            }),
        })
    }
    /// Hold this permit until the entire response has been consumed or abandoned.
    pub async fn acquire(&self) -> Result<OwnedSemaphorePermit> {
        let permit = self
            .slots
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| Error::Unready("REST governor halted".into()))?;
        loop {
            let mut state = self.state.lock().await;
            if state.halted {
                return Err(Error::Unready("REST governor halted".into()));
            }
            let deadline = state.next.max(state.cooldown);
            let now = Instant::now();
            if now >= deadline {
                state.admitted = state
                    .admitted
                    .checked_add(1)
                    .ok_or_else(|| Error::Capacity("REST admission counter exhausted".into()))?;
                state.next = now + Duration::from_millis(self.policy.minimum_interval_ms);
                return Ok(permit);
            }
            drop(state);
            tokio::time::sleep_until(deadline).await;
        }
    }
    /// IMF-fixdate and decimal seconds are supported. Other values halt admission,
    /// rather than guessing a shorter delay. UTC is required only for HTTP dates.
    pub async fn cooldown(
        &self,
        header: Option<&str>,
        utc_now: chrono::DateTime<chrono::Utc>,
    ) -> Result<()> {
        let delay = match header {
            None => Some(Duration::from_millis(self.policy.default_cooldown_ms)),
            Some(value) if !value.is_empty() && value.bytes().all(|b| b.is_ascii_digit()) => {
                value.parse::<u64>().ok().map(Duration::from_secs)
            }
            Some(value) => {
                chrono::NaiveDateTime::parse_from_str(value, "%a, %d %b %Y %H:%M:%S GMT")
                    .ok()
                    .and_then(|date| (date.and_utc() - utc_now).to_std().ok())
            }
        };
        let mut state = self.state.lock().await;
        let Some(delay) =
            delay.filter(|d| *d <= Duration::from_millis(self.policy.maximum_cooldown_ms))
        else {
            state.halted = true;
            self.slots.close();
            return Err(Error::Unready(
                "REST cooldown invalid or exceeds approved bound; governor halted".into(),
            ));
        };
        state.cooldown = state.cooldown.max(Instant::now() + delay);
        Ok(())
    }
    pub async fn status(&self) -> Status {
        let state = self.state.lock().await;
        Status {
            halted: state.halted,
            admitted: state.admitted,
            cooldown_remaining_ms: state
                .cooldown
                .saturating_duration_since(Instant::now())
                .as_millis(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn governor() -> Governor {
        Governor::new(Policy {
            maximum_inflight: 2,
            minimum_interval_ms: 100,
            default_cooldown_ms: 500,
            maximum_cooldown_ms: 5000,
        })
        .unwrap()
    }
    #[tokio::test(start_paused = true)]
    async fn spacing_and_shared_cooldown() {
        let governor = governor();
        let start = Instant::now();
        let first = governor.acquire().await.unwrap();
        let second = governor.acquire().await.unwrap();
        assert_eq!(start.elapsed(), Duration::from_millis(100));
        drop((first, second));
        governor
            .cooldown(Some("2"), chrono::Utc::now())
            .await
            .unwrap();
        let _third = governor.acquire().await.unwrap();
        assert_eq!(start.elapsed(), Duration::from_millis(2100));
        assert_eq!(governor.status().await.admitted, 3);
    }
    #[tokio::test(start_paused = true)]
    async fn inflight_bound_and_cancellation_release() {
        let governor = governor();
        let first = governor.acquire().await.unwrap();
        let second = governor.acquire().await.unwrap();
        assert!(
            tokio::time::timeout(Duration::from_secs(1), governor.acquire())
                .await
                .is_err()
        );
        drop(first);
        let _third = governor.acquire().await.unwrap();
        drop(second);
    }
    #[tokio::test(start_paused = true)]
    async fn dates_and_unsafe_cooldowns() {
        let governor = governor();
        let now = chrono::DateTime::parse_from_rfc3339("2026-09-15T12:00:00Z")
            .unwrap()
            .to_utc();
        governor
            .cooldown(Some("Tue, 15 Sep 2026 12:00:03 GMT"), now)
            .await
            .unwrap();
        assert_eq!(governor.status().await.cooldown_remaining_ms, 3000);
        assert!(governor.cooldown(Some("6"), now).await.is_err());
        assert!(governor.acquire().await.is_err());
        assert!(governor.status().await.halted);
        assert!(super::tests::governor()
            .cooldown(Some("invalid"), now)
            .await
            .is_err());
    }
}
