//! Causal Strategy 350 session open/high from eligible ordered trades only.
//! The first trade cannot use its own high as a prior-HOD reference.
use crate::{
    acquisition::VerifiedCertificate,
    content_hash,
    event_order::Scope,
    event_storage::Batch,
    events::{Decimal, EventKind, Observation, Payload},
    strategy350_price_gate::{PriceFact, SessionContext},
    trade_eligibility::Pinned,
    Error, Result,
};

pub const VERSION: &str = "arte.strategy-350-session.v1";

pub struct Builder {
    scope: Scope,
    start_ns: u64,
    end_ns: u64,
    price_scale: u8,
    policy_hash: String,
    configuration_hash: String,
    start_certified: bool,
    open: Option<i64>,
    high: i64,
    high_available_at_ns: u64,
    high_order: (u64, u64),
    last_order: Option<(u64, u64)>,
    failed: bool,
    seen: u64,
    excluded: u64,
}
impl Builder {
    fn new(
        scope: Scope,
        start_ns: u64,
        end_ns: u64,
        price_scale: u8,
        policy_hash: String,
        start_certified: bool,
    ) -> Result<Self> {
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || start_ns == 0
            || start_ns >= end_ns
            || price_scale > 9
            || policy_hash.len() != 64
            || !policy_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("Strategy 350 session contract".into()));
        }
        let configuration_hash = content_hash(&(
            VERSION,
            scope.provider,
            scope.instrument,
            scope.session,
            start_ns,
            end_ns,
            price_scale,
            &policy_hash,
            start_certified,
        ))?;
        Ok(Self {
            scope,
            start_ns,
            end_ns,
            price_scale,
            policy_hash,
            configuration_hash,
            start_certified,
            open: None,
            high: 0,
            high_available_at_ns: 0,
            high_order: (0, 0),
            last_order: None,
            failed: false,
            seen: 0,
            excluded: 0,
        })
    }
    /// Streaming state starts unready until the separate live handover owner
    /// supplies a verified continuity authority. Callers cannot set readiness.
    pub fn new_unready(
        scope: Scope,
        start_ns: u64,
        end_ns: u64,
        price_scale: u8,
        policy_hash: String,
    ) -> Result<Self> {
        Self::new(scope, start_ns, end_ns, price_scale, policy_hash, false)
    }
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
    }
    pub fn counts(&self) -> Result<(u64, u64)> {
        if self.failed {
            return Err(Error::Unready(
                "Strategy 350 session requires recovery".into(),
            ));
        }
        Ok((self.seen, self.excluded))
    }
    pub fn observe(
        &mut self,
        event: &Observation,
        policy: &Pinned,
        evaluated_at_ns: u64,
    ) -> Result<Option<SessionContext>> {
        if self.failed {
            return Err(Error::Unready(
                "Strategy 350 session requires recovery".into(),
            ));
        }
        let result = self.update(event, policy, evaluated_at_ns);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn update(
        &mut self,
        event: &Observation,
        policy: &Pinned,
        evaluated_at_ns: u64,
    ) -> Result<Option<SessionContext>> {
        event.validate()?;
        let order = (event.sip.ns, event.key.sequence);
        if event.key.provider != self.scope.provider
            || event.key.instrument != self.scope.instrument
            || event.key.session != self.scope.session
            || policy.hash() != self.policy_hash
            || event.available_at_ns > evaluated_at_ns
            || event.sip.ns > event.available_at_ns
            || self.last_order.is_some_and(|old| order <= old)
        {
            return Err(Error::Conflict(
                "Strategy 350 session trade scope or order".into(),
            ));
        }
        let Payload::Trade { price, .. } = &event.payload else {
            return Err(Error::Invalid("Strategy 350 session needs trade".into()));
        };
        let eligible = policy.evaluate(event, evaluated_at_ns)?;
        self.last_order = Some(order);
        self.seen = self
            .seen
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("session trade count".into()))?;
        if !eligible {
            self.excluded = self
                .excluded
                .checked_add(1)
                .ok_or_else(|| Error::Capacity("excluded trade count".into()))?;
            return Ok(None);
        }
        if event.sip.ns < self.start_ns || event.sip.ns >= self.end_ns {
            return Ok(None);
        }
        let atoms = price.atoms_at_scale(self.price_scale)?;
        if atoms <= 0 {
            return Err(Error::Invalid("Strategy 350 eligible trade price".into()));
        }
        let open = *self.open.get_or_insert(atoms);
        let (prior_atoms, prior_available_at, prior_order) = if self.high == 0 {
            (atoms, event.available_at_ns, order)
        } else {
            (self.high, self.high_available_at_ns, self.high_order)
        };
        if atoms > self.high {
            self.high = atoms;
            self.high_available_at_ns = event.available_at_ns;
            self.high_order = order;
        }
        Ok(Some(SessionContext {
            session: self.scope.session,
            at_ns: event.available_at_ns,
            source_order: order,
            open: Decimal {
                atoms: open,
                scale: self.price_scale,
            },
            high: Decimal {
                atoms: self.high,
                scale: self.price_scale,
            },
            prior_high: Some(PriceFact {
                value: Decimal {
                    atoms: prior_atoms,
                    scale: self.price_scale,
                },
                available_at_ns: prior_available_at,
                source_hash: self.policy_hash.clone(),
                source_order: Some(prior_order),
            }),
            complete: self.start_certified,
        }))
    }
}

