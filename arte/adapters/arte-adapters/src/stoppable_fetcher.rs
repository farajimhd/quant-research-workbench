//! Cancellation boundary for read-only acquisition only, never database publication.
use crate::{massive::FetchedPage, rest_acquisition::Fetcher};
use arte_core::{Error, Result};
use tokio::sync::watch;

pub struct StoppableFetcher<F> {
    inner: F,
    stop: watch::Receiver<bool>,
    cancelled: bool,
}
impl<F> StoppableFetcher<F> {
    pub fn new(inner: F, stop: watch::Receiver<bool>) -> Self {
        Self {
            inner,
            stop,
            cancelled: false,
        }
    }
    pub fn cancelled(&self) -> bool {
        self.cancelled
    }
}
async fn stopped(stop: &mut watch::Receiver<bool>) {
    loop {
        if *stop.borrow_and_update() {
            return;
        }
        if stop.changed().await.is_err() {
            return;
        }
    }
}
impl<F: Fetcher + Send> Fetcher for StoppableFetcher<F> {
    async fn fetch(&mut self, url: &str, path: &str) -> Result<FetchedPage> {
        if self.cancelled {
            return Err(Error::Unready("historical fetch stopped".into()));
        }
        tokio::select! {
            biased;
            () = stopped(&mut self.stop) => {
                self.cancelled = true;
                Err(Error::Unready("historical fetch stopped".into()))
            }
            result = self.inner.fetch(url, path) => result,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };
    struct Pending(Arc<AtomicUsize>);
    impl Fetcher for Pending {
        async fn fetch(&mut self, _: &str, _: &str) -> Result<FetchedPage> {
            self.0.fetch_add(1, Ordering::SeqCst);
            std::future::pending().await
        }
    }
    #[tokio::test]
    async fn stop_cancels_pending_read_and_latches() {
        let calls = Arc::new(AtomicUsize::new(0));
        let (tx, rx) = watch::channel(false);
        let mut fetcher = StoppableFetcher::new(Pending(calls.clone()), rx);
        let stop = async {
            tokio::task::yield_now().await;
            tx.send_replace(true);
        };
        let (result, ()) = tokio::join!(fetcher.fetch("unused", "unused"), stop);
        assert!(result.is_err());
        assert!(fetcher.cancelled());
        assert_eq!(calls.load(Ordering::SeqCst), 1);
        tx.send_replace(false);
        assert!(fetcher.fetch("unused", "unused").await.is_err());
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }
    #[tokio::test]
    async fn closed_control_channel_never_starts_request() {
        let calls = Arc::new(AtomicUsize::new(0));
        let (tx, rx) = watch::channel(false);
        drop(tx);
        let mut fetcher = StoppableFetcher::new(Pending(calls.clone()), rx);
        assert!(fetcher.fetch("unused", "unused").await.is_err());
        assert!(fetcher.cancelled());
        assert_eq!(calls.load(Ordering::SeqCst), 0);
    }
    struct Failed;
    struct Admitted(Arc<crate::request_governor::Governor>);
    impl Fetcher for Admitted {
        async fn fetch(&mut self, _: &str, _: &str) -> Result<FetchedPage> {
            let _permit = self.0.acquire().await?;
            std::future::pending().await
        }
    }
    #[tokio::test(start_paused = true)]
    async fn cancelled_response_releases_shared_request_slot() {
        let governor = Arc::new(
            crate::request_governor::Governor::new(crate::request_governor::Policy {
                maximum_inflight: 1,
                minimum_interval_ms: 1,
                default_cooldown_ms: 100,
                maximum_cooldown_ms: 1000,
            })
            .unwrap(),
        );
        let (tx, rx) = watch::channel(false);
        let mut fetcher = StoppableFetcher::new(Admitted(governor.clone()), rx);
        let stop = async {
            tokio::task::yield_now().await;
            tx.send_replace(true);
        };
        let (result, ()) = tokio::join!(fetcher.fetch("unused", "unused"), stop);
        assert!(result.is_err());
        assert_eq!(governor.status().await.admitted, 1);
        let permit =
            tokio::time::timeout(std::time::Duration::from_secs(1), governor.acquire()).await;
        assert!(permit.unwrap().is_ok());
        assert_eq!(governor.status().await.admitted, 2);
    }
    impl Fetcher for Failed {
        async fn fetch(&mut self, _: &str, _: &str) -> Result<FetchedPage> {
            Err(Error::Conflict("provider error".into()))
        }
    }
    #[tokio::test]
    async fn ordinary_failure_is_not_a_stop() {
        let (_tx, rx) = watch::channel(false);
        let mut fetcher = StoppableFetcher::new(Failed, rx);
        assert!(matches!(
            fetcher.fetch("unused", "unused").await,
            Err(Error::Conflict(_))
        ));
        assert!(!fetcher.cancelled());
    }
}
