#![forbid(unsafe_code)]
use arte_core::replay::{replay, ReplayTrade};
use std::io::{self, Read};
fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str){
        None|Some("help")|Some("--help")=>{println!("ARTE - Automated Real-Time Trading Engine\n\nOffline commands:\n  version\n  replay-market <interval-ns> <workers>   JSON trade array on stdin\n\nService startup is not enabled in this implementation slice.\nMarket replay is not a strategy-performance backtest.");Ok(())},
        Some("version")=>{println!("ARTE {}",env!("CARGO_PKG_VERSION"));Ok(())},
        Some("replay-market")=>{
            if args.len()!=3{return Err("usage: arte replay-market <interval-ns> <workers>".into());}
            let interval=args[1].parse().map_err(|_|"invalid interval")?;let workers=args[2].parse().map_err(|_|"invalid workers")?;
            let mut input=String::new();io::stdin().take(64*1024*1024+1).read_to_string(&mut input).map_err(|e|e.to_string())?;
            if input.len()>64*1024*1024{return Err("input exceeds 64 MiB offline-command limit".into());}
            let trades:Vec<ReplayTrade>=serde_json::from_str(&input).map_err(|e|e.to_string())?;
            let result=replay(&trades,interval,workers).map_err(|e|e.to_string())?;
            println!("{}",serde_json::to_string(&result).map_err(|e|e.to_string())?);Ok(())
        },
        Some("live"|"serve"|"maintenance"|"paper")=>Err("service startup unavailable; repository extraction and connected acceptance remain required".into()),
        Some(other)=>Err(format!("unknown command {other}; use --help")),
    }
}
fn main() {
    if let Err(error) = run() {
        eprintln!("ARTE blocked: {error}");
        std::process::exit(2);
    }
}
