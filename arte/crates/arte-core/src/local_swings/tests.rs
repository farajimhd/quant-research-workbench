use super::*;
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
