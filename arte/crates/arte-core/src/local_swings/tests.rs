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
