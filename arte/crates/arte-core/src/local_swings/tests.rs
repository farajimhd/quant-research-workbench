use super::*;
use crate::{
    acquisition::{trade_seconds::Builder, Authority, Certificate, Page},
    coverage::Interval,
    event_order::Scope,
    events::EventKind,
};
fn empty_proof(start: u64, end: u64) -> crate::acquisition::trade_seconds::EmptySpan {
    let scope = Scope {
        provider: 1,
        instrument: 1,
        session: 20260915,
    };
    let interval = Interval {
        start: start * 1_000_000_000,
        end: end * 1_000_000_000,
    };
    let cert = Certificate {
        schema_version: 1,
        authority: Authority {
            provider: 1,
            instrument: 1,
            kind: EventKind::Trade,
            source_revision: "test".into(),
            contract_hash: "a".repeat(64),
            capabilities_hash: "b".repeat(64),
        },
        interval,
        first_request_hash: "c".repeat(64),
        pages: vec![Page {
            request_hash: "c".repeat(64),
            response_hash: "d".repeat(64),
            next_request_hash: None,
            acquired_at_ns: interval.end,
            source_rows: 0,
            accepted_rows: 0,
            rejected_rows: 0,
            deduplicated_rows: 0,
            batches: vec![],
            identity_checked: true,
            ordering_checked: true,
            interval_checked: true,
        }],
        published_at_ns: interval.end,
    };
    Builder::new(cert, scope, 100)
        .unwrap()
        .finish()
        .unwrap()
        .1
        .prove_empty(interval, interval.end)
        .unwrap()
}
fn epoch() -> u64 {
    chrono::DateTime::parse_from_rfc3339("2026-09-15T14:00:00Z")
        .unwrap()
        .timestamp() as u64
}
#[test]
fn certified_gap_preserves_sequence_extremes_and_recovery() {
    let base = epoch();
    let scope = Scope {
        provider: 1,
        instrument: 1,
        session: 20260915,
    };
    let mut state = State::new(1, scope.session, config()).unwrap();
    let mut contiguous = state.clone();
    for (i, price) in [10., 9.8, 10.].into_iter().enumerate() {
        state.observe(&bar(base + i as u64, price)).unwrap();
        contiguous.observe(&bar(base + i as u64, price)).unwrap();
    }
    let proof = empty_proof(base + 3, base + 8);
    let next = bar(base + 8, 10.2);
    state
        .observe_with_empty_span(&next, scope, &proof, next.end_ns)
        .unwrap();
    contiguous.observe(&bar(base + 3, 10.2)).unwrap();
    assert_eq!(state.sequence, contiguous.sequence);
    assert_eq!(state.direction, contiguous.direction);
    assert_eq!(state.levels.len(), contiguous.levels.len());
    assert_eq!(state.ranges, contiguous.ranges);
    assert!(!state.snapshot().unwrap().unwrap().gap_reset);
    let context = "e".repeat(64);
    let image = state.checkpoint(&context, 100_000).unwrap();
    let mut restored = State::restore_checkpoint(
        &image,
        &image.id,
        &context,
        1,
        scope.session,
        config(),
        Some(&next),
        100_000,
    )
    .unwrap();
    state.observe(&bar(base + 9, 10.)).unwrap();
    restored.observe(&bar(base + 9, 10.)).unwrap();
    assert_eq!(
        content_hash(&state).unwrap(),
        content_hash(&restored).unwrap()
    );
    // A recovery image cannot erase the elapsed empty interval.
    restored.gaps.clear();
    assert!(restored.checkpoint(&context, 100_000).is_err());
}
#[test]
fn wrong_unknown_long_or_cross_session_gap_fails_closed() {
    let base = epoch();
    let scope = Scope {
        provider: 1,
        instrument: 1,
        session: 20260915,
    };
    for (start, end, provider, cutoff) in [
        (base + 1, base + 5, 2, u64::MAX),
        (base + 1, base + 5, 1, 0),
        (base + 1, base + 32, 1, u64::MAX),
        (base + 2, base + 5, 1, u64::MAX),
        (base + 86401, base + 86405, 1, u64::MAX),
    ] {
        let mut state = State::new(1, scope.session, config()).unwrap();
        state
            .observe(&bar(
                if start > base + 86400 {
                    base + 86400
                } else {
                    base
                },
                10.,
            ))
            .unwrap();
        let proof = empty_proof(start, end);
        assert!(state
            .observe_with_empty_span(&bar(end, 10.), Scope { provider, ..scope }, &proof, cutoff)
            .is_err());
        assert!(state.snapshot().is_err());
    }
}
fn config() -> Config {
    Config {
        reversal_bps: 50.,
        volatility_multiple: 2.,
        volatility_cap_multiple: 2.,
        lifetime_bars: 1800,
        maximum_levels: 100,
    }
}
fn bar(second: u64, close: f64) -> Bar {
    Bar {
        start_ns: second * 1_000_000_000,
        end_ns: (second + 1) * 1_000_000_000,
        open: close,
        high: close + 0.01,
        low: close - 0.01,
        close,
        volume: 1.,
        notional: close,
        trades: 1,
    }
}
#[test]
fn pivots_are_confirmed_later_and_gaps_reset_without_identity_reuse() {
    let mut state = State::new(1, 20260915, config()).unwrap();
    for (i, p) in [10., 9.8, 10., 10.2, 10.].into_iter().enumerate() {
        state.observe(&bar(200 + i as u64, p)).unwrap();
    }
    let snapshot = state.snapshot().unwrap().unwrap();
    assert!(!snapshot.swings.is_empty());
    assert!(snapshot
        .swings
        .iter()
        .all(|s| s.pivot_at_ns < s.confirmed_at_ns && s.confirmed_at_ns <= snapshot.at_ns));
    let ids: Vec<_> = snapshot.swings.iter().map(|s| s.id.clone()).collect();
    state.observe(&bar(210, 10.)).unwrap();
    assert!(state.snapshot().unwrap().unwrap().gap_reset);
    assert!(state.snapshot().unwrap().unwrap().swings.is_empty());
    for (i, p) in [9.8, 10., 10.2, 10.].into_iter().enumerate() {
        state.observe(&bar(211 + i as u64, p)).unwrap();
    }
    assert!(state
        .snapshot()
        .unwrap()
        .unwrap()
        .swings
        .iter()
        .all(|s| !ids.contains(&s.id)));
}
#[test]
fn invalid_or_over_capacity_owner_exposes_no_partial_state() {
    let mut state = State::new(1, 20260915, config()).unwrap();
    state.observe(&bar(200, 10.)).unwrap();
    assert!(state.observe(&bar(200, 10.)).is_err());
    assert!(state.snapshot().is_err());
    let mut cfg = config();
    cfg.maximum_levels = 1;
    let mut state = State::new(1, 20260915, cfg).unwrap();
    let mut failed = false;
    for (i, p) in [10., 9.8, 10., 10.2, 10.].into_iter().enumerate() {
        if state.observe(&bar(200 + i as u64, p)).is_err() {
            failed = true;
            break;
        }
    }
    assert!(failed && state.snapshot().is_err());
}

