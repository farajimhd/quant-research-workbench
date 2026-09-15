//! Inactive-until-called Client Portal HTTPS transport. No background services.
use super::{
    session::Gate,
    submission::{AuthorizedRequest, Response, Scope, Transport},
};
use crate::request_governor::{Governor, Policy};
use arte_core::{
    config::{Acceptance, HardwareProfile},
    Error, Result,
};
use std::{
    collections::BTreeSet,
    sync::{Arc, Mutex},
    time::Duration,
};

/// Create once per broker session; all account transports share this owner.
pub struct SharedSession {
    gate: Mutex<Gate>,
    pacing: Governor,
}
impl SharedSession {
    pub fn new(gate: Gate, policy: Policy) -> Result<Self> {
        if policy.minimum_interval_ms < 100
            || policy.maximum_inflight > 10
            || policy.default_cooldown_ms < 900_000
        {
            return Err(Error::Invalid(
                "broker pacing requires at most 10 requests/second and 15-minute default penalty"
                    .into(),
            ));
        }
        Ok(Self {
            gate: Mutex::new(gate),
            pacing: Governor::new(policy)?,
        })
    }
    pub async fn pacing_status(&self) -> crate::request_governor::Status {
        self.pacing.status().await
    }
    async fn rate_limited(
        &self,
        retry_after: Option<&str>,
        utc_now: chrono::DateTime<chrono::Utc>,
    ) -> Result<()> {
        self.pacing.cooldown(None, utc_now).await?;
        if let Some(header) = retry_after {
            if self.pacing.cooldown(Some(header), utc_now).await.is_err() {
                // The governor has halted. Retain the HTTP response for audit
                // instead of replacing it with a parsing error.
                self.gate
                    .lock()
                    .map_err(|_| Error::Unready("broker gate poisoned".into()))?
                    .invalidate();
            }
        }
        Ok(())
    }
}
pub struct Http {
    client: reqwest::Client,
    base: String,
    scope: Scope,
    shared: Arc<SharedSession>,
    monotonic_now: Arc<dyn Fn() -> u64 + Send + Sync>,
}
fn endpoint(value: &str) -> Result<String> {
    let url =
        reqwest::Url::parse(value).map_err(|_| Error::Invalid("broker gateway URL".into()))?;
    if url.scheme() != "https"
        || !matches!(
            url.host_str(),
            Some("localhost" | "127.0.0.1" | "[::1]" | "::1")
        )
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.path(), "/v1/api" | "/v1/api/")
    {
        return Err(Error::Invalid(
            "broker gateway must be local HTTPS /v1/api without credentials or query".into(),
        ));
    }
    Ok(url.as_str().trim_end_matches('/').into())
}
impl Http {
    /// Explicit trusted certificate is optional; normal TLS verification always
    /// remains enabled. No insecure-certificate or hostname bypass is offered.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        base: &str,
        scope: Scope,
        shared: Arc<SharedSession>,
        monotonic_now: Arc<dyn Fn() -> u64 + Send + Sync>,
        timeout: Duration,
        trusted_certificate_pem: Option<&[u8]>,
        profile: &HardwareProfile,
        passed: &BTreeSet<Acceptance>,
    ) -> Result<Self> {
        if !arte_core::config::live_blockers(profile, passed).is_empty() {
            return Err(Error::Unready(
                "broker transport acceptance gates incomplete".into(),
            ));
        }
        if timeout.is_zero() || timeout > Duration::from_secs(30) {
            return Err(Error::Invalid("broker HTTP timeout".into()));
        }
        let base = endpoint(base)?;
        let mut builder = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .timeout(timeout)
            .connect_timeout(timeout);
        if let Some(pem) = trusted_certificate_pem {
            if pem.len() > 64 * 1024 {
                return Err(Error::Capacity("broker certificate size".into()));
            }
            builder = builder.add_root_certificate(
                reqwest::Certificate::from_pem(pem)
                    .map_err(|_| Error::Invalid("broker trust certificate".into()))?,
            );
        }
        let client = builder
            .build()
            .map_err(|_| Error::Invalid("broker HTTP client configuration".into()))?;
        Ok(Self {
            client,
            base,
            scope,
            shared,
            monotonic_now,
        })
    }
    async fn post(&self, path: &str, body: String) -> Result<Response> {
        let mut response = self
            .client
            .post(format!("{}{path}", self.base))
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body)
            .send()
            .await
            .map_err(|_| Error::Unready("broker HTTP outcome unknown".into()))?;
        let status = response.status().as_u16();
        if status == 429 {
            // The documented penalty is a floor, even when Retry-After says less.
            let retry = response
                .headers()
                .get(reqwest::header::RETRY_AFTER)
                .map(|header| header.to_str().unwrap_or("invalid-header"));
            self.shared.rate_limited(retry, chrono::Utc::now()).await?;
        }
        let mut body = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|_| Error::Unready("broker response incomplete".into()))?
        {
            if chunk.len() > (64 * 1024_usize).saturating_sub(body.len()) {
                return Err(Error::Capacity("broker response byte limit".into()));
            }
            body.extend_from_slice(&chunk);
        }
        Ok(Response { status, body })
    }
    /// Uses the documented status endpoint. Does not log in, resolve prompts or
    /// clear pending requests. Shares global pacing with order sends.
    pub async fn refresh_status(&self) -> Result<()> {
        let _admission = self.shared.pacing.acquire().await?;
        let result = self.post("/iserver/auth/status", "{}".into()).await;
        let mut gate = self
            .shared
            .gate
            .lock()
            .map_err(|_| Error::Unready("broker gate poisoned".into()))?;
        match result {
            Ok(response) if (200..300).contains(&response.status) => {
                gate.observe(&response.body, (self.monotonic_now)())
            }
            _ => {
                gate.invalidate();
                Err(Error::Unready("broker status refresh failed".into()))
            }
        }
    }
}
impl Transport for Http {
    fn scope(&self) -> &Scope {
        &self.scope
    }
    fn require_ready(&self, now_monotonic_ns: u64) -> Result<()> {
        self.shared
            .gate
            .lock()
            .map_err(|_| Error::Unready("broker gate poisoned".into()))?
            .require(&self.scope, now_monotonic_ns)
    }
    async fn send(&mut self, request: AuthorizedRequest) -> Result<Response> {
        if request.scope() != &self.scope {
            return Err(Error::Conflict("broker request transport mismatch".into()));
        }
        let _admission = self.shared.pacing.try_acquire()?;
        // The guard is released before I/O, but pending_request remains latched.
        // Cancellation or a lost response cannot permit another account to send.
        self.shared
            .gate
            .lock()
            .map_err(|_| Error::Unready("broker gate poisoned".into()))?
            .begin(&self.scope, request.hash(), (self.monotonic_now)())?;
        self.post(request.path(), request.body().into()).await
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test(start_paused = true)]
    async fn broker_penalty_floor_and_invalid_retry_halt_are_shared() {
        use arte_core::strategy_dispatch::Mode;
        let policy = Policy {
            maximum_inflight: 2,
            minimum_interval_ms: 100,
            default_cooldown_ms: 900_000,
            maximum_cooldown_ms: 3_600_000,
        };
        let gate =
            || Gate::new(Mode::Paper, "s".into(), BTreeSet::from(["DU1".into()]), 100).unwrap();
        assert!(SharedSession::new(
            gate(),
            Policy {
                minimum_interval_ms: 99,
                ..policy
            }
        )
        .is_err());
        assert!(SharedSession::new(
            gate(),
            Policy {
                default_cooldown_ms: 100,
                ..policy
            }
        )
        .is_err());
        let shared = Arc::new(SharedSession::new(gate(), policy).unwrap());
        let other = shared.clone();
        shared
            .rate_limited(Some("1"), chrono::Utc::now())
            .await
            .unwrap();
        assert_eq!(other.pacing_status().await.cooldown_remaining_ms, 900_000);
        assert!(other.pacing.try_acquire().is_err());
        shared
            .rate_limited(Some("1800"), chrono::Utc::now())
            .await
            .unwrap();
        assert_eq!(other.pacing_status().await.cooldown_remaining_ms, 1_800_000);
        shared
            .rate_limited(Some("invalid"), chrono::Utc::now())
            .await
            .unwrap();
        assert!(other.pacing_status().await.halted);
        assert!(other.pacing.try_acquire().is_err());
    }
    #[test]
    fn gateway_url_cannot_redirect_scope_to_remote_or_insecure_endpoint() {
        assert_eq!(
            endpoint("https://localhost:5000/v1/api/").unwrap(),
            "https://localhost:5000/v1/api"
        );
        assert!(endpoint("https://127.0.0.1:5000/v1/api").is_ok());
        for url in [
            "http://localhost:5000/v1/api",
            "https://example.com/v1/api",
            "https://localhost/other",
            "https://user:pass@localhost/v1/api",
            "https://localhost/v1/api?q=x",
            "https://localhost/v1/api#x",
        ] {
            assert!(endpoint(url).is_err());
        }
    }
}
