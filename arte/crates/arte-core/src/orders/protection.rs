//! Strict normalized evidence for a settled parent's remaining bracket exposure.
//! The broker adapter must establish these facts; acknowledgments are insufficient.
//! This check does not release a session gate or authorize a new order.
use super::{Authorization, Side};
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Status {
    Working,
    Filled,
    Cancelled,
    Uncertain,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Kind {
    StopMarket,
    Limit,
    Other,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Leg {
    pub kind: Kind,
    pub broker_id: String,
    pub parent_id: String,
    pub account: String,
    pub instrument: u64,
    /// Long means buy, Short means sell, including exit orders.
    pub side: Side,
    pub price: i64,
    pub price_scale: u8,
    pub remaining: u64,
    pub filled: u64,
    pub status: Status,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub authorization_hash: String,
    pub broker_session: String,
    pub paper: bool,
    pub observed_at_ns: u64,
    pub parent_id: String,
    pub parent_status: Status,
    pub parent_filled: u64,
    /// Position attributable to this command, not the entire account position.
    pub open_quantity: u64,
    pub stop: Leg,
    pub target: Leg,
    /// True only after adapter evidence confirms protection survives disconnect.
    pub broker_resident: bool,
    /// True only after confirming sibling cancellation/reduction on partial fills.
    pub sibling_quantity_management: bool,
}

pub struct Scope<'a> {
    pub authorization_hash: &'a str,
    pub broker_session: &'a str,
    pub paper: bool,
    pub parent_id: &'a str,
    pub now_ns: u64,
    pub max_age_ns: u64,
}

/// An immutable audit result, deliberately not an execution capability.
#[derive(Debug, PartialEq, Eq)]
pub struct Verified {
    pub snapshot_hash: String,
    pub open_quantity: u64,
}

impl Snapshot {
    pub fn verify(&self, authorization: &Authorization, scope: &Scope<'_>) -> Result<Verified> {
        let order = &authorization.bracket;
        // An expired entry deadline does not invalidate protection of an existing fill.
        order.validate_geometry(0)?;
        if scope.authorization_hash.len() != 64
            || !scope
                .authorization_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || scope.broker_session.is_empty()
            || scope.parent_id.is_empty()
            || self.authorization_hash != scope.authorization_hash
            || authorization.hash()? != scope.authorization_hash
            || self.broker_session != scope.broker_session
            || self.paper != scope.paper
            || self.parent_id != scope.parent_id
        {
            return Err(Error::Conflict("protection evidence scope mismatch".into()));
        }
        if self.observed_at_ns == 0
            || scope.max_age_ns == 0
            || scope
                .now_ns
                .checked_sub(self.observed_at_ns)
                .is_none_or(|age| age >= scope.max_age_ns)
        {
            return Err(Error::Unready("protection evidence stale or future".into()));
        }
        // A still-fillable parent needs a separate pending-entry coverage protocol.
        if !matches!(self.parent_status, Status::Filled | Status::Cancelled)
            || self.parent_filled == 0
            || self.parent_filled > order.quantity
            || (self.parent_status == Status::Filled && self.parent_filled != order.quantity)
            || self.open_quantity == 0
            || !self.broker_resident
            || !self.sibling_quantity_management
        {
            return Err(Error::Unready(
                "settled parent and resident bracket evidence required".into(),
            ));
        }
        let open = self
            .parent_filled
            .checked_sub(self.stop.filled)
            .and_then(|q| q.checked_sub(self.target.filled));
        if open != Some(self.open_quantity) {
            return Err(Error::Conflict(
                "bracket fills disagree with attributed position".into(),
            ));
        }
        if self.stop.broker_id == self.target.broker_id {
            return Err(Error::Conflict(
                "duplicate protection leg identifier".into(),
            ));
        }
        let exit_side = match order.side {
            Side::Long => Side::Short,
            Side::Short => Side::Long,
        };
        for (leg, price, kind) in [
            (&self.stop, order.stop, Kind::StopMarket),
            (&self.target, order.target, Kind::Limit),
        ] {
            if leg.broker_id.is_empty()
                || leg.kind != kind
                || leg.broker_id == self.parent_id
                || leg.parent_id != self.parent_id
                || leg.account != order.account
                || leg.instrument != order.instrument
                || leg.side != exit_side
                || Some(leg.price) != price
                || leg.price_scale != order.price_scale
                || leg.status != Status::Working
                || leg.remaining != self.open_quantity
            {
                return Err(Error::Unready(
                    "protection leg missing, mismatched or not working".into(),
                ));
            }
        }
        Ok(Verified {
            snapshot_hash: content_hash(&("arte.bracket-protection.v1", self))?,
            open_quantity: self.open_quantity,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::orders::{AuthorizationContext, Bracket};
    fn authorization(order: &Bracket) -> Authorization {
        Authorization {
            bracket: order.clone(),
            context: AuthorizationContext {
                session_hash: "b".repeat(64),
                allow_extended: false,
                risk_policy_hash: "c".repeat(64),
            },
        }
    }
    fn fixture() -> (Bracket, Snapshot) {
        let order = Bracket {
            command_id: "c".into(),
            account: "a".into(),
            instrument: 1,
            side: Side::Long,
            quantity: 10,
            entry: 100,
            price_scale: 2,
            stop: Some(90),
            target: Some(120),
            tick: 1,
            deadline_ns: 1,
        };
        let stop = Leg {
            kind: Kind::StopMarket,
            broker_id: "s".into(),
            parent_id: "p".into(),
            account: "a".into(),
            instrument: 1,
            side: Side::Short,
            price: 90,
            price_scale: 2,
            remaining: 8,
            filled: 0,
            status: Status::Working,
        };
        let target = Leg {
            kind: Kind::Limit,
            broker_id: "t".into(),
            price: 120,
            filled: 2,
            ..stop.clone()
        };
        let snapshot = Snapshot {
            authorization_hash: authorization(&order).hash().unwrap(),
            broker_session: "session".into(),
            paper: true,
            observed_at_ns: 10,
            parent_id: "p".into(),
            parent_status: Status::Filled,
            parent_filled: 10,
            open_quantity: 8,
            stop,
            target,
            broker_resident: true,
            sibling_quantity_management: true,
        };
        (order, snapshot)
    }
    fn check(order: &Bracket, snapshot: &Snapshot) -> Result<Verified> {
        snapshot.verify(
            &authorization(order),
            &Scope {
                authorization_hash: &authorization(order).hash().unwrap(),
                broker_session: "session",
                paper: true,
                parent_id: "p",
                now_ns: 11,
                max_age_ns: 5,
            },
        )
    }
    #[test]
    fn working_partial_exit_and_cancelled_partial_entry() {
        let (order, mut s) = fixture();
        assert_eq!(check(&order, &s).unwrap().open_quantity, 8);
        s.parent_status = Status::Cancelled;
        s.parent_filled = 9;
        s.open_quantity = 7;
        s.stop.remaining = 7;
        s.target.remaining = 7;
        assert!(check(&order, &s).is_ok());
    }
    #[test]
    fn short_requires_buy_children() {
        let (mut order, mut s) = fixture();
        order.side = Side::Short;
        order.stop = Some(120);
        order.target = Some(90);
        s.authorization_hash = authorization(&order).hash().unwrap();
        s.stop.price = 120;
        s.target.price = 90;
        assert!(check(&order, &s).is_err());
        s.stop.side = Side::Long;
        s.target.side = Side::Long;
        assert!(check(&order, &s).is_ok());
    }
    #[test]
    fn uncertain_or_wrong_evidence_never_proves_protection() {
        let (order, original) = fixture();
        let changes: Vec<fn(&mut Snapshot)> = vec![
            |s| s.paper = false,
            |s| s.broker_session = "other".into(),
            |s| s.authorization_hash = "b".repeat(64),
            |s| s.parent_id = "other".into(),
            |s| s.observed_at_ns = 6,
            |s| s.observed_at_ns = 12,
            |s| s.parent_status = Status::Working,
            |s| s.parent_filled = 9,
            |s| s.broker_resident = false,
            |s| s.sibling_quantity_management = false,
            |s| s.stop.account = "other".into(),
            |s| s.stop.instrument = 2,
            |s| s.stop.parent_id = "other".into(),
            |s| s.stop.broker_id = "t".into(),
            |s| s.stop.broker_id = "p".into(),
            |s| s.stop.status = Status::Uncertain,
            |s| s.stop.remaining = 9,
            |s| s.target.remaining = 7,
            |s| s.stop.price = 89,
            |s| s.target.price_scale = 3,
            |s| s.stop.filled = u64::MAX,
            |s| s.open_quantity = 0,
            |s| s.stop.kind = Kind::Limit,
            |s| s.target.kind = Kind::StopMarket,
        ];
        for change in changes {
            let mut s = original.clone();
            change(&mut s);
            assert!(check(&order, &s).is_err(), "{s:?}");
        }
    }
    #[test]
    fn changed_authorization_cannot_reuse_snapshot() {
        let (mut order, s) = fixture();
        order.command_id = "different".into();
        assert!(check(&order, &s).is_err());
    }
}
