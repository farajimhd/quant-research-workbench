//! One shared brokerage-session gate across all account transports.
use super::submission::Scope;
use arte_core::{strategy_dispatch::Mode, Error, Result};
use std::collections::BTreeSet;

#[derive(serde::Deserialize)]
struct Flags {
    authenticated: Option<bool>,
    established: Option<bool>,
    connected: Option<bool>,
    competing: Option<bool>,
}
pub struct Gate {
    mode: Mode,
    session_id: String,
    accounts: BTreeSet<String>,
    maximum_age_ns: u64,
    observed_at: Option<u64>,
    ready: bool,
    pending_request: Option<String>,
}
impl Gate {
    pub fn new(
        mode: Mode,
        session_id: String,
        accounts: BTreeSet<String>,
        maximum_age_ns: u64,
    ) -> Result<Self> {
        if accounts.is_empty() || accounts.len() > 4096 || maximum_age_ns == 0 {
            return Err(Error::Invalid("broker session budget".into()));
        }
        for account in &accounts {
            Scope::new(mode, session_id.clone(), account.clone())?;
        }
        Ok(Self {
            mode,
            session_id,
            accounts,
            maximum_age_ns,
            observed_at: None,
            ready: false,
            pending_request: None,
        })
    }
    /// Caller stamps completion using the same monotonic clock as transports.
    /// Handles documented success.value and direct response forms; never treats
    /// absent authentication fields as ready. Status refresh cannot clear a send.
    pub fn observe(&mut self, body: &[u8], now_ns: u64) -> Result<()> {
        self.ready = false;
        if body.len() > 16 * 1024 || self.observed_at.is_some_and(|at| now_ns < at) {
            return Err(Error::Invalid(
                "broker status size or clock reversal".into(),
            ));
        }
        let value: serde_json::Value =
            serde_json::from_slice(body).map_err(|e| Error::Serialization(e.to_string()))?;
        let flags = if value.get("success").is_some() {
            if value.get("authenticated").is_some() {
                return Err(Error::Conflict("ambiguous broker status wrapper".into()));
            }
            value
                .get("success")
                .and_then(|v| v.get("value"))
                .ok_or_else(|| Error::Invalid("broker status wrapper".into()))?
        } else {
            &value
        };
        let flags: Flags = serde_json::from_value(flags.clone())
            .map_err(|e| Error::Serialization(e.to_string()))?;
        self.observed_at = Some(now_ns);
        self.ready = flags.authenticated == Some(true)
            && flags.established == Some(true)
            && flags.connected == Some(true)
            && flags.competing == Some(false);
        Ok(())
    }
    pub fn invalidate(&mut self) {
        self.ready = false;
    }
    pub fn require(&self, scope: &Scope, now_ns: u64) -> Result<()> {
        if scope.mode() != self.mode
            || scope.session_id() != self.session_id
            || !self.accounts.contains(scope.account())
        {
            return Err(Error::Conflict("broker session scope mismatch".into()));
        }
        if !self.ready
            || self
                .observed_at
                .is_none_or(|at| now_ns < at || now_ns - at >= self.maximum_age_ns)
            || self.pending_request.is_some()
        {
            return Err(Error::Unready(
                "broker session stale, blocked or awaiting outcome resolution".into(),
            ));
        }
        Ok(())
    }
    pub fn begin(&mut self, scope: &Scope, request_hash: &str, now_ns: u64) -> Result<()> {
        self.require(scope, now_ns)?;
        if request_hash.len() != 64
            || !request_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("broker request hash".into()));
        }
        self.pending_request = Some(request_hash.into());
        Ok(())
    }
    pub fn pending_request(&self) -> Option<&str> {
        self.pending_request.as_deref()
    }
    // No generic reset: durable response/reply reconciliation must own release.
}
#[cfg(test)]
mod tests {
    use super::*;
    const READY: &[u8] =
        br#"{"authenticated":true,"established":true,"connected":true,"competing":false}"#;
    #[test]
    fn all_accounts_share_pending_outcome_and_status_cannot_clear_it() {
        let mut gate = Gate::new(
            Mode::Paper,
            "s".into(),
            BTreeSet::from(["DU1".into(), "DU2".into()]),
            100,
        )
        .unwrap();
        let a = Scope::new(Mode::Paper, "s".into(), "DU1".into()).unwrap();
        let b = Scope::new(Mode::Paper, "s".into(), "DU2".into()).unwrap();
        assert!(gate.require(&a, 10).is_err());
        gate.observe(READY, 10).unwrap();
        assert!(gate.require(&a, 109).is_ok());
        assert!(gate.require(&a, 110).is_err());
        for foreign in [
            Scope::new(Mode::Live, "s".into(), "DU1".into()).unwrap(),
            Scope::new(Mode::Paper, "other".into(), "DU1".into()).unwrap(),
            Scope::new(Mode::Paper, "s".into(), "DU3".into()).unwrap(),
        ] {
            assert!(gate.require(&foreign, 11).is_err());
        }
        assert!(gate.begin(&a, "invalid", 11).is_err());
        assert!(gate.pending_request().is_none());
        gate.begin(&a, &"a".repeat(64), 11).unwrap();
        assert!(gate.require(&b, 11).is_err());
        gate.observe(READY, 12).unwrap();
        assert!(gate.require(&b, 12).is_err());
        assert!(gate.pending_request().is_some());
    }
    #[test]
    fn malformed_missing_competing_and_rewound_status_fail_closed() {
        let scope = Scope::new(Mode::Paper, "s".into(), "DU1".into()).unwrap();
        for body in [
            b"{}".as_slice(),
            br#"{"authenticated":true,"established":true,"connected":true,"competing":true}"#,
            b"garbage",
        ] {
            let mut gate =
                Gate::new(Mode::Paper, "s".into(), BTreeSet::from(["DU1".into()]), 100).unwrap();
            gate.observe(READY, 10).unwrap();
            let _ = gate.observe(body, 11);
            assert!(gate.require(&scope, 11).is_err());
        }
        let mut gate =
            Gate::new(Mode::Paper, "s".into(), BTreeSet::from(["DU1".into()]), 100).unwrap();
        let wrapper = format!(
            "{{\"success\":{{\"value\":{}}}}}",
            std::str::from_utf8(READY).unwrap()
        );
        gate.observe(wrapper.as_bytes(), 10).unwrap();
        assert!(gate.require(&scope, 10).is_ok());
        assert!(gate.observe(READY, 9).is_err());
        assert!(gate.require(&scope, 10).is_err());
    }
}
