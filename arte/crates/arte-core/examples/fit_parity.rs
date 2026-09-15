//! Offline numerical test bridge. Never packaged as a production service.
use arte_core::v7_fit::fit;
use serde::Deserialize;
use std::io::{self, Read};
#[derive(Deserialize)]
struct Case {
    prices: Vec<f64>,
    tick: f64,
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    io::stdin()
        .take(64 * 1024 * 1024 + 1)
        .read_to_string(&mut input)?;
    if input.len() > 64 * 1024 * 1024 {
        return Err("input exceeds 64 MiB".into());
    }
    let cases: Vec<Case> = serde_json::from_str(&input)?;
    if cases.len() > 512 {
        return Err("case limit exceeded".into());
    }
    let result: Vec<_> = cases
        .iter()
        .map(|case| fit(&case.prices, case.tick).map_err(|e| e.to_string()))
        .collect();
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}
