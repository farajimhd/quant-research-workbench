//! Exact broker request boundary. Constructors and offline tests perform no I/O.
use arte_core::{
    orders::{submission::SendPermit, Authorization, Bands, RiskPolicy, TradingSession},
    Error, Result,
};
use std::future::Future;
pub mod workflow;

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
    pub fn session_id(&self) -> &str {
        &self.session_id
    }
    pub fn mode(&self) -> arte_core::strategy_dispatch::Mode {
        self.mode
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
    fn send(&mut self, request: AuthorizedRequest)
        -> impl Future<Output = Result<Response>> + Send;
}
pub struct Safety<'a> {
    pub now_utc_ns: u64,
    pub now_monotonic_ns: u64,
    pub session: &'a TradingSession,
    pub bands: Option<&'a Bands>,
    pub policy: &'a RiskPolicy,
    pub market: arte_core::exposure::Check<'a>,
}
pub struct Response {
    pub status: u16,
    pub body: Vec<u8>,
}
pub enum Delivery {
    /// Raw bounded evidence, not proof all bracket legs are working.
    Response(Response),
    /// Do not automatically resend. Persist the outcome and reconcile.
    Unknown(String),
}
impl Delivery {
    /// Retain this pending value until publication succeeds; never rebuild it
    /// with a newer timestamp after an ambiguous write.
    pub fn into_pending(
        self,
        marker: &arte_core::orders::submission::Marker,
        observed_at_ns: u64,
    ) -> Result<arte_core::orders::outcome::Pending> {
        use arte_core::orders::outcome::{Observation, Pending};
        let observation = match self {
            Self::Response(response) => Observation::Response {
                status: response.status,
                body: response.body,
            },
            Self::Unknown(reason) => Observation::Unknown { reason },
        };
        Pending::new(marker, observed_at_ns, observation)
    }
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
        Ok(response) if response.body.len() <= 64 * 1024 => Delivery::Response(response),
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
    struct Writer {
        saved: Option<arte_core::orders::outcome::Record>,
        fail: bool,
        pause: bool,
    }
    impl crate::order_journal::OutcomePublisher for Writer {
        async fn append_outcome(
            &mut self,
            _: &Authorization,
            _: &arte_core::orders::submission::Marker,
            record: &arte_core::orders::outcome::Record,
        ) -> Result<arte_core::orders::outcome::Record> {
            if let Some(saved) = &self.saved {
                assert_eq!(saved, record);
            }
            self.saved = Some(record.clone());
            if self.pause {
                std::future::pending::<()>().await;
            }
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("ambiguous outcome write".into()));
            }
            Ok(record.clone())
        }
    }
    fn mock(scope: Scope) -> Mock {
        Mock {
            scope,
            ready: true,
            calls: 0,
            fail: false,
            oversized: false,
            sent: None,
        }
    }
    #[tokio::test]
    async fn workflow_publication_retry_never_resends_and_preserves_observation() {
        let f = fixture();
        let mut transport = mock(f.request.scope.clone());
        let mut attempt =
            workflow::Attempt::new(f.ledger.authorization("c").unwrap(), f.request, f.permit)
                .unwrap();
        attempt
            .send(
                &mut transport,
                || {
                    Ok(Safety {
                        now_utc_ns: 12,
                        now_monotonic_ns: 12,
                        session: &f.session,
                        bands: None,
                        policy: &f.policy,
                        market: f.gate.at(12),
                    })
                },
                || 13,
            )
            .await
            .unwrap();
        let original = attempt.observed_record().unwrap().clone();
        let mut writer = Writer {
            saved: None,
            fail: true,
            pause: false,
        };
        assert!(attempt.publish(&mut writer).await.is_err());
        assert_eq!(attempt.phase(), workflow::Phase::Publishing);
        assert!(attempt
            .send(
                &mut transport,
                || panic!("must not check a second send"),
                || 99
            )
            .await
            .is_err());
        let committed = attempt.publish(&mut writer).await.unwrap();
        assert_eq!(committed.record(), &original);
        assert_eq!(attempt.phase(), workflow::Phase::Complete);
        assert!(attempt.publish(&mut writer).await.is_err());
        assert_eq!(transport.calls, 1);
    }
    #[tokio::test]
    async fn workflow_cancelled_publication_retries_the_same_record() {
        let f = fixture();
        let mut transport = mock(f.request.scope.clone());
        let mut attempt =
            workflow::Attempt::new(f.ledger.authorization("c").unwrap(), f.request, f.permit)
                .unwrap();
        attempt
            .send(
                &mut transport,
                || {
                    Ok(Safety {
                        now_utc_ns: 12,
                        now_monotonic_ns: 12,
                        session: &f.session,
                        bands: None,
                        policy: &f.policy,
                        market: f.gate.at(12),
                    })
                },
                || 13,
            )
            .await
            .unwrap();
        let original = attempt.observed_record().unwrap().clone();
        let mut writer = Writer {
            saved: None,
            fail: false,
            pause: true,
        };
        assert!(tokio::time::timeout(
            std::time::Duration::from_millis(2),
            attempt.publish(&mut writer)
        )
        .await
        .is_err());
        assert_eq!(attempt.observed_record(), Some(&original));
        writer.pause = false;
        assert_eq!(
            attempt.publish(&mut writer).await.unwrap().record(),
            &original
        );
        assert_eq!(transport.calls, 1);
    }
    struct Hanging {
        scope: Scope,
        calls: usize,
    }
    impl Transport for Hanging {
        fn scope(&self) -> &Scope {
            &self.scope
        }
        fn require_ready(&self, _: u64) -> Result<()> {
            Ok(())
        }
        async fn send(&mut self, _: AuthorizedRequest) -> Result<Response> {
            self.calls += 1;
            std::future::pending().await
        }
    }
    #[tokio::test]
    async fn workflow_cancelled_send_requires_explicit_unknown_observation() {
        let f = fixture();
        let mut transport = Hanging {
            scope: f.request.scope.clone(),
            calls: 0,
        };
        let mut attempt =
            workflow::Attempt::new(f.ledger.authorization("c").unwrap(), f.request, f.permit)
                .unwrap();
        assert!(tokio::time::timeout(
            std::time::Duration::from_millis(2),
            attempt.send(
                &mut transport,
                || Ok(Safety {
                    now_utc_ns: 12,
                    now_monotonic_ns: 12,
                    session: &f.session,
                    bands: None,
                    policy: &f.policy,
                    market: f.gate.at(12)
                }),
                || panic!("response not received")
            )
        )
        .await
        .is_err());
        assert_eq!(attempt.phase(), workflow::Phase::Sending);
        assert!(attempt
            .send(&mut transport, || panic!("no resend"), || 99)
            .await
            .is_err());
        attempt.record_interruption(14).unwrap();
        assert!(attempt.record_interruption(15).is_err());
        let mut writer = Writer {
            saved: None,
            fail: false,
            pause: false,
        };
        let committed = attempt.publish(&mut writer).await.unwrap();
        assert!(matches!(
            committed.record().observation,
            arte_core::orders::outcome::Observation::Unknown { .. }
        ));
        assert_eq!(committed.record().observed_at_ns, 14);
        assert_eq!(transport.calls, 1);
    }
    #[tokio::test]
    async fn workflow_invalid_observation_clock_keeps_raw_evidence() {
        let f = fixture();
        let mut transport = mock(f.request.scope.clone());
        let mut attempt =
            workflow::Attempt::new(f.ledger.authorization("c").unwrap(), f.request, f.permit)
                .unwrap();
        attempt
            .send(
                &mut transport,
                || {
                    Ok(Safety {
                        now_utc_ns: 12,
                        now_monotonic_ns: 12,
                        session: &f.session,
                        bands: None,
                        policy: &f.policy,
                        market: f.gate.at(12),
                    })
                },
                || 9,
            )
            .await
            .unwrap();
        let original = attempt.observed_record().unwrap().clone();
        let mut writer = Writer {
            saved: None,
            fail: false,
            pause: false,
        };
        assert!(attempt.publish(&mut writer).await.is_err());
        assert_eq!(attempt.phase(), workflow::Phase::Observed);
        assert_eq!(attempt.observed_record(), Some(&original));
        assert!(writer.saved.is_none());
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
        async fn send(&mut self, request: AuthorizedRequest) -> Result<Response> {
            self.calls += 1;
            self.sent = Some((request.path().into(), request.body().into()));
            if self.fail {
                return Err(Error::Unready("transport lost after write".into()));
            }
            Ok(Response {
                status: 200,
                body: if self.oversized {
                    vec![0; 64 * 1024 + 1]
                } else {
                    b"[]".to_vec()
                },
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
                        assert_eq!(body.status, 200);
                        assert_eq!(body.body, b"[]");
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
