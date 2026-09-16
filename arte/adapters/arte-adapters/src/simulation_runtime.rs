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
    owners: std::collections::BTreeMap<String, arte_core::strategy_dispatch::Scope>,
    reservations: std::collections::BTreeMap<String, arte_core::portfolio::Reservation>,
    released: std::collections::BTreeSet<String>,
    costs: Option<arte_core::simulation_costs::Pinned>,
    fees: std::collections::BTreeMap<String, u64>,
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
            owners: Default::default(),
            reservations: Default::default(),
            released: Default::default(),
            costs: None,
            fees: Default::default(),
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
    pub(crate) fn bind_costs(&mut self, costs: arte_core::simulation_costs::Pinned) -> Result<()> {
        self.ready()?;
        if self.costs.is_some()
            || self.last_source_quote.is_some()
            || costs.run_id() != self.simulator.run_id()
        {
            return Err(Error::Conflict(
                "cost binding must precede execution and match its run".into(),
            ));
        }
        self.costs = Some(costs);
        Ok(())
    }
    pub fn fees_minor(&self, command: &str) -> Result<Option<u64>> {
        if self.costs.is_none() {
            return Err(Error::Unready("simulation cost model unbound".into()));
        }
        Ok(self.fees.get(command).copied())
    }
    pub(crate) fn require_new_playback(
        &self,
        run: &arte_core::market_structure::scheduler::playback::accounts::Run,
    ) -> Result<()> {
        self.ready()?;
        if self.last_source_quote.is_some()
            || self.source != Some(run.market()?.source_scope())
            || run
                .scopes()
                .first()
                .is_none_or(|scope| scope.run_id != self.simulator.run_id())
        {
            return Err(Error::Conflict(
                "execution is not a matching unused playback lane".into(),
            ));
        }
        Ok(())
    }
    pub(crate) fn require_committed_fills(&self) -> Result<()> {
        self.ready()
    }
    pub(crate) fn advance_playback_clock(&mut self, at_ns: u64) -> Result<()> {
        self.ready()?;
        self.simulator.advance_clock(at_ns)
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
    /// Consume only a released quote from this exact backtest run. The pending
    /// boundary supplies simulation identity and clock; raw receive times stay raw.
    pub fn quote_playback(
        &mut self,
        run: &arte_core::market_structure::scheduler::playback::accounts::Run,
        maximum_age_ns: u64,
    ) -> Result<()> {
        if run
            .scopes()
            .first()
            .is_none_or(|scope| scope.run_id != self.simulator.run_id())
        {
            return Err(Error::Conflict("simulation and playback run differ".into()));
        }
        let boundary = run
            .pending()?
            .ok_or_else(|| Error::Unready("no playback quote boundary".into()))?;
        let arte_core::market_structure::scheduler::Kind::Quote { observation } = boundary.kind
        else {
            return Err(Error::Unready(
                "simulation requires a quote boundary".into(),
            ));
        };
        let book = run.quotes()?;
        if book.latest() != Some(observation) {
            return Err(Error::Conflict(
                "playback quote book differs from boundary".into(),
            ));
        }
        self.quote_book(
            book,
            boundary.sequence,
            boundary.evaluated_at_ns,
            maximum_age_ns,
        )
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
        let command = &request.plan.bracket.command_id;
        if self.released.contains(command) {
            return Err(Error::Conflict(
                "cannot resubmit released simulation command".into(),
            ));
        }
        if self
            .owners
            .get(command)
            .is_some_and(|scope| scope != &request.plan.scope)
            || (self
                .simulator
                .positions()
                .iter()
                .any(|order| &order.bracket.command_id == command)
                && !self.owners.contains_key(command))
        {
            return Err(Error::Conflict(
                "simulation command ownership differs or is unknown".into(),
            ));
        }
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
        if self.reservations.get(command).is_some_and(|r| {
            r.cash_minor != expected.cash_minor || r.instrument != request.plan.bracket.instrument
        }) {
            return Err(Error::Conflict(
                "original simulation reservation changed".into(),
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
        )?;
        self.owners
            .insert(command.clone(), request.plan.scope.clone());
        self.reservations.insert(
            command.clone(),
            arte_core::portfolio::Reservation {
                command_id: command.clone(),
                instrument: b.instrument,
                cash_minor: expected.cash_minor,
            },
        );
        Ok(())
    }
    /// No fills means no trade cash or fill costs to settle. Filled orders must
    /// retain funding until the modeled cash/cost settlement path acknowledges them.
    pub(crate) fn release_unfilled_reservation(
        &mut self,
        command: &str,
        portfolio: &arte_core::portfolio::Portfolio,
    ) -> Result<bool> {
        self.ready()?;
        let owner = self
            .owners
            .get(command)
            .ok_or_else(|| Error::Unready("reservation owner missing".into()))?;
        let expected = self
            .reservations
            .get(command)
            .ok_or_else(|| Error::Unready("original reservation missing".into()))?;
        let order = self
            .simulator
            .positions()
            .iter()
            .find(|p| p.bracket.command_id == command)
            .ok_or_else(|| Error::Unready("reservation order missing".into()))?;
        if !order.entry_cancelled || order.entry_filled != 0 || order.exit_filled != 0 {
            return Err(Error::Unready(
                "reservation still protects entry or filled exposure".into(),
            ));
        }
        if self.released.contains(command) {
            return Ok(false);
        }
        if !portfolio.release_matching(&owner.account, expected)? {
            return Err(Error::Unready(
                "owned reservation missing before release".into(),
            ));
        }
        self.released.insert(command.into());
        Ok(true)
    }
    pub(crate) fn cancel_entries_for(
        &mut self,
        scope: &arte_core::strategy_dispatch::Scope,
        at_ns: u64,
    ) -> Result<usize> {
        let commands = self.owned_commands(scope, false)?;
        self.simulator.cancel_entries(&commands, at_ns)
    }
    pub(crate) fn exit_for(
        &mut self,
        scope: &arte_core::strategy_dispatch::Scope,
        quantity: u64,
        at_ns: u64,
    ) -> Result<usize> {
        let commands = self.owned_commands(scope, true)?;
        self.simulator.exit_entries(&commands, quantity, at_ns)
    }
    fn owned_commands(
        &self,
        scope: &arte_core::strategy_dispatch::Scope,
        include_positions: bool,
    ) -> Result<Vec<String>> {
        self.ready()?;
        arte_core::strategy_dispatch::State::new(scope.clone())?;
        if scope.mode != arte_core::strategy_dispatch::Mode::Backtest
            || scope.run_id != self.simulator.run_id()
            || scope.instrument != self.simulator.instrument()
        {
            return Err(Error::Conflict(
                "execution action escaped simulation scope".into(),
            ));
        }
        let mut commands = Vec::new();
        for order in self.simulator.positions() {
            let pending_entry =
                !order.entry_cancelled && order.entry_filled < order.bracket.quantity;
            let held = order.entry_filled > order.exit_filled && !order.exit_requested;
            if order.bracket.account != scope.account
                || !(pending_entry || include_positions && held)
            {
                continue;
            }
            let owner = self.owners.get(&order.bracket.command_id).ok_or_else(|| {
                Error::Unready("cannot control order with unknown strategy ownership".into())
            })?;
            if owner == scope {
                commands.push(order.bracket.command_id.clone());
            }
        }
        Ok(commands)
    }
    pub(crate) fn replace_for(
        &mut self,
        scope: &arte_core::strategy_dispatch::Scope,
        action: &arte_core::strategy_dispatch::Action,
        quantity: u64,
        at_ns: u64,
        safety: AmendmentSafety<'_>,
    ) -> Result<usize> {
        use arte_core::strategy_dispatch::Action;
        let (price, proposed_at, stop) = match action {
            Action::ReplaceStop(p) => (p.price, p.at_ns, true),
            Action::ReplaceTarget(p) => (p.target.price(), p.at_ns, false),
            _ => return Err(Error::Invalid("not a protection action".into())),
        };
        if !price.is_finite() || price <= 0. || proposed_at > at_ns || quantity == 0 {
            return Err(Error::Invalid(
                "invalid protection proposal price, time or quantity".into(),
            ));
        }
        let price = arte_core::events::Decimal::parse(&price.to_string())?
            .atoms_at_scale(self.simulator.price_scale())?;
        let commands: std::collections::BTreeSet<_> =
            self.owned_commands(scope, true)?.into_iter().collect();
        let mut replacements = Vec::new();
        let mut held = 0_u64;
        for order in self.simulator.positions() {
            if !commands.contains(&order.bracket.command_id)
                || order.entry_filled == order.exit_filled
            {
                continue;
            }
            held = held
                .checked_add(order.entry_filled - order.exit_filled)
                .ok_or_else(|| Error::Capacity("protection quantity overflow".into()))?;
            let prices = if stop {
                (price, order.active_target)
            } else {
                (order.active_stop, price)
            };
            safety.session.validate_protection(
                &order.bracket,
                prices,
                at_ns,
                safety.bands,
                safety.risk_policy,
            )?;
            replacements.push((order.bracket.command_id.clone(), prices.0, prices.1));
        }
        if held != quantity {
            return Err(Error::Conflict(
                "protection quantity differs from owned exposure".into(),
            ));
        }
        self.simulator
            .replace_protection_batch(&replacements, at_ns)
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
        let mut fees = std::collections::BTreeMap::new();
        if let Some(costs) = &self.costs {
            for fill in &p.fills[p.applied..*end] {
                let charge = costs.charge(fill)?;
                let total = fees
                    .entry(fill.command_id.clone())
                    .or_insert_with(|| self.fees.get(&fill.command_id).copied().unwrap_or(0));
                *total = total
                    .checked_add(charge.fee_minor)
                    .ok_or_else(|| Error::Capacity("cumulative simulation fees overflow".into()))?;
            }
        }
        committer.commit(publisher, &mut self.projection).await?;
        self.fees.extend(fees);
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
    fn exits_select_owned_filled_positions_and_unknown_ownership_blocks() {
        use arte_core::strategy_dispatch::{Mode, Scope};
        let scope = Scope {
            run_id: "r".into(),
            mode: Mode::Backtest,
            account: "a".into(),
            instrument: 1,
            strategy_instance: "one".into(),
            code_hash: "c".into(),
            config_hash: "f".into(),
        };
        let mut simulator = Simulator::new_scoped("r", 1, 2, 3, 10000).unwrap();
        let one = bracket("a");
        let quantity = one.quantity;
        let mut two = one.clone();
        two.command_id = "two".into();
        simulator.submit(one, 0, 0).unwrap();
        simulator.submit(two, 0, 0).unwrap();
        simulator
            .quote(&Quote {
                sequence: 1,
                at_ns: 1,
                bid: 99,
                ask: 100,
                bid_size: 100,
                ask_size: 100,
            })
            .unwrap();
        // Isolate ownership dispatch; fill journaling is covered by controller tests.
        let mut runtime = Runtime::new(simulator, Projection::new(2, 10, 6).unwrap(), 6).unwrap();
        runtime.owners.insert("a".into(), scope.clone());
        let before = runtime.simulator.checkpoint(100000).unwrap();
        assert!(runtime.exit_for(&scope, quantity, 1).is_err());
        assert_eq!(before, runtime.simulator.checkpoint(100000).unwrap());
        let mut other = scope.clone();
        other.strategy_instance = "two".into();
        runtime.owners.insert("two".into(), other);
        assert!(runtime.exit_for(&scope, quantity + 1, 1).is_err());
        assert_eq!(before, runtime.simulator.checkpoint(100000).unwrap());
        use arte_core::{
            strategy_dispatch::Action,
            strategy_protection::{ActiveTarget, TargetProposal},
        };
        let session = session(true);
        let risk = arte_core::orders::RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 2,
            max_band_age_ns: 10,
        };
        let safety = || AmendmentSafety {
            session: &session,
            risk_policy: &risk,
            bands: None,
        };
        let target = |price, at_ns| {
            Action::ReplaceTarget(TargetProposal {
                target: ActiveTarget::Official { price },
                triggering_breakout: None,
                at_ns,
            })
        };
        for (action, qty) in [
            (target(1.15, 2), quantity),
            (target(1.151, 1), quantity),
            (target(1.15, 1), quantity + 1),
        ] {
            assert!(runtime
                .replace_for(&scope, &action, qty, 1, safety())
                .is_err());
            assert_eq!(before, runtime.simulator.checkpoint(100000).unwrap());
        }
        assert_eq!(
            runtime
                .replace_for(&scope, &target(1.15, 1), quantity, 1, safety())
                .unwrap(),
            1
        );
        assert_eq!(runtime.simulator.positions()[0].active_target, 115);
        assert_eq!(runtime.simulator.positions()[1].active_target, 110);
        // Regular-hours replacement cannot proceed without official bands.
        runtime.simulator.advance_clock(21).unwrap();
        let before = runtime.simulator.checkpoint(100000).unwrap();
        assert!(runtime
            .replace_for(&scope, &target(1.16, 21), quantity, 21, safety())
            .is_err());
        assert_eq!(before, runtime.simulator.checkpoint(100000).unwrap());
        assert_eq!(runtime.exit_for(&scope, quantity, 21).unwrap(), 1);
        assert!(runtime.simulator.positions()[0].exit_requested);
        assert!(!runtime.simulator.positions()[1].exit_requested);
    }
    #[test]
    fn cancellation_is_strategy_scoped_and_unknown_ownership_blocks_atomically() {
        use arte_core::strategy_dispatch::{Mode, Scope};
        let scope = Scope {
            run_id: "r".into(),
            mode: Mode::Backtest,
            account: "a".into(),
            instrument: 1,
            strategy_instance: "one".into(),
            code_hash: "c".into(),
            config_hash: "f".into(),
        };
        let mut runtime = Runtime::new(
            Simulator::new_scoped("r", 1, 2, 3, 10000).unwrap(),
            Projection::new(2, 10, 6).unwrap(),
            6,
        )
        .unwrap();
        let one = bracket("a");
        let mut two = one.clone();
        two.command_id = "two".into();
        runtime.submit(one, 0, 0).unwrap();
        runtime.submit(two, 0, 0).unwrap();
        runtime.owners.insert("a".into(), scope.clone());
        let before = runtime.simulator.checkpoint(100000).unwrap();
        assert!(runtime.cancel_entries_for(&scope, 0).is_err());
        assert_eq!(before, runtime.simulator.checkpoint(100000).unwrap());
        let mut other = scope.clone();
        other.strategy_instance = "two".into();
        runtime.owners.insert("two".into(), other);
        assert_eq!(runtime.cancel_entries_for(&scope, 0).unwrap(), 1);
        assert!(runtime.simulator.positions()[0].entry_cancelled);
        assert!(!runtime.simulator.positions()[1].entry_cancelled);
        assert_eq!(runtime.cancel_entries_for(&scope, 0).unwrap(), 0);
    }
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
        assert!(runtime
            .release_unfilled_reservation("a", &portfolio)
            .is_err());
        assert_eq!(portfolio.snapshot("a").unwrap().reservations.len(), 1);
        runtime.cancel_entries_for(&plan.scope, 1).unwrap();
        assert!(runtime
            .release_unfilled_reservation("a", &portfolio)
            .unwrap());
        assert!(!runtime
            .release_unfilled_reservation("a", &portfolio)
            .unwrap());
        assert!(portfolio.snapshot("a").unwrap().reservations.is_empty());
        assert_eq!(
            portfolio.snapshot("a").unwrap().broker_available_minor,
            1000
        );
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
