//! Single real-time stock connection. Reconnect requires the caller's repair gate.
use arte_core::{Error, Result};
use futures_util::{SinkExt, StreamExt};
use serde_json::json;
#[cfg(test)]
use serde_json::Value;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::{mpsc, watch};
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{protocol::WebSocketConfig, Message},
};
const ENDPOINT: &str = "wss://socket.massive.com/stocks";
#[derive(Debug, Clone)]
pub struct Config {
    pub symbols: Vec<String>,
    pub maximum_message_bytes: usize,
    pub handshake_timeout_ms: u64,
    pub idle_timeout_ms: u64,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Health {
    Connecting,
    Authenticating,
    /// Subscribe bytes sent; channel acceptance and completeness are unknown.
    SubscriptionRequested,
    /// At least one market frame arrived; not a trading-readiness certificate.
    Receiving,
    Stopped,
    Failed,
}
/// Receipt is stamped before JSON parsing. Multiple events may share one frame.
/// Event application sequence is assigned later; frame sequence is not an event ID.
#[derive(Debug, Clone)]
pub struct ReceivedFrame {
    pub text: String,
    pub utc_ns: u64,
    pub monotonic_ns: u64,
    pub frame_sequence: u64,
}
#[derive(Debug)]
pub enum Exit {
    Stopped,
    ConsumerOverflow(ReceivedFrame),
}
struct Guard {
    health: watch::Sender<Health>,
    terminal: bool,
}
impl Drop for Guard {
    fn drop(&mut self) {
        if !self.terminal {
            self.health.send_replace(Health::Failed);
        }
    }
}
fn subscription(config: &Config) -> Result<String> {
    if config.symbols.is_empty()
        || config.symbols.len() > 10000
        || config.maximum_message_bytes == 0
        || config.maximum_message_bytes > 32 * 1024 * 1024
        || config.handshake_timeout_ms == 0
        || config.idle_timeout_ms == 0
    {
        return Err(Error::Invalid(
            "invalid bounded stream configuration".into(),
        ));
    }
    let mut unique = std::collections::BTreeSet::new();
    let mut channels = Vec::with_capacity(config.symbols.len() * 2);
    for symbol in &config.symbols {
        if symbol.is_empty()
            || symbol.len() > 32
            || !symbol
                .bytes()
                .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || b".-^".contains(&c))
            || !unique.insert(symbol)
        {
            return Err(Error::Invalid("invalid or duplicate stream symbol".into()));
        }
        channels.push(format!("T.{symbol}"));
        channels.push(format!("Q.{symbol}"));
    }
    Ok(json!({"action":"subscribe","params":channels.join(",")}).to_string())
}
#[derive(Debug, Default)]
struct Classification {
    authenticated: bool,
    market: bool,
}
fn deliver(
    output: &mpsc::Sender<ReceivedFrame>,
    health: &watch::Sender<Health>,
    frame: ReceivedFrame,
) -> Option<ReceivedFrame> {
    match output.try_send(frame) {
        Ok(()) => {
            health.send_replace(Health::Receiving);
            None
        }
        Err(mpsc::error::TrySendError::Full(frame) | mpsc::error::TrySendError::Closed(frame)) => {
            health.send_replace(Health::Failed);
            Some(frame)
        }
    }
}
fn classify(text: &str) -> Result<Classification> {
    #[derive(serde::Deserialize)]
    struct Header {
        ev: String,
        status: Option<String>,
    }
    // Ignore market payload fields here; downstream normalization owns them.
    let rows: Vec<Header> = serde_json::from_str(text)
        .map_err(|_| Error::Invalid("invalid stream JSON; payload redacted".into()))?;
    if rows.is_empty() {
        return Err(Error::Invalid("empty stream frame".into()));
    }
    let mut kind = Classification::default();
    for row in rows {
        match row.ev.as_str() {
            "T" | "Q" => kind.market = true,
            "status" => match row.status.as_deref() {
                Some("auth_success") => kind.authenticated = true,
                Some("connected" | "success") => {}
                _ => {
                    return Err(Error::Unready(
                        "provider stream status requires intervention; payload redacted".into(),
                    ))
                }
            },
            _ => return Err(Error::Invalid("unexpected stream channel".into())),
        }
    }
    Ok(kind)
}
/// Does not reconnect or start any background task. Call only after lifecycle authorization.
/// `origin` belongs to the run and must survive reconnections for monotonic comparisons.
/// Overflow retains the undelivered frame and marks health failed; trading must stay blocked.
pub async fn receive(
    config: &Config,
    api_key: &str,
    origin: Instant,
    output: mpsc::Sender<ReceivedFrame>,
    health: watch::Sender<Health>,
    mut shutdown: watch::Receiver<bool>,
) -> Result<Exit> {
    let mut guard = Guard {
        health,
        terminal: false,
    };
    guard.health.send_replace(Health::Connecting);
    let result = receive_inner(
        config,
        api_key,
        origin,
        output,
        &guard.health,
        &mut shutdown,
    )
    .await;
    guard
        .health
        .send_replace(if matches!(result, Ok(Exit::Stopped)) {
            Health::Stopped
        } else {
            Health::Failed
        });
    guard.terminal = true;
    result
}
async fn receive_inner(
    config: &Config,
    api_key: &str,
    origin: Instant,
    output: mpsc::Sender<ReceivedFrame>,
    health: &watch::Sender<Health>,
    shutdown: &mut watch::Receiver<bool>,
) -> Result<Exit> {
    let subscribe = subscription(config)?;
    if api_key.is_empty() {
        return Err(Error::Invalid("stream credential missing".into()));
    }
    if *shutdown.borrow() {
        return Ok(Exit::Stopped);
    }
    let ws_config = WebSocketConfig::default()
        .max_message_size(Some(config.maximum_message_bytes))
        .max_frame_size(Some(config.maximum_message_bytes));
    let (mut socket, _) = tokio::time::timeout(
        Duration::from_millis(config.handshake_timeout_ms),
        connect_async_with_config(ENDPOINT, Some(ws_config), true),
    )
    .await
    .map_err(|_| Error::Unready("stream connect timeout".into()))?
    .map_err(|_| Error::Unready("stream connect failure; details redacted".into()))?;
    health.send_replace(Health::Authenticating);
    tokio::time::timeout(
        Duration::from_millis(config.handshake_timeout_ms),
        socket.send(Message::Text(
            json!({"action":"auth","params":api_key}).to_string().into(),
        )),
    )
    .await
    .map_err(|_| Error::Unready("stream auth send timeout".into()))?
    .map_err(|_| Error::Unready("stream auth send failed".into()))?;
    let auth_deadline =
        tokio::time::Instant::now() + Duration::from_millis(config.handshake_timeout_ms);
    let mut authenticated = false;
    let mut sequence = 0u64;
    loop {
        let deadline = if authenticated {
            tokio::time::Instant::now() + Duration::from_millis(config.idle_timeout_ms)
        } else {
            auth_deadline
        };
        let next = tokio::select! {
            changed=shutdown.changed()=>{
                if changed.is_err()||*shutdown.borrow(){let _=tokio::time::timeout(Duration::from_secs(2),socket.close(None)).await;return Ok(Exit::Stopped);}
                continue;
            },
            frame=tokio::time::timeout_at(deadline,socket.next())=>frame.map_err(|_|Error::Unready("stream authentication or receive timeout".into()))?,
        };
        // Capture application receipt before inspection or parsing. This is not NIC time.
        let utc_ns = u64::try_from(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|_| Error::Unready("receive clock before epoch".into()))?
                .as_nanos(),
        )
        .map_err(|_| Error::Invalid("receive clock overflow".into()))?;
        let monotonic_ns = u64::try_from(origin.elapsed().as_nanos())
            .map_err(|_| Error::Invalid("monotonic clock overflow".into()))?;
        let message = next
            .ok_or_else(|| Error::Unready("provider stream ended; repair required".into()))?
            .map_err(|_| Error::Unready("provider stream read failed; repair required".into()))?;
        match message {
            Message::Text(text) => {
                let kind = classify(text.as_str())?;
                if kind.authenticated && !authenticated {
                    authenticated = true;
                    tokio::time::timeout(
                        Duration::from_millis(config.handshake_timeout_ms),
                        socket.send(Message::Text(subscribe.clone().into())),
                    )
                    .await
                    .map_err(|_| Error::Unready("stream subscription timeout".into()))?
                    .map_err(|_| Error::Unready("stream subscription failed".into()))?;
                    health.send_replace(Health::SubscriptionRequested);
                }
                if kind.market {
                    if !authenticated {
                        return Err(Error::Unready("market events before authentication".into()));
                    }
                    sequence = sequence
                        .checked_add(1)
                        .ok_or_else(|| Error::Capacity("stream frame sequence exhausted".into()))?;
                    let frame = ReceivedFrame {
                        text: text.to_string(),
                        utc_ns,
                        monotonic_ns,
                        frame_sequence: sequence,
                    };
                    if let Some(frame) = deliver(&output, health, frame) {
                        return Ok(Exit::ConsumerOverflow(frame));
                    }
                }
            }
            Message::Ping(_) => {
                tokio::time::timeout(
                    Duration::from_millis(config.handshake_timeout_ms),
                    socket.flush(),
                )
                .await
                .map_err(|_| Error::Unready("stream pong timeout".into()))?
                .map_err(|_| Error::Unready("stream pong failed".into()))?;
            }
            Message::Pong(_) => {}
            Message::Close(_) => {
                return Err(Error::Unready(
                    "provider closed stream; repair required".into(),
                ))
            }
            _ => return Err(Error::Invalid("unexpected non-text market frame".into())),
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn channel_list_is_explicit_and_does_not_allow_injection() {
        let mut c = Config {
            symbols: vec!["AAPL".into(), "BRK.B".into()],
            maximum_message_bytes: 1024,
            handshake_timeout_ms: 1000,
            idle_timeout_ms: 5000,
        };
        let v: Value = serde_json::from_str(&subscription(&c).unwrap()).unwrap();
        assert_eq!(v["params"], "T.AAPL,Q.AAPL,T.BRK.B,Q.BRK.B");
        c.symbols.push("*,Q.*".into());
        assert!(subscription(&c).is_err());
    }
    #[test]
    fn protocol_status_is_not_market_readiness() {
        assert!(
            !classify(r#"[{"ev":"status","status":"auth_success"}]"#)
                .unwrap()
                .market
        );
        assert!(classify(r#"[{"ev":"T"},{"ev":"Q"}]"#).unwrap().market);
        assert!(
            !classify(r#"[{"ev":"status","status":"auth_failed","message":"secret"}]"#)
                .unwrap_err()
                .to_string()
                .contains("secret")
        );
    }
    #[test]
    fn cancellation_guard_marks_feed_failed() {
        let (tx, rx) = watch::channel(Health::Receiving);
        {
            let _guard = Guard {
                health: tx,
                terminal: false,
            };
        }
        assert_eq!(*rx.borrow(), Health::Failed);
    }
    #[test]
    fn overflow_retains_undelivered_frame_and_sets_failed_health() {
        let (tx, mut rx) = mpsc::channel(1);
        let (health, state) = watch::channel(Health::SubscriptionRequested);
        let frame = ReceivedFrame {
            text: "first".into(),
            utc_ns: 10,
            monotonic_ns: 5,
            frame_sequence: 1,
        };
        assert!(deliver(&tx, &health, frame.clone()).is_none());
        let second = ReceivedFrame {
            text: "second".into(),
            frame_sequence: 2,
            ..frame
        };
        assert_eq!(deliver(&tx, &health, second).unwrap().text, "second");
        assert_eq!(*state.borrow(), Health::Failed);
        assert_eq!(rx.try_recv().unwrap().text, "first");
    }
}
