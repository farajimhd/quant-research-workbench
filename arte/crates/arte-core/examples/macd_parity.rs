//! Offline comparison bridge. Not a production service or release component.
use arte_core::strategy_macd::State;
use serde::Deserialize;
use std::io::{self, Read};
#[derive(Deserialize)]
struct Sample {
    timeframe: u8,
    at_ns: u64,
    open: f64,
    close: f64,
    line: Option<f64>,
    signal: Option<f64>,
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    io::stdin()
        .take(64 * 1024 * 1024 + 1)
        .read_to_string(&mut input)?;
    if input.len() > 64 * 1024 * 1024 {
        return Err("input exceeds 64 MiB".into());
    }
    let cases: Vec<Vec<Sample>> = serde_json::from_str(&input)?;
    if cases.len() > 128 || cases.iter().any(|case| case.len() > 512) {
        return Err("case budget exceeded".into());
    }
    let output: Vec<_> = cases
        .iter()
        .map(|case| {
            let mut state = State::new(true);
            case.iter()
                .map(|sample| {
                    match sample.timeframe {
                        5 => state.completed_five(
                            sample.at_ns,
                            sample.close,
                            sample.line,
                            sample.signal,
                        ),
                        1 => state.completed_one(sample.at_ns, sample.open, sample.close),
                        _ => Err(arte_core::Error::Invalid(
                            "unsupported fixture timeframe".into(),
                        )),
                    }
                    .map_err(|error| error.to_string())
                })
                .collect::<Vec<_>>()
        })
        .collect();
    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}
