//! Offline policy input preflight. No provider, database or broker capability.
use arte_core::{
    candidate_config::document::Document,
    run_manifest::{Manifest, Pinned},
};
use std::{fs::File, io::Read};

fn read(path: &str, maximum: usize) -> Result<Vec<u8>, String> {
    let file = File::open(path).map_err(|error| format!("cannot open input: {error}"))?;
    let mut bytes = Vec::new();
    file.take(maximum as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| format!("cannot read input: {error}"))?;
    if bytes.len() > maximum {
        return Err("input exceeds byte budget".into());
    }
    Ok(bytes)
}

pub fn check(
    manifest: &[u8],
    expected_hash: &str,
    policies: &[u8],
    json: bool,
) -> Result<String, String> {
    let manifest: Manifest =
        serde_json::from_slice(manifest).map_err(|error| format!("manifest: {error}"))?;
    let pinned = Pinned::new(manifest, expected_hash).map_err(|error| error.to_string())?;
    let configs = Document::decode(policies)
        .and_then(|document| document.bind(&pinned))
        .map_err(|error| error.to_string())?;
    Ok(report(pinned.hash(), configs.len(), json))
}

fn report(manifest_hash: &str, consumers: usize, json: bool) -> String {
    if json {
        serde_json::json!({
            "status": "policy_structure_verified",
            "manifest_hash": manifest_hash,
            "consumers": consumers,
            "effective_hashes_verified": false,
            "trading_authorized": false,
            "remaining": ["market_feature_and_quote_policy_binding", "runtime_acceptance"]
        })
        .to_string()
    } else {
        format!("Policy structure verified: {consumers} consumer(s).\nManifest:\n{manifest_hash}\nNot verified: effective hashes against market and quote policies.\nTrading remains unauthorized; runtime acceptance is still required.")
    }
}

pub fn run(args: &[String]) -> Result<String, String> {
    if !(args.len() == 4 || (args.len() == 5 && args[4] == "--json")) {
        return Err("usage: arte check-policies <manifest.json> <expected-manifest-sha256> <policies.json> [--json]".into());
    }
    let manifest = read(&args[1], 64 * 1024 * 1024)?;
    let policies = read(
        &args[3],
        arte_core::candidate_config::document::MAXIMUM_BYTES,
    )?;
    check(&manifest, &args[2], &policies, args.len() == 5)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reports_preserve_readiness_limits_and_machine_output() {
        let hash = "a".repeat(64);
        let human = report(&hash, 2, false);
        assert!(human.lines().all(|line| line.len() <= 80));
        assert!(!human.contains('\u{1b}'));
        assert!(human.contains("Trading remains unauthorized"));
        let machine: serde_json::Value = serde_json::from_str(&report(&hash, 2, true)).unwrap();
        assert_eq!(machine["consumers"], 2);
        assert_eq!(machine["manifest_hash"], hash);
        assert_eq!(machine["effective_hashes_verified"], false);
        assert_eq!(machine["trading_authorized"], false);
    }
    #[test]
    fn invalid_inputs_and_arguments_fail_without_starting_services() {
        assert!(run(&["check-policies".into()]).is_err());
        assert!(run(&[
            "check-policies".into(),
            "x".into(),
            "x".into(),
            "x".into(),
            "--unknown".into()
        ])
        .is_err());
        assert!(check(b"{}", "bad", b"{}", false).is_err());
    }
}
