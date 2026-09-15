//! Historical execution lane: quote -> simulated fills -> journal -> projection.
//! This owns no live broker capability. Strategy/market scheduling stays upstream.
use crate::fill_journal::{Batch, Committer, Publisher};
use arte_core::{
    content_hash,
    execution_events::Fill,
    execution_positions::{Key, Position, Projection},
    simulated_execution::{Amendment, Quote, Simulator},
    Error, Result,
};
use serde::Serialize;
struct Pending {
    quote_hash: String,
    fills: Vec<Fill>,
    applied: usize,
    current: Option<(usize, Committer)>,
}
#[derive(Debug, Clone, Serialize)]
pub struct Status {
    pub pending_fills: usize,
    pub applied_in_pending_quote: usize,
}
pub struct Runtime {
    simulator: Simulator,
    projection: Projection,
    pending: Option<Pending>,
    source: Option<arte_core::event_order::Scope>,
    last_source_quote: Option<(arte_core::events::Observation, u64, u64)>,
}
pub struct Submission<'a> {
    pub plan: &'a arte_core::decision_orders::Plan,
    pub funding: &'a arte_core::order_funding::Funding,
    pub portfolio: &'a arte_core::portfolio::Portfolio,
    pub cash_policy: &'a arte_core::order_funding::Policy,
    pub risk_policy: &'a arte_core::orders::RiskPolicy,
    pub bands: Option<&'a arte_core::orders::Bands>,
    pub session: &'a arte_core::orders::TradingSession,
    pub now_ns: u64,
    pub latency_ns: u64,
}
pub struct AmendmentSafety<'a> {
    pub session: &'a arte_core::orders::TradingSession,
    pub risk_policy: &'a arte_core::orders::RiskPolicy,
    pub bands: Option<&'a arte_core::orders::Bands>,
}
impl Runtime {
    pub fn new(
        simulator: Simulator,
        projection: Projection,
        maximum_pending_fills: usize,
    ) -> Result<Self> {
        if maximum_pending_fills == 0
            || maximum_pending_fills > 200000
            || simulator.maximum_quote_fills() > maximum_pending_fills
        {
            return Err(Error::Capacity(
                "simulation quote can exceed pending-fill budget".into(),
            ));
        }
        Ok(Self {
            simulator,
            projection,
            pending: None,
            source: None,
            last_source_quote: None,
        })
    }
    fn ready(&self) -> Result<()> {
        if self.pending.is_some() {
            return Err(Error::Unready(
                "simulation fills await journal/projection; retain next input".into(),
            ));
        }
        Ok(())
    }
    pub fn status(&self) -> Status {
        match &self.pending {
            None => Status {
                pending_fills: 0,
                applied_in_pending_quote: 0,
            },
            Some(p) => {
                let applied = p.applied + p.current.as_ref().map_or(0, |(_, c)| c.projected());
                Status {
                    pending_fills: p.fills.len() - applied,
                    applied_in_pending_quote: applied,
                }
            }
        }
    }
    pub fn position(&self, key: &Key) -> Option<&Position> {
        self.projection.position(key)
    }
    /// Bind the historical provider/session once. Changing source needs a new run.
    pub fn bind_source(&mut self, scope: arte_core::event_order::Scope) -> Result<()> {
        arte_core::quote_state::Book::new(scope)?;
        if scope.instrument != self.simulator.instrument()
            || self.source.is_some_and(|previous| previous != scope)
        {
            return Err(Error::Conflict("simulation source binding differs".into()));
        }
        self.source = Some(scope);
        Ok(())
    }
    /// Production historical input uses the same executable quote checks as live.
    /// Retry with the original replay sequence and modeled clock while fills await
    /// publication. A later quote cannot overtake those pending fills.
    pub fn quote_book(
        &mut self,
        book: &arte_core::quote_state::Book,
        sequence: u64,
        at_ns: u64,
        maximum_age_ns: u64,
    ) -> Result<()> {
        let scope = self
            .source
            .ok_or_else(|| Error::Unready("simulation source not bound".into()))?;
        let quote = Quote::from_book(
            book,
            scope,
            self.simulator.price_scale(),
            sequence,
            at_ns,
            maximum_age_ns,
        )?;
        let event = book.require_executable(at_ns, maximum_age_ns)?;
        if let Some((previous, previous_sequence, previous_at)) = &self.last_source_quote {
            if event.key == previous.key {
                if event.payload != previous.payload
                    || event.sip != previous.sip
                    || sequence != *previous_sequence
                    || at_ns != *previous_at
                {
                    return Err(Error::Conflict(
                        "source quote cannot acquire a new simulation identity".into(),
                    ));
                }
            } else if (event.sip.ns, event.key.sequence) <= (previous.sip.ns, previous.key.sequence)
            {
                return Err(Error::Invalid(
                    "simulation source quote order regressed".into(),
                ));
            }
        }
        self.quote(&quote)?;
        self.last_source_quote = Some((event.clone(), sequence, at_ns));
        Ok(())
    }
    #[cfg(test)]
    fn submit(
        &mut self,
        bracket: arte_core::orders::Bracket,
        now_ns: u64,
        latency_ns: u64,
    ) -> Result<()> {
        self.ready()?;
        self.simulator.submit(bracket, now_ns, latency_ns)
    }
    pub fn submit_reserved(&mut self, request: Submission<'_>) -> Result<()> {
        self.ready()?;
        validate_run(request.plan, self.simulator.run_id())?;
        request.session.validate(
            &request.plan.bracket,
            request.now_ns,
            request.bands,
            request.risk_policy,
        )?;
        let expected = arte_core::order_funding::requirements(
            request.plan,
            request.cash_policy,
            request.now_ns,
            request.session.require_phase(request.now_ns)?,
            request.bands,
            request.risk_policy,
        )?;
        if &expected != request.funding {
            return Err(Error::Conflict(
                "simulation funding differs from approved plan requirements".into(),
            ));
        }
        let b = &request.plan.bracket;
        request.portfolio.with_reservation(
            &b.account,
            &arte_core::portfolio::Reservation {
                command_id: b.command_id.clone(),
                instrument: b.instrument,
                cash_minor: expected.cash_minor,
            },
            request.now_ns,
            || {
                self.simulator
                    .submit(b.clone(), request.now_ns, request.latency_ns)
            },
        )
    }
    pub fn amend(
        &mut self,
        command: &str,
        revision: u64,
        at_ns: u64,
        amendment: &Amendment,
        safety: Option<AmendmentSafety<'_>>,
    ) -> Result<()> {
        self.ready()?;
        if let Amendment::ReplaceProtection { stop, target } = amendment {
            let safety = safety.ok_or_else(|| {
                Error::Unready("protection replacement requires session and risk evidence".into())
            })?;
            let order = self
                .simulator
                .positions()
                .iter()
                .find(|p| p.bracket.command_id == command)
                .ok_or_else(|| Error::Invalid("unknown simulation command".into()))?;
            safety.session.validate_protection(
                &order.bracket,
                (*stop, *target),
                at_ns,
                safety.bands,
                safety.risk_policy,
            )?;
        }
        self.simulator
            .acknowledge_amendment(command, revision, at_ns, amendment)
    }
    fn quote(&mut self, quote: &Quote) -> Result<()> {
        let hash = content_hash(quote)?;
        if let Some(p) = &self.pending {
            return if p.quote_hash == hash {
                Ok(())
            } else {
                Err(Error::Unready(
                    "next quote cannot overtake pending execution fills".into(),
                ))
            };
        }
        let fills = self.simulator.quote(quote)?;
        if !fills.is_empty() {
            self.pending = Some(Pending {
                quote_hash: hash,
                fills,
                applied: 0,
                current: None,
            });
        }
        Ok(())
    }
    /// Select the matching scope-owned publisher before committing a batch.
    pub fn next_scope_hash(&self) -> Result<Option<String>> {
        self.pending
            .as_ref()
            .map(|p| content_hash(&Key::from_fill(&p.fills[p.applied])?))
            .transpose()
    }
    /// One bounded contiguous-scope batch per call. False means no work remains.
    /// Cancellation preserves the committer and exact pending quote identity.
    pub async fn commit_next(&mut self, publisher: &mut impl Publisher) -> Result<bool> {
        let Some(p) = &mut self.pending else {
            return Ok(false);
        };
        if p.current.is_none() {
            let scope = Key::from_fill(&p.fills[p.applied])?;
            let mut end = p.applied + 1;
            while end < p.fills.len()
                && end - p.applied < 256
                && Key::from_fill(&p.fills[end])? == scope
            {
                end += 1;
            }
            let batch = Batch::new(p.fills[p.applied..end].to_vec())?;
            p.current = Some((end, Committer::new(batch)));
        }
        let (end, committer) = p.current.as_mut().unwrap();
        committer.commit(publisher, &mut self.projection).await?;
        p.applied = *end;
        p.current = None;
        if p.applied == p.fills.len() {
            self.pending = None;
        }
        Ok(true)
    }
}
fn validate_run(plan: &arte_core::decision_orders::Plan, run_id: &str) -> Result<()> {
    plan.validate_scope()?;
    if plan.scope.mode != arte_core::strategy_dispatch::Mode::Backtest
        || plan.scope.run_id != run_id
    {
        return Err(Error::Conflict(
            "historical execution requires its own backtest plan scope".into(),
        ));
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::orders::Bracket;
    #[test]
    fn reserved_submission_requires_matching_funding_and_held_cash() {
        let mut runtime = Runtime::new(
            Simulator::new_scoped("r", 1, 2, 2, 10000).unwrap(),
            Projection::new(2, 10, 4).unwrap(),
            4,
        )
        .unwrap();
        let plan = arte_core::decision_orders::Plan {
            scope: arte_core::strategy_dispatch::Scope {
                run_id: "r".into(),
                mode: arte_core::strategy_dispatch::Mode::Backtest,
                account: "a".into(),
                instrument: 1,
                strategy_instance: "s".into(),
                code_hash: "code".into(),
                config_hash: "config".into(),
            },
            decision_id: "d".into(),
            action_index: 0,
            bracket: bracket("a"),
        };
        validate_run(&plan, "r").unwrap();
        assert!(validate_run(&plan, "another-run").is_err());
        for mode in [
            arte_core::strategy_dispatch::Mode::Live,
            arte_core::strategy_dispatch::Mode::Paper,
        ] {
            let mut foreign = plan.clone();
            foreign.scope.mode = mode;
            assert!(validate_run(&foreign, "r").is_err());
        }
        let mut foreign = plan.clone();
        foreign.scope.account = "b".into();
        assert!(validate_run(&foreign, "r").is_err());
        let portfolio = arte_core::portfolio::Portfolio::new(BTreeMap::from([(
            "a".into(),
            arte_core::portfolio::Account {
                budget_minor: 1000,
                broker_available_minor: 1000,
                balance_at_ns: 0,
                max_balance_age_ns: 10,
                reservations: BTreeMap::new(),
            },
        )]))
        .unwrap();
        let cash = arte_core::order_funding::Policy {
            currency_scale: 2,
            maximum_order_cash_minor: 1000,
            maximum_order_risk_minor: 100,
            fee_reserve_minor: 1,
        };
        let risk = arte_core::orders::RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 10,
        };
        let funding =
            arte_core::order_funding::requirements(&plan, &cash, 1, false, None, &risk).unwrap();
        let session = session(true);
        let submit =
            |runtime: &mut Runtime, funding: &arte_core::order_funding::Funding, now_ns| {
                runtime.submit_reserved(Submission {
                    plan: &plan,
                    funding,
                    portfolio: &portfolio,
                    cash_policy: &cash,
                    risk_policy: &risk,
                    bands: None,
                    session: &session,
                    now_ns,
                    latency_ns: 0,
                })
            };
        assert!(submit(&mut runtime, &funding, 1).is_err());
        arte_core::order_funding::reserve(&portfolio, &plan, &cash, 1, false, None, &risk).unwrap();
        let mut wrong = funding.clone();
        wrong.cash_minor -= 1;
        assert!(submit(&mut runtime, &wrong, 1).is_err());
        assert!(submit(&mut runtime, &funding, 11).is_err());
        submit(&mut runtime, &funding, 1).unwrap();
        submit(&mut runtime, &funding, 1).unwrap();
        portfolio.release("a", "a").unwrap();
        assert!(submit(&mut runtime, &funding, 1).is_err());
    }
    use std::collections::BTreeMap;
    fn source_quote() -> arte_core::events::Observation {
        use arte_core::events::*;
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Quote,
                sequence: 8,
            },
            sip: SourceTime {
                ns: 1,
                precision_ns: 1,
            },
            participant: None,
            receipt: None,
            available_at_ns: 2,
            payload: Payload::Quote {
                bid: Decimal {
                    atoms: 99,
                    scale: 2,
                },
                ask: Decimal { atoms: 1, scale: 0 },
                bid_size: Decimal {
                    atoms: 10,
                    scale: 0,
                },
                ask_size: Decimal {
                    atoms: 10,
                    scale: 0,
                },
                bid_exchange: 1,
                ask_exchange: 1,
                conditions: vec![],
                indicators: vec![],
            },
        }
    }
    fn source_scope() -> arte_core::event_order::Scope {
        arte_core::event_order::Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        }
    }
    #[tokio::test]
    async fn replacement_requires_current_bands_and_preserves_previous_protection_on_failure() {
        let mut runtime = Runtime::new(
            Simulator::new_scoped("r", 1, 2, 2, 10000).unwrap(),
            Projection::new(2, 10, 4).unwrap(),
            4,
        )
        .unwrap();
        let mut order = bracket("a");
        order.deadline_ns = 10;
        runtime.submit(order, 0, 0).unwrap();
        runtime
            .quote(&Quote {
                sequence: 1,
                at_ns: 2,
                bid: 99,
                ask: 100,
                bid_size: 10,
                ask_size: 10,
            })
            .unwrap();
        let mut store = Store {
            fail: false,
            calls: 0,
            rows: BTreeMap::new(),
        };
        runtime.commit_next(&mut store).await.unwrap();
        runtime
            .quote(&Quote {
                sequence: 2,
                at_ns: 20,
                bid: 107,
                ask: 108,
                bid_size: 10,
                ask_size: 10,
            })
            .unwrap();
        let session = session(true);
        let policy = arte_core::orders::RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 10,
        };
        let bands = arte_core::orders::Bands {
            provider: 1,
            instrument: 1,
            session: 20260915,
            lower: 80,
            upper: 120,
            scale: 2,
            effective_at_ns: 20,
            available_at_ns: 20,
            official: true,
        };
        let replace = Amendment::ReplaceProtection {
            stop: 105,
            target: 110,
        };
        let before = runtime.simulator.checkpoint(10000).unwrap();
        assert!(runtime.amend("a", 1, 20, &replace, None).is_err());
        assert!(runtime
            .amend(
                "a",
                1,
                20,
                &replace,
                Some(AmendmentSafety {
                    session: &session,
                    risk_policy: &policy,
                    bands: None
                })
            )
            .is_err());
        let mut stale = bands.clone();
        stale.effective_at_ns = 9;
        stale.available_at_ns = 9;
        assert!(runtime
            .amend(
                "a",
                1,
                20,
                &replace,
                Some(AmendmentSafety {
                    session: &session,
                    risk_policy: &policy,
                    bands: Some(&stale)
                })
            )
            .is_err());
        let outside = Amendment::ReplaceProtection {
            stop: 105,
            target: 118,
        };
        assert!(runtime
            .amend(
                "a",
                1,
                20,
                &outside,
                Some(AmendmentSafety {
                    session: &session,
                    risk_policy: &policy,
                    bands: Some(&bands)
                })
            )
            .is_err());
        assert_eq!(runtime.simulator.checkpoint(10000).unwrap(), before);
        runtime
            .amend(
                "a",
                1,
                20,
                &replace,
                Some(AmendmentSafety {
                    session: &session,
                    risk_policy: &policy,
                    bands: Some(&bands),
                }),
            )
            .unwrap();
        assert_eq!(runtime.simulator.positions()[0].active_stop, 105);
        runtime
            .quote(&Quote {
                sequence: 3,
                at_ns: 21,
                bid: 104,
                ask: 105,
                bid_size: 10,
                ask_size: 10,
            })
            .unwrap();
        assert_eq!(runtime.status().pending_fills, 1);
        runtime.commit_next(&mut store).await.unwrap();
        assert_eq!(runtime.simulator.positions()[0].exit_filled, 1);
    }
    #[test]
    fn replacement_session_and_direction_are_checked_without_entry_deadline_reuse() {
        let session = session(true);
        let mut order = bracket("a");
        order.deadline_ns = 2;
        let risk = arte_core::orders::RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 10,
        };
        assert!(session
            .validate_protection(&order, (105, 110), 3, None, &risk)
            .is_ok());
        assert!(session
            .validate_protection(&order, (110, 105), 3, None, &risk)
            .is_err());
        assert!(session
            .validate_protection(&order, (105, 110), 50, None, &risk)
            .is_err());
        order.side = arte_core::orders::Side::Short;
        order.stop = Some(110);
        order.target = Some(90);
        assert!(session
            .validate_protection(&order, (95, 85), 3, None, &risk)
            .is_ok());
        assert!(session
            .validate_protection(&order, (85, 95), 3, None, &risk)
            .is_err());
    }
    #[tokio::test]
    async fn normalized_quote_uses_exact_prices_and_replay_clock_with_retry_safe_fills() {
        let mut runtime = Runtime::new(
            Simulator::new_scoped("r", 1, 2, 2, 10000).unwrap(),
            Projection::new(2, 10, 4).unwrap(),
            4,
        )
        .unwrap();
        runtime.submit(bracket("a"), 0, 0).unwrap();
        let mut book = arte_core::quote_state::Book::new(source_scope()).unwrap();
        book.bind_policy(crate::test_quote_policy()).unwrap();
        let raw = source_quote();
        let raw_hash = content_hash(&raw).unwrap();
        book.observe(&raw).unwrap();
        assert!(runtime.quote_book(&book, 1, 2, 10).is_err());
        runtime.bind_source(source_scope()).unwrap();
        runtime.bind_source(source_scope()).unwrap();
        assert!(runtime
            .bind_source(arte_core::event_order::Scope {
                provider: 2,
                ..source_scope()
            })
            .is_err());
        assert!(runtime.quote_book(&book, 1, 1, 10).is_err());
        assert!(runtime.quote_book(&book, 1, 11, 10).is_err());
        assert_eq!(runtime.status().pending_fills, 0);
        runtime.quote_book(&book, 1, 2, 10).unwrap();
        assert_eq!(runtime.status().pending_fills, 1);
        runtime.quote_book(&book, 1, 2, 10).unwrap();
        assert!(runtime.quote_book(&book, 1, 3, 10).is_err());
        let mut store = Store {
            fail: true,
            calls: 0,
            rows: BTreeMap::new(),
        };
        assert!(runtime.commit_next(&mut store).await.is_err());
        runtime.quote_book(&book, 1, 2, 10).unwrap();
        runtime.commit_next(&mut store).await.unwrap();
        let fill: Fill = serde_json::from_str(store.rows.values().next().unwrap()).unwrap();
        assert_eq!((fill.price, fill.at_ns, fill.sequence), (100, 2, 1));
        assert_eq!(
            runtime
                .position(&Key::from_fill(&fill).unwrap())
                .unwrap()
                .quantity,
            1
        );
        runtime.quote_book(&book, 1, 2, 10).unwrap();
        assert_eq!(runtime.status().pending_fills, 0);
        assert!(runtime.quote_book(&book, 2, 3, 10).is_err());
        assert_eq!(runtime.status().pending_fills, 0);
        assert_eq!(content_hash(book.latest().unwrap()).unwrap(), raw_hash);
        assert!(book.latest().unwrap().receipt.is_none());
    }
    #[test]
    fn normalized_quote_rejects_wrong_scope_precision_and_non_executable_book() {
        use arte_core::events::{Decimal, Payload};
        let convert = |event: arte_core::events::Observation, scale| {
            let mut book = arte_core::quote_state::Book::new(source_scope()).unwrap();
            book.bind_policy(crate::test_quote_policy()).unwrap();
            book.observe(&event).unwrap();
            Quote::from_book(&book, source_scope(), scale, 1, 2, 10)
        };
        assert!(convert(source_quote(), 1).is_err());
        let mut fractional = source_quote();
        if let Payload::Quote { ask_size, .. } = &mut fractional.payload {
            *ask_size = Decimal {
                atoms: 15,
                scale: 1,
            };
        }
        assert!(convert(fractional, 2).is_err());
        for bid in [100, 101] {
            let mut crossed = source_quote();
            if let Payload::Quote { bid: value, .. } = &mut crossed.payload {
                value.atoms = bid;
            }
            assert!(convert(crossed, 2).is_err());
        }
        let mut book = arte_core::quote_state::Book::new(source_scope()).unwrap();
        book.bind_policy(crate::test_quote_policy()).unwrap();
        book.observe(&source_quote()).unwrap();
        assert!(Quote::from_book(
            &book,
            arte_core::event_order::Scope {
                session: 20260916,
                ..source_scope()
            },
            2,
            1,
            2,
            10
        )
        .is_err());
        assert!(Quote::from_book(&book, source_scope(), 2, 0, 2, 10).is_err());
    }
    #[test]
    fn pinned_calendar_controls_historical_submission_before_simulator_mutation() {
        use arte_core::{
            decision_orders::Plan, order_funding, orders, portfolio, strategy_dispatch,
        };
        let plan = Plan {
            scope: strategy_dispatch::Scope {
                run_id: "r".into(),
                mode: strategy_dispatch::Mode::Backtest,
                account: "a".into(),
                instrument: 1,
                strategy_instance: "s".into(),
                code_hash: "code".into(),
                config_hash: "config".into(),
            },
            decision_id: "d".into(),
            action_index: 0,
            bracket: bracket("a"),
        };
        let portfolio = portfolio::Portfolio::new(BTreeMap::from([(
            "a".into(),
            portfolio::Account {
                budget_minor: 1000,
                broker_available_minor: 1000,
                balance_at_ns: 0,
                max_balance_age_ns: 100,
                reservations: BTreeMap::new(),
            },
        )]))
        .unwrap();
        let cash = order_funding::Policy {
            currency_scale: 2,
            maximum_order_cash_minor: 1000,
            maximum_order_risk_minor: 100,
            fee_reserve_minor: 1,
        };
        let risk = orders::RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 10,
        };
        // Deliberately reserve through the lower-level geometry path. This must
        // never bypass the calendar at the actual execution adapter boundary.
        let funding =
            order_funding::reserve(&portfolio, &plan, &cash, 1, false, None, &risk).unwrap();
        let bands = orders::Bands {
            provider: 1,
            instrument: 1,
            session: 20260915,
            lower: 80,
            upper: 120,
            scale: 2,
            effective_at_ns: 20,
            available_at_ns: 20,
            official: true,
        };
        for (at, extended, band, accepted) in [
            (20, true, None, false),
            (20, false, Some(&bands), true),
            (1, false, None, false),
            (1, true, None, true),
            (30, false, None, false),
            (30, true, None, true),
            (50, true, None, false),
            (0, true, None, false),
        ] {
            let mut runtime = Runtime::new(
                Simulator::new_scoped("r", 1, 2, 2, 10000).unwrap(),
                Projection::new(2, 10, 4).unwrap(),
                4,
            )
            .unwrap();
            let session = session(extended);
            let result = runtime.submit_reserved(Submission {
                plan: &plan,
                funding: &funding,
                portfolio: &portfolio,
                cash_policy: &cash,
                risk_policy: &risk,
                bands: band,
                session: &session,
                now_ns: at,
                latency_ns: 0,
            });
            assert_eq!(
                result.is_ok(),
                accepted,
                "at={at}, extended={extended}: {result:?}"
            );
            runtime
                .quote(&Quote {
                    sequence: 1,
                    at_ns: at + 1,
                    bid: 99,
                    ask: 100,
                    bid_size: 10,
                    ask_size: 10,
                })
                .unwrap();
            assert_eq!(runtime.status().pending_fills, usize::from(accepted));
        }
    }
    fn session(extended: bool) -> arte_core::orders::TradingSession {
        let session = arte_core::session::Session {
            exchange: "XNYS".into(),
            session: 20260915,
            previous_trading_session: 20260914,
            extended: arte_core::coverage::Interval { start: 1, end: 50 },
            regular: arte_core::coverage::Interval { start: 20, end: 30 },
            available_at_ns: 0,
            source_manifest_hash: "a".repeat(64),
        };
        let hash = content_hash(&session).unwrap();
        arte_core::orders::TradingSession::new(session, hash, 0, extended).unwrap()
    }
    fn bracket(account: &str) -> Bracket {
        Bracket {
            command_id: account.into(),
            account: account.into(),
            instrument: 1,
            side: arte_core::orders::Side::Long,
            quantity: 1,
            entry: 100,
            price_scale: 2,
            stop: Some(90),
            target: Some(110),
            tick: 1,
            deadline_ns: 100,
        }
    }
    struct Store {
        fail: bool,
        calls: usize,
        rows: BTreeMap<String, String>,
    }
    impl Publisher for Store {
        async fn publish(&mut self, batch: &Batch) -> Result<BTreeMap<String, String>> {
            self.calls += 1;
            self.rows.extend(batch.rows().clone());
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("simulated ambiguous journal write".into()));
            }
            Ok(batch.rows().clone())
        }
    }
    #[tokio::test]
    async fn pending_quote_blocks_overtaking_and_accounts_commit_separately() {
        let simulator = Simulator::new_scoped("r", 1, 2, 2, 10000).unwrap();
        let mut runtime = Runtime::new(simulator, Projection::new(2, 10, 4).unwrap(), 4).unwrap();
        runtime.submit(bracket("a"), 0, 0).unwrap();
        runtime.submit(bracket("b"), 0, 0).unwrap();
        let q = Quote {
            sequence: 1,
            at_ns: 1,
            bid: 99,
            ask: 100,
            bid_size: 10,
            ask_size: 10,
        };
        runtime.quote(&q).unwrap();
        assert_eq!(runtime.status().pending_fills, 2);
        let first_scope = runtime.next_scope_hash().unwrap().unwrap();
        let mut store = Store {
            fail: true,
            calls: 0,
            rows: BTreeMap::new(),
        };
        assert!(runtime.commit_next(&mut store).await.is_err());
        let fill: Fill = serde_json::from_str(store.rows.values().next().unwrap()).unwrap();
        let key = Key::from_fill(&fill).unwrap();
        assert!(runtime.position(&key).is_none());
        runtime.quote(&q).unwrap();
        let mut next = q.clone();
        next.sequence += 1;
        next.at_ns += 1;
        assert!(runtime.quote(&next).is_err());
        assert!(runtime
            .amend("a", 1, 1, &Amendment::ExitPosition, None)
            .is_err());
        runtime.commit_next(&mut store).await.unwrap();
        assert_eq!(runtime.position(&key).unwrap().quantity, 1);
        assert_eq!(runtime.status().pending_fills, 1);
        assert_ne!(runtime.next_scope_hash().unwrap().unwrap(), first_scope);
        runtime.commit_next(&mut store).await.unwrap();
        assert!(!runtime.commit_next(&mut store).await.unwrap());
        assert_eq!(runtime.status().pending_fills, 0);
        assert_eq!(store.calls, 3);
        runtime
            .amend("a", 1, 1, &Amendment::ExitPosition, None)
            .unwrap();
        runtime.quote(&next).unwrap();
        runtime.commit_next(&mut store).await.unwrap();
        assert_eq!(runtime.position(&key).unwrap().quantity, 0);
    }
    #[test]
    fn pending_budget_covers_worst_case_quote_before_acceptance() {
        assert!(Runtime::new(
            Simulator::new_scoped("r", 1, 2, 2, 10000).unwrap(),
            Projection::new(2, 10, 4).unwrap(),
            3
        )
        .is_err());
    }
}
