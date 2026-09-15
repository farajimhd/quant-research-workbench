//! Exact broker request boundary. Constructors and offline tests perform no I/O.
use arte_core::{
    orders::{submission::SendPermit, Authorization, Bands, RiskPolicy, TradingSession},
    Error, Result,
};
use std::future::Future;

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Scope {
    mode: arte_core::strategy_dispatch::Mode,
    session_id: String,
    account: String,
}
impl Scope {
    pub fn new(
        mode: arte_core::strategy_dispatch::Mode,
        session_id: String,
        account: String,
    ) -> Result<Self> {
        use arte_core::strategy_dispatch::Mode;
        if !matches!(mode, Mode::Live | Mode::Paper)
            || session_id.is_empty()
            || session_id.len() > 128
            || account.is_empty()
            || account.len() > 64
            || !account.bytes().all(|b| b.is_ascii_alphanumeric())
        {
            return Err(Error::Invalid("broker submission scope".into()));
        }
        Ok(Self {
            mode,
            session_id,
            account,
        })
    }
    pub fn account(&self) -> &str {
        &self.account
    }
}
pub struct Request {
    scope: Scope,
    authorization_hash: String,
    path: String,
    body: String,
    hash: String,
}
impl Request {
    /// conid must come from the certified instrument reference authority. This
    /// factory pins the supplied mapping; it does not discover or certify it.
    pub fn new(authorization: &Authorization, conid: u64, scope: Scope) -> Result<Self> {
        if authorization.bracket.account != scope.account {
            return Err(Error::Conflict(
                "broker account differs from authorization".into(),
            ));
        }
        let payload = super::bracket_payload(&authorization.bracket, conid)?;
        let body =
            serde_json::to_string(&payload).map_err(|e| Error::Serialization(e.to_string()))?;
        if body.len() > 16 * 1024 {
            return Err(Error::Capacity("broker request byte limit".into()));
        }
        let path = format!("/iserver/account/{}/orders", scope.account);
        let authorization_hash = authorization.hash()?;
        let hash = arte_core::content_hash(&(
            "arte.ibkr-request.v1",
            &scope,
            &authorization_hash,
            &path,
            &body,
        ))?;
        Ok(Self {
            scope,
            authorization_hash,
            path,
            body,
            hash,
        })
    }
    pub fn hash(&self) -> &str {
        &self.hash
    }
}
/// Cannot be constructed or cloned externally. Transport receives this value
/// only after consuming a persisted marker's single-use permission.
pub struct AuthorizedRequest {
    request: Request,
}
impl AuthorizedRequest {
    pub fn scope(&self) -> &Scope {
        &self.request.scope
    }
    pub fn path(&self) -> &str {
        &self.request.path
    }
    pub fn body(&self) -> &str {
        &self.request.body
    }
    pub fn hash(&self) -> &str {
        &self.request.hash
    }
}
pub trait Transport {
    fn scope(&self) -> &Scope;
    /// Owns broker authentication, session freshness, pacing and pending replies.
    fn require_ready(&self, now_monotonic_ns: u64) -> Result<()>;
    /// Exactly one attempt. Implementations must disable redirects and retries.
    fn send(&mut self, request: AuthorizedRequest) -> impl Future<Output = Result<Vec<u8>>> + Send;
}
pub struct Safety<'a> {
    pub now_utc_ns: u64,
    pub now_monotonic_ns: u64,
    pub session: &'a TradingSession,
    pub bands: Option<&'a Bands>,
    pub policy: &'a RiskPolicy,
    pub market: arte_core::exposure::Check<'a>,
}
pub enum Delivery {
    /// Raw bounded evidence, not proof all bracket legs are working.
    Response(Vec<u8>),
    /// Do not automatically resend. Persist the outcome and reconcile.
    Unknown(String),
}
/// The clock/health closure runs on execution, not when a future is queued.
/// Cancellation after polling transport leaves ledger state Submitting; recovery
/// converts that state to Unknown. This function never retries or confirms replies.
pub async fn submit<'a>(
    request: Request,
    permit: SendPermit,
    transport: &mut impl Transport,
    current_safety: impl FnOnce() -> Result<Safety<'a>>,
) -> Result<Delivery> {
    if &request.scope != transport.scope() {
        return Err(Error::Conflict(
            "broker transport scope differs from pinned request".into(),
        ));
    }
    let safety = current_safety()?;
    let authorization = permit.validate_request(
        &request.hash,
        safety.now_utc_ns,
        safety.session,
        safety.bands,
        safety.policy,
        safety.market,
    )?;
    if authorization.hash()? != request.authorization_hash {
        return Err(Error::Conflict(
            "broker authorization identity changed".into(),
        ));
    }
    transport.require_ready(safety.now_monotonic_ns)?;
    Ok(match transport.send(AuthorizedRequest { request }).await {
        Ok(body) if body.len() <= 64 * 1024 => Delivery::Response(body),
        Ok(_) => Delivery::Unknown("broker response exceeded byte limit".into()),
        Err(error) => Delivery::Unknown(error.to_string()),
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{coverage::Interval, events::EventKind, orders::*, strategy_dispatch::Mode};
    struct Fixture {
        session: TradingSession,
        policy: RiskPolicy,
        gate: arte_core::exposure::Gate,
        ledger: OrderLedger,
        request: Request,
        permit: SendPermit,
    }
    fn fixture() -> Fixture {
        let record = arte_core::session::Session {
            exchange: "XNYS".into(),
            session: 20260915,
            previous_trading_session: 20260914,
            extended: Interval { start: 1, end: 100 },
            regular: Interval { start: 50, end: 80 },
            available_at_ns: 1,
            source_manifest_hash: "a".repeat(64),
        };
        let hash = arte_core::content_hash(&record).unwrap();
        let session = TradingSession::new(record, hash, 1, true).unwrap();
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 10,
        };
        let mut gate = arte_core::exposure::Gate::new(100, 2).unwrap();
        gate.transport(true);
        for kind in [EventKind::Trade, EventKind::Quote] {
            gate.update(1, kind, 1, true).unwrap();
        }
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(
                Bracket {
                    command_id: "c".into(),
                    account: "DU1".into(),
                    instrument: 1,
                    side: Side::Long,
                    quantity: 1,
                    entry: 100,
                    price_scale: 2,
                    stop: Some(90),
                    target: Some(110),
                    tick: 1,
                    deadline_ns: 100,
                },
                10,
                &session,
                None,
                &policy,
                gate.at(10),
            )
            .unwrap();
        let authorization = ledger.authorization("c").unwrap();
        ledger
            .acknowledge_authorization("c", &authorization)
            .unwrap();
        let scope = Scope::new(Mode::Paper, "broker-session".into(), "DU1".into()).unwrap();
        let request = Request::new(&authorization, 123, scope).unwrap();
        ledger
            .begin_submit("c", 10, &session, None, &policy, gate.at(10))
            .unwrap();
        let marker = ledger
            .prepare_submission_marker("c", request.hash(), 10)
            .unwrap()
            .clone();
        let permit = ledger.acknowledge_submission("c", &marker).unwrap();
        Fixture {
            session,
            policy,
            gate,
            ledger,
            request,
            permit,
        }
    }
    struct Mock {
        scope: Scope,
        ready: bool,
        calls: usize,
        fail: bool,
        oversized: bool,
        sent: Option<(String, String)>,
    }
    impl Transport for Mock {
        fn scope(&self) -> &Scope {
            &self.scope
        }
        fn require_ready(&self, now: u64) -> Result<()> {
            if !self.ready || now != 12 {
                return Err(Error::Unready("broker session not ready".into()));
            }
            Ok(())
        }
        async fn send(&mut self, request: AuthorizedRequest) -> Result<Vec<u8>> {
            self.calls += 1;
            self.sent = Some((request.path().into(), request.body().into()));
            if self.fail {
                return Err(Error::Unready("transport lost after write".into()));
            }
            Ok(if self.oversized {
                vec![0; 64 * 1024 + 1]
            } else {
                b"[]".to_vec()
            })
        }
    }
    #[tokio::test]
    async fn exact_request_is_sent_once_and_ambiguous_errors_are_not_retried() {
        for case in 0..6 {
            let f = fixture();
            let mut transport = Mock {
                scope: f.request.scope.clone(),
                ready: case != 1,
                calls: 0,
                fail: case == 2,
                oversized: case == 3,
                sent: None,
            };
            if case == 4 {
                transport.scope.session_id = "another-session".into();
            }
            let expected = (f.request.path.clone(), f.request.body.clone());
            let result = submit(f.request, f.permit, &mut transport, || {
                Ok(Safety {
                    now_utc_ns: if case == 5 { 100 } else { 12 },
                    now_monotonic_ns: 12,
                    session: &f.session,
                    bands: None,
                    policy: &f.policy,
                    market: f.gate.at(12),
                })
            })
            .await;
            if case == 1 || case == 4 || case == 5 {
                assert!(result.is_err());
                assert_eq!(transport.calls, 0);
            } else {
                assert_eq!(transport.calls, 1);
                assert_eq!(transport.sent, Some(expected));
                match result.unwrap() {
                    Delivery::Response(body) => {
                        assert_eq!(case, 0);
                        assert_eq!(body, b"[]");
                    }
                    Delivery::Unknown(reason) => {
                        assert!(case == 2 || case == 3);
                        assert!(!reason.is_empty());
                    }
                }
            }
            assert_eq!(f.ledger.record("c").unwrap().state, OrderState::Submitting);
        }
    }
    #[test]
    fn request_identity_pins_mapping_session_mode_and_account() {
        let f = fixture();
        let authorization = f.ledger.authorization("c").unwrap();
        let changed_mapping = Request::new(&authorization, 124, f.request.scope.clone()).unwrap();
        assert_ne!(f.request.hash(), changed_mapping.hash());
        let changed_mode = Scope::new(Mode::Live, "broker-session".into(), "DU1".into()).unwrap();
        assert_ne!(
            f.request.hash(),
            Request::new(&authorization, 123, changed_mode)
                .unwrap()
                .hash()
        );
        assert!(Scope::new(Mode::Backtest, "s".into(), "DU1".into()).is_err());
        assert!(Scope::new(Mode::Paper, "s".into(), "../DU1".into()).is_err());
        let foreign = Scope::new(Mode::Paper, "s".into(), "DU2".into()).unwrap();
        assert!(Request::new(&authorization, 123, foreign).is_err());
    }
}
