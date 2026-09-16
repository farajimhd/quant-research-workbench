//! Isolated restriction adapter test. Algorithm and scheduler tests cover state production.
use super::*;

#[test]
fn encounter_exit_merges_without_clearing_external_safety() {
    let market = crate::market_structure::tests::runtime_with_timeframes(
        20,
        vec![crate::market_structure::Timeframe {
            interval_ns: 5 * SECOND,
            macd_periods: (12, 26, 9),
            maximum_bars: 20,
        }],
    );
    let config = Config {
        swings: crate::local_swings::Config {
            reversal_bps: 50.,
            volatility_multiple: 2.,
            volatility_cap_multiple: 2.,
            lifetime_bars: 1800,
            maximum_levels: 100,
        },
        encounters: crate::strategy_encounters::stream::Config {
            tick: 0.01,
            maximum_prior_levels: 100,
            settings: crate::strategy_encounters::Settings {
                breakout_buffer_ticks: 1.,
                breakout_buffer_bps: 0.,
                rejection_break_offset_bps: 10.,
                topping_tail_fraction: 0.5,
                maximum_encounters: 100,
            },
        },
        setup: SetupSettings {
            range_ns: 30 * SECOND,
            minimum_bars: 1,
            maximum_gap_ns: 0,
        },
        forming_macd: true,
        minimum_range_pct: 0.,
        minimum_progress_pct: 0.,
        maximum_quote_age_ns: SECOND,
        maximum_completed_bar_age_ns: SECOND,
        maximum_levels: 100,
    };
    let mut state = State::new(&market, config).unwrap();
    let input = crate::strategy_dispatch::InputBoundary {
        event_id: "a".repeat(64),
        event_time_ns: 200 * SECOND,
        available_at_ns: 200 * SECOND,
        evaluated_at_ns: 200 * SECOND,
        source_sequence: 1,
        feature_hash: String::new(),
    };
    let safety = crate::strategy_dispatch::Safety {
        position_quantity: 3,
        pending_exit_quantity: 0,
        exit_pending: false,
        pending_entry: false,
        last_exit_reason: None,
        flatten: false,
        protective_stop_crossed: false,
        manual_exit: true,
        completed_macd_reversal: false,
        setup_phase: crate::strategy_lifecycle::Phase::Building,
        luld_buffer_reached: false,
        encounter_exit: false,
        early_setup_failed: false,
        structural_exit: false,
    };
    assert!(state.restrict_safety(&input, &safety).is_err());
    state.snapshot = Some(Snapshot {
        boundary_id: input.event_id.clone(),
        sequence: 1,
        available_at_ns: input.available_at_ns,
        evaluated_at_ns: input.evaluated_at_ns,
        macd: None,
        one_second: None,
        encounters: crate::strategy_encounters::stream::Snapshot {
            boundary_id: input.event_id.clone(),
            sequence: 1,
            input_hash: "b".repeat(64),
            evaluated_at_ns: input.evaluated_at_ns,
            blocked: true,
            exit_reason: Some(crate::strategy_encounters::ExitReason::ToppingRejectionNextOpen),
            tracked_levels: 1,
        },
    });
    let restricted = state.restrict_safety(&input, &safety).unwrap();
    assert!(restricted.encounter_exit && restricted.manual_exit);
    assert_eq!(restricted.position_quantity, 3);
    assert!(!safety.encounter_exit);
    state.snapshot.as_mut().unwrap().encounters.exit_reason = None;
    assert!(
        state
            .restrict_safety(&input, &restricted)
            .unwrap()
            .encounter_exit
    );
    assert!(
        !state
            .restrict_safety(&input, &safety)
            .unwrap()
            .encounter_exit
    );
    let mut later = input.clone();
    later.evaluated_at_ns += 1;
    assert!(state.restrict_safety(&later, &safety).is_ok());
    later.source_sequence += 1;
    assert!(state.restrict_safety(&later, &safety).is_err());
    later = input;
    later.evaluated_at_ns -= 1;
    assert!(state.restrict_safety(&later, &safety).is_err());
}
