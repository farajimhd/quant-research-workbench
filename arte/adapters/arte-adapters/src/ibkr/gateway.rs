//! Inactive-until-called Client Portal HTTPS transport. No background services.
use super::{
    session::Gate,
    submission::{AuthorizedRequest, Response, Scope, Transport},
};
use arte_core::{
    config::{Acceptance, HardwareProfile},
    Error, Result,
};
use std::{
    collections::BTreeSet,
    sync::{Arc, Mutex},
    time::Duration,
};

pub struct Http {
    client: reqwest::Client,
    base: String,
    scope: Scope,
    gate: Arc<Mutex<Gate>>,
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
        gate: Arc<Mutex<Gate>>,
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
            gate,
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
    /// clear pending requests. The session owner must pace these control calls.
    pub async fn refresh_status(&self) -> Result<()> {
        let result = self.post("/iserver/auth/status", "{}".into()).await;
        let mut gate = self
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
        self.gate
            .lock()
            .map_err(|_| Error::Unready("broker gate poisoned".into()))?
            .require(&self.scope, now_monotonic_ns)
    }
    async fn send(&mut self, request: AuthorizedRequest) -> Result<Response> {
        if request.scope() != &self.scope {
            return Err(Error::Conflict("broker request transport mismatch".into()));
        }
        // The guard is released before I/O, but pending_request remains latched.
        // Cancellation or a lost response cannot permit another account to send.
        self.gate
            .lock()
            .map_err(|_| Error::Unready("broker gate poisoned".into()))?
            .begin(&self.scope, request.hash(), (self.monotonic_now)())?;
        self.post(request.path(), request.body().into()).await
    }
}
#[cfg(test)]
mod tests {
    use super::*;
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
