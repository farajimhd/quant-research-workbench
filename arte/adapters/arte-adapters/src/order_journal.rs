//! Publish the complete authorization before advancing the order ledger.
use arte_core::{
    orders::{Authorization, OrderLedger, OrderState},
    Error, Result,
};
use std::future::Future;

pub trait Publisher {
    fn append(
        &mut self,
        authorization: &Authorization,
    ) -> impl Future<Output = Result<Authorization>> + Send;
}

/// Cancellation, ambiguous writes and conflicting readback leave Authorized
/// unchanged. A retry publishes the same stable slot and exact envelope.
pub async fn commit(
    ledger: &mut OrderLedger,
    id: &str,
    publisher: &mut impl Publisher,
) -> Result<()> {
    if ledger.record(id)?.state != OrderState::Authorized {
        return Err(Error::Unready(
            "order is not awaiting authorization publication".into(),
        ));
    }
    let prepared = ledger.authorization(id)?;
    let readback = publisher.append(&prepared).await?;
    ledger.acknowledge_authorization(id, &readback)
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{coverage::Interval, events::EventKind, orders::*, session::Session};
    fn prepared() -> OrderLedger {
        let session = Session {
            exchange: "XNYS".into(),
            session: 20260915,
            previous_trading_session: 20260914,
            extended: Interval { start: 1, end: 100 },
            regular: Interval { start: 50, end: 80 },
            available_at_ns: 1,
            source_manifest_hash: "a".repeat(64),
        };
        let hash = arte_core::content_hash(&session).unwrap();
        let session = TradingSession::new(session, hash, 1, true).unwrap();
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
                    account: "paper".into(),
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
                &RiskPolicy {
                    band_provider: 1,
                    band_session: 20260915,
                    band_buffer_ticks: 3,
                    max_band_age_ns: 10,
                },
                gate.at(10),
            )
            .unwrap();
        ledger
    }
    struct Store {
        saved: Option<Authorization>,
        fail: bool,
        corrupt: bool,
    }
    impl Publisher for Store {
        async fn append(&mut self, authorization: &Authorization) -> Result<Authorization> {
            if let Some(old) = &self.saved {
                assert_eq!(old, authorization);
            }
            self.saved = Some(authorization.clone());
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("ambiguous write".into()));
            }
            let mut result = authorization.clone();
            if self.corrupt {
                result.bracket.quantity += 1;
            }
            Ok(result)
        }
    }
    #[tokio::test]
    async fn ambiguous_write_and_wrong_readback_cannot_advance_ledger() {
        let mut ledger = prepared();
        let hash = ledger.envelope_hash("c").unwrap();
        let mut store = Store {
            saved: None,
            fail: true,
            corrupt: false,
        };
        assert!(commit(&mut ledger, "c", &mut store).await.is_err());
        store.corrupt = true;
        assert!(commit(&mut ledger, "c", &mut store).await.is_err());
        assert_eq!(ledger.record("c").unwrap().state, OrderState::Authorized);
        assert_eq!(ledger.envelope_hash("c").unwrap(), hash);
        store.corrupt = false;
        commit(&mut ledger, "c", &mut store).await.unwrap();
        assert_eq!(ledger.record("c").unwrap().state, OrderState::Durable);
        assert!(commit(&mut ledger, "c", &mut store).await.is_err());
    }
    struct InterruptedStore {
        saved: Option<Authorization>,
    }
    impl Publisher for InterruptedStore {
        async fn append(&mut self, authorization: &Authorization) -> Result<Authorization> {
            self.saved = Some(authorization.clone());
            std::future::pending().await
        }
    }
    #[tokio::test]
    async fn cancellation_after_write_preserves_exact_retry() {
        let mut ledger = prepared();
        let mut interrupted = InterruptedStore { saved: None };
        assert!(tokio::time::timeout(
            std::time::Duration::from_millis(2),
            commit(&mut ledger, "c", &mut interrupted),
        )
        .await
        .is_err());
        assert!(interrupted.saved.is_some());
        assert_eq!(ledger.record("c").unwrap().state, OrderState::Authorized);
        let mut retry = Store {
            saved: interrupted.saved,
            fail: false,
            corrupt: false,
        };
        commit(&mut ledger, "c", &mut retry).await.unwrap();
        assert_eq!(ledger.record("c").unwrap().state, OrderState::Durable);
    }
}