/// Maintenance-only replay of an already readback-verified full session. The
/// certificate's acquisition time is not historical decision availability.
pub struct HistoricalReplay {
    builder: Builder,
    verified: VerifiedCertificate,
    page: usize,
    batch: usize,
    rows: u64,
    expected_rows: u64,
    as_of_ns: u64,
    failed: bool,
}
impl HistoricalReplay {
    pub fn new(
        scope: Scope,
        start_ns: u64,
        end_ns: u64,
        price_scale: u8,
        policy: &Pinned,
        verified: VerifiedCertificate,
        as_of_ns: u64,
    ) -> Result<Self> {
        let cert = verified.certificate();
        if cert.authority.provider != scope.provider
            || cert.authority.instrument != scope.instrument
            || cert.authority.kind != EventKind::Trade
            || cert.interval.start != start_ns
            || cert.interval.end != end_ns
            || cert.published_at_ns > as_of_ns
        {
            return Err(Error::Conflict(
                "Strategy 350 historical session authority".into(),
            ));
        }
        let expected_rows = cert.pages.iter().try_fold(0u64, |sum, p| {
            sum.checked_add(p.accepted_rows - p.deduplicated_rows)
                .ok_or_else(|| Error::Capacity("session certificate count".into()))
        })?;
        let mut builder = Builder::new(
            scope,
            start_ns,
            end_ns,
            price_scale,
            policy.hash().into(),
            true,
        )?;
        builder.configuration_hash =
            content_hash(&(VERSION, builder.configuration_hash, cert.id()?))?;
        let mut replay = Self {
            builder,
            verified,
            page: 0,
            batch: 0,
            rows: 0,
            expected_rows,
            as_of_ns,
            failed: false,
        };
        replay.advance_empty();
        Ok(replay)
    }
    fn advance_empty(&mut self) {
        while self.page < self.verified.certificate().pages.len()
            && self.verified.certificate().pages[self.page]
                .batches
                .is_empty()
        {
            self.page += 1;
        }
    }
    pub fn next_batch_id(&self) -> Option<&str> {
        self.verified
            .certificate()
            .pages
            .get(self.page)
            .and_then(|p| p.batches.get(self.batch))
            .map(String::as_str)
    }
    /// Callback is a maintenance calculation. It cannot submit orders or claim
    /// a historical receive timestamp from the REST acquisition clock.
    pub fn apply_batch(
        &mut self,
        batch: &Batch,
        policy: &Pinned,
        mut apply: impl FnMut(&Observation, &SessionContext) -> Result<()>,
    ) -> Result<usize> {
        if self.failed {
            return Err(Error::Unready(
                "historical session replay requires recovery".into(),
            ));
        }
        let result = (|| {
            if self.next_batch_id() != Some(batch.id()?.as_str())
                || policy.hash() != self.builder.policy_hash
            {
                return Err(Error::Conflict(
                    "historical session batch or policy differs".into(),
                ));
            }
            let mut count = 0usize;
            for event in batch.hydrate()? {
                if let Some(context) = self.builder.observe(&event, policy, self.as_of_ns)? {
                    apply(&event, &context)?;
                }
                count += 1;
            }
            self.rows = self
                .rows
                .checked_add(count as u64)
                .ok_or_else(|| Error::Capacity("historical replay count".into()))?;
            self.batch += 1;
            if self.batch == self.verified.certificate().pages[self.page].batches.len() {
                self.page += 1;
                self.batch = 0;
                self.advance_empty();
            }
            Ok(count)
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    pub fn finish(self) -> Result<(u64, u64, String)> {
        if self.failed
            || self.page != self.verified.certificate().pages.len()
            || self.rows != self.expected_rows
        {
            return Err(Error::Unready(
                "historical session replay incomplete".into(),
            ));
        }
        let (seen, excluded) = self.builder.counts()?;
        Ok((seen, excluded, self.builder.configuration_hash))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        acquisition::{Authority, Certificate, Page, Verifier},
        coverage::Interval,
        events::{EventKey, EventKind, SourceTime},
        trade_eligibility,
    };
    use std::collections::BTreeSet;
    const S: u64 = 1_000_000_000;
    fn policy() -> Pinned {
        let p = trade_eligibility::Policy {
            schema_version: 1,
            provider: 1,
            valid_from_ns: 0,
            valid_to_ns: u64::MAX,
            available_at_ns: 0,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: BTreeSet::new(),
            excluded_conditions: BTreeSet::from([7]),
            allow_empty_conditions: true,
        };
        let hash = p.hash().unwrap();
        Pinned::new(p, &hash).unwrap()
    }
    fn event(
        sip: u64,
        available: u64,
        sequence: u64,
        price: &str,
        conditions: Vec<u16>,
    ) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind: EventKind::Trade,
                sequence,
            },
            payload: Payload::Trade {
                price: Decimal::parse(price).unwrap(),
                size: Decimal::parse("1").unwrap(),
                exchange: 1,
                trade_id: sequence.to_string(),
                trf: None,
                conditions,
                correction: None,
            },
            sip: SourceTime {
                ns: sip,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: available,
            receipt: None,
        }
    }
    fn builder(policy: &Pinned, complete: bool) -> Builder {
        Builder::new(
            Scope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            S,
            10 * S,
            2,
            policy.hash().into(),
            complete,
        )
        .unwrap()
    }
    fn scaled(price: &str) -> Decimal {
        Decimal {
            atoms: Decimal::parse(price).unwrap().atoms_at_scale(2).unwrap(),
            scale: 2,
        }
    }
    #[test]
    fn excluded_trades_do_not_set_open_or_high() {
        let p = policy();
        let mut b = builder(&p, true);
        assert!(b
            .observe(&event(2 * S, 2 * S, 1, "15", vec![7]), &p, 2 * S)
            .unwrap()
            .is_none());
        let context = b
            .observe(&event(3 * S, 3 * S, 2, "10", vec![]), &p, 3 * S)
            .unwrap()
            .unwrap();
        assert_eq!(context.open, scaled("10"));
        assert_eq!(context.high, scaled("10"));
        assert_eq!(context.prior_high.unwrap().source_order, Some((3 * S, 2)));
        assert_eq!(b.counts().unwrap(), (2, 1));
    }
    #[test]
    fn high_is_prior_only_even_with_equal_or_reordered_arrival() {
        let p = policy();
        let mut b = builder(&p, true);
        b.observe(&event(2 * S, 5 * S, 1, "10", vec![]), &p, 5 * S)
            .unwrap();
        let second = b
            .observe(&event(3 * S, 4 * S, 2, "11.50", vec![]), &p, 5 * S)
            .unwrap()
            .unwrap();
        assert_eq!(second.prior_high().unwrap().value, scaled("10"));
        let third = b
            .observe(&event(3 * S, 4 * S, 3, "10.50", vec![]), &p, 5 * S)
            .unwrap()
            .unwrap();
        assert_eq!(third.prior_high().unwrap().value, scaled("11.50"));
        assert_eq!(third.high(), scaled("11.50"));
        assert!(b
            .observe(&event(3 * S, 4 * S, 3, "10.50", vec![]), &p, 5 * S)
            .is_err());
        assert!(b.counts().is_err());
    }
    #[test]
    fn uncertified_start_never_grants_ready_context() {
        let p = policy();
        let mut b = builder(&p, false);
        assert!(!b
            .observe(&event(2 * S, 2 * S, 1, "10", vec![]), &p, 2 * S)
            .unwrap()
            .unwrap()
            .complete());
    }
    #[test]
    fn builder_prior_high_feeds_gate_at_equal_availability() {
        use crate::execution_interval::ExecutionInterval;
        use crate::strategy350_price_gate::{
            Block, Config as GateConfig, PriceFact, State as Gate,
        };
        let p = policy();
        let mut builder = builder(&p, true);
        let config = GateConfig {
            execution_interval: ExecutionInterval::Events,
            price_scale: 2,
            prior_close_max_atoms: 2000,
            purchase_min_atoms: 100,
            late_gain_bps: 1500,
            hod_floor_bps: 7000,
            prior_close_source_hash: "c".repeat(64),
            trade_policy_hash: p.hash().into(),
        };
        let hash = config.hash().unwrap();
        let mut gate = Gate::new(
            Scope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            S,
            config,
            &hash,
        )
        .unwrap();
        let close = PriceFact {
            value: scaled("19"),
            available_at_ns: S,
            source_hash: "c".repeat(64),
            source_order: None,
        };
        let first = event(2 * S, 5 * S, 1, "10", vec![]);
        let first_context = builder.observe(&first, &p, 5 * S).unwrap().unwrap();
        assert_eq!(
            gate.observe(&first, &p, Some(&close), &first_context, 5 * S)
                .unwrap()
                .block,
            None
        );
        let high = event(3 * S, 5 * S, 2, "11.50", vec![]);
        let high_context = builder.observe(&high, &p, 5 * S).unwrap().unwrap();
        assert_eq!(
            gate.observe(&high, &p, Some(&close), &high_context, 5 * S)
                .unwrap()
                .block,
            Some(Block::LateModeOutsidePriorHodZone)
        );
        let inside = event(3 * S, 5 * S, 3, "10.50", vec![]);
        let inside_context = builder.observe(&inside, &p, 5 * S).unwrap().unwrap();
        assert_eq!(
            gate.observe(&inside, &p, Some(&close), &inside_context, 5 * S)
                .unwrap()
                .block,
            None
        );
    }
    fn verified_session() -> (Batch, VerifiedCertificate) {
        let events = [
            event(2 * S, 5 * S, 1, "10", vec![]),
            event(3 * S, 5 * S, 2, "11", vec![]),
        ];
        let batch = Batch::prepare(&events).unwrap();
        let certificate = Certificate {
            schema_version: 1,
            authority: Authority {
                provider: 1,
                instrument: 10,
                kind: EventKind::Trade,
                source_revision: "test".into(),
                contract_hash: "c".repeat(64),
                capabilities_hash: "d".repeat(64),
            },
            interval: Interval {
                start: S,
                end: 10 * S,
            },
            first_request_hash: "a".repeat(64),
            pages: vec![Page {
                request_hash: "a".repeat(64),
                response_hash: "b".repeat(64),
                next_request_hash: None,
                acquired_at_ns: 5 * S,
                source_rows: 2,
                accepted_rows: 2,
                rejected_rows: 0,
                deduplicated_rows: 0,
                batches: vec![batch.id().unwrap()],
                identity_checked: true,
                ordering_checked: true,
                interval_checked: true,
            }],
            published_at_ns: 6 * S,
        };
        let mut verifier = Verifier::new(certificate).unwrap();
        verifier.observe(&batch).unwrap();
        (batch, verifier.finish().unwrap())
    }
    #[test]
    fn verified_full_session_replay_is_bounded_and_causal() {
        let p = policy();
        let (batch, verified) = verified_session();
        let scope = Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        };
        let mut replay = HistoricalReplay::new(scope, S, 10 * S, 2, &p, verified, 6 * S).unwrap();
        assert_eq!(replay.next_batch_id(), Some(batch.id().unwrap().as_str()));
        let mut seen = Vec::new();
        assert_eq!(
            replay
                .apply_batch(&batch, &p, |event, context| {
                    seen.push((event.sip.ns, context.high().atoms, context.complete()));
                    Ok(())
                })
                .unwrap(),
            2
        );
        assert_eq!(seen, vec![(2 * S, 1000, true), (3 * S, 1100, true)]);
        assert_eq!(replay.next_batch_id(), None);
        assert_eq!(replay.finish().unwrap().0, 2);
        let (batch, verified) = verified_session();
        let incomplete = HistoricalReplay::new(scope, S, 10 * S, 2, &p, verified, 6 * S).unwrap();
        assert!(incomplete.finish().is_err());
        assert_eq!(batch.observations().len(), 2);
    }
}
