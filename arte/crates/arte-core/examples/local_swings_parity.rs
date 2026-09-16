//! Offline stdin/stdout comparison bridge. No services or database access.
use arte_core::{
    local_swings::{Config, State},
    market::Bar,
};
use serde::Deserialize;
use std::io::Read;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    config: Config,
    bars: Vec<Bar>,
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut bytes = Vec::new();
    std::io::stdin()
        .take(16 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 16 * 1024 * 1024 {
        return Err("parity input budget".into());
    }
    let input: Input = serde_json::from_slice(&bytes)?;
    let mut state = State::new(1, 20260915, input.config)?;
    let mut output = Vec::new();
    for bar in input.bars {
        state.observe(&bar)?;
        output.push(state.snapshot()?.unwrap().clone());
    }
    println!("{}", serde_json::to_string(&output)?);
    Ok(())
}
