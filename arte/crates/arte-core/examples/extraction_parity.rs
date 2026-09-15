//! Offline test bridge, not included in production release packaging.
use arte_core::v7_extraction::{extract, Candle, ProfileRow, Settings};
use serde::Deserialize;
use std::io::{self, Read};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Case {
    bars: Vec<Candle>,
    profile: Vec<ProfileRow>,
    ticker: String,
    session: String,
    available_at: u64,
    settings: Settings,
}
fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    io::stdin()
        .take(64 * 1024 * 1024 + 1)
        .read_to_string(&mut input)?;
    if input.len() > 64 * 1024 * 1024 {
        return Err("parity input exceeds 64 MiB".into());
    }
    let cases: Vec<Case> = serde_json::from_str(&input)?;
    if cases.len() > 512 {
        return Err("parity input exceeds 512 cases".into());
    }
    let result: Vec<_> = cases
        .into_iter()
        .map(|case| {
            match extract(
                &case.bars,
                &case.profile,
                &case.ticker,
                &case.session,
                case.available_at,
                "offline-parity",
                case.settings,
            ) {
                Ok(result) => serde_json::json!({"ok":result}),
                Err(error) => serde_json::json!({"error":error.to_string()}),
            }
        })
        .collect();
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("ARTE parity bridge failed: {error}");
        std::process::exit(2);
    }
}
