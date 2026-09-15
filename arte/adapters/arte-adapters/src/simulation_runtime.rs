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
}
pub struct Submission<'a> {
    pub plan: &'a arte_core::decision_orders::Plan,
    pub funding: &'a arte_core::order_funding::Funding,
    pub portfolio: &'a arte_core::portfolio::Portfolio,
    pub cash_policy: &'a arte_core::order_funding::Policy,
    pub risk_policy: &'a arte_core::orders::RiskPolicy,
    pub bands: Option<&'a arte_core::orders::Bands>,
    pub regular: bool,
    pub now_ns: u64,
    pub latency_ns: u64,
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
        let expected = arte_core::order_funding::requirements(
            request.plan,
            request.cash_policy,
            request.now_ns,
            request.regular,
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
    ) -> Result<()> {
        self.ready()?;
        self.simulator
            .acknowledge_amendment(command, revision, at_ns, amendment)
    }
    pub fn quote(&mut self, quote: &Quote) -> Result<()> {
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
        let submit =
            |runtime: &mut Runtime, funding: &arte_core::order_funding::Funding, now_ns| {
                runtime.submit_reserved(Submission {
                    plan: &plan,
                    funding,
                    portfolio: &portfolio,
                    cash_policy: &cash,
                    risk_policy: &risk,
                    bands: None,
                    regular: false,
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
        assert!(runtime.amend("a", 1, 1, &Amendment::ExitPosition).is_err());
        runtime.commit_next(&mut store).await.unwrap();
        assert_eq!(runtime.position(&key).unwrap().quantity, 1);
        assert_eq!(runtime.status().pending_fills, 1);
        assert_ne!(runtime.next_scope_hash().unwrap().unwrap(), first_scope);
        runtime.commit_next(&mut store).await.unwrap();
        assert!(!runtime.commit_next(&mut store).await.unwrap());
        assert_eq!(runtime.status().pending_fills, 0);
        assert_eq!(store.calls, 3);
        runtime.amend("a", 1, 1, &Amendment::ExitPosition).unwrap();
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
