use super::*;
use crate::events::{Decimal, EventKey, EventKind, SourceTime};
fn policy() -> Policy {
    Policy {
        schema_version: 1,
        provider: 1,
        valid_from_ns: 10,
        valid_to_ns: 20,
        available_at_ns: 9,
        source_manifest_hash: "a".repeat(64),
        allowed_conditions: BTreeSet::from([1]),
        excluded_conditions: BTreeSet::from([2]),
        allow_empty_conditions: true,
    }
}
fn event(conditions: Vec<u16>) -> Observation {
    Observation {
        key: EventKey {
            provider: 1,
            instrument: 1,
            session: 20260915,
            kind: EventKind::Trade,
            sequence: 1,
        },
        payload: Payload::Trade {
            price: Decimal {
                atoms: 100,
                scale: 2,
            },
            size: Decimal { atoms: 1, scale: 0 },
            exchange: 1,
            trade_id: "1".into(),
            trf: None,
            conditions,
            correction: None,
        },
        sip: SourceTime {
            ns: 10,
            precision_ns: 1,
        },
        participant: None,
        available_at_ns: 30,
        receipt: None,
    }
}
fn pin(policy: Policy) -> Pinned {
    let hash = policy.hash().unwrap();
    Pinned::new(policy, &hash).unwrap()
}
#[test]
fn known_exclusions_are_not_unknown_conditions_and_order_cannot_hide_errors() {
    let p = pin(policy());
    assert!(p.evaluate(&event(vec![]), 10).unwrap());
    assert!(p.evaluate(&event(vec![1]), 10).unwrap());
    assert!(!p.evaluate(&event(vec![1, 2]), 10).unwrap());
    for conditions in [vec![3], vec![2, 3], vec![3, 2]] {
        assert!(p.evaluate(&event(conditions), 10).is_err());
    }
    let mut changed = policy();
    changed.allow_empty_conditions = false;
    assert!(!pin(changed).evaluate(&event(vec![]), 10).unwrap());
}
#[test]
fn scope_causal_policy_time_and_corrections_fail_closed() {
    for fault in 0..6 {
        let mut p = policy();
        let mut event = event(vec![]);
        match fault {
            0 => event.key.provider = 2,
            1 => event.sip.ns = 20,
            2 => p.available_at_ns = 11,
            3 => event.sip.ns = 9,
            4 => {
                if let Payload::Trade { correction, .. } = &mut event.payload {
                    *correction = Some(0);
                }
            }
            5 => event.sip.ns = 11,
            _ => unreachable!(),
        }
        assert!(pin(p).evaluate(&event, 10).is_err());
    }
    let p = pin(policy());
    assert!(p
        .require_interval(Interval { start: 10, end: 20 }, 9)
        .is_ok());
    assert!(p
        .require_interval(Interval { start: 10, end: 21 }, 9)
        .is_err());
    assert!(p
        .require_interval(Interval { start: 10, end: 20 }, 8)
        .is_err());
}
#[test]
fn identity_and_disjoint_condition_classes_are_required() {
    let p = policy();
    assert!(Pinned::new(p.clone(), &"0".repeat(64)).is_err());
    let mut overlap = p.clone();
    overlap.excluded_conditions.insert(1);
    assert!(overlap.hash().is_err());
    let mut changed = p.clone();
    changed.allow_empty_conditions = false;
    assert_ne!(p.hash().unwrap(), changed.hash().unwrap());
    changed.schema_version = 2;
    assert!(changed.hash().is_err());
}