#[test]
fn recovery_at_every_candle_preserves_continuation_and_gap_generations() {
    let mut original = State::new(1, 20260915, config()).unwrap();
    let mut restored = State::new(1, 20260915, config()).unwrap();
    let context = "c".repeat(64);
    let genesis = original.checkpoint(&context, 100_000).unwrap();
    assert!(State::restore_checkpoint(
        &genesis,
        &genesis.id,
        &context,
        1,
        20260915,
        config(),
        None,
        100_000
    )
    .unwrap()
    .snapshot()
    .unwrap()
    .is_none());
    let mut saw_pending = false;
    for (i, price) in [10., 9.8, 10., 9.6, 9.5, 9.79, 9.4, 10., 10.2, 10., 9.8, 10.]
        .into_iter()
        .enumerate()
    {
        let candle = bar(200 + i as u64 + if i >= 9 { 3 } else { 0 }, price);
        original.observe(&candle).unwrap();
        restored.observe(&candle).unwrap();
        saw_pending |= original.levels.values().any(|l| l.phase != Phase::Active);
        let image = original.checkpoint(&context, 100_000).unwrap();
        assert_eq!(
            restored.checkpoint(&context, 100_000).unwrap().payload,
            image.payload
        );
        restored = State::restore_checkpoint(
            &image,
            &image.id,
            &context,
            1,
            20260915,
            config(),
            Some(&candle),
            100_000,
        )
        .unwrap();
    }
    assert!(saw_pending);
}

#[test]
fn recovery_rejects_wrong_candle_policy_scope_and_state() {
    use crate::seed_storage::Object;
    let mut state = State::new(1, 20260915, config()).unwrap();
    for (i, p) in [10., 9.8, 10.].into_iter().enumerate() {
        state.observe(&bar(200 + i as u64, p)).unwrap();
    }
    let candle = bar(202, 10.);
    let context = "c".repeat(64);
    let image = state.checkpoint(&context, 100_000).unwrap();
    assert!(state.checkpoint(&context, 1).is_err());
    assert!(State::restore_checkpoint(
        &image,
        &image.id,
        &context,
        1,
        20260915,
        config(),
        None,
        100_000
    )
    .is_err());
    assert!(State::restore_checkpoint(
        &image,
        &image.id,
        &context,
        2,
        20260915,
        config(),
        Some(&candle),
        100_000
    )
    .is_err());
    let mut changed = config();
    changed.reversal_bps += 1.;
    assert!(State::restore_checkpoint(
        &image,
        &image.id,
        &context,
        1,
        20260915,
        changed,
        Some(&candle),
        100_000
    )
    .is_err());
    let mut corrupt = image.clone();
    corrupt.payload.push(b' ');
    assert!(State::restore_checkpoint(
        &corrupt,
        &image.id,
        &context,
        1,
        20260915,
        config(),
        Some(&candle),
        100_000
    )
    .is_err());
    for case in 0..6 {
        let mut json: serde_json::Value = serde_json::from_slice(&image.payload).unwrap();
        match case {
            0 => json["state"]["sequence"] = 999.into(),
            1 => json["state"]["failed"] = true.into(),
            2 => json["state"]["high"]["at_ns"] = 999_000_000_000_u64.into(),
            3 => json["state"]["ranges"] = serde_json::json!([]),
            4 => json["state"]["snapshot"]["at_ns"] = 0.into(),
            5 => json["state"]["levels"]["1"]["last_test"] = 999.into(),
            _ => unreachable!(),
        }
        let changed = Object::new(serde_json::to_vec(&json).unwrap());
        assert!(
            State::restore_checkpoint(
                &changed,
                &changed.id,
                &context,
                1,
                20260915,
                config(),
                Some(&candle),
                100_000
            )
            .is_err(),
            "case {case}"
        );
    }
}
