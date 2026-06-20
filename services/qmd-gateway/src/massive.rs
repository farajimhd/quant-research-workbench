use crate::bars::BarEventRouter;
use crate::config::GatewayConfig;
use crate::event::{massive_status_message, parse_massive_payload, MarketEvent};
use crate::indicators::IndicatorEventRouter;
use crate::metrics::SharedMetrics;
use crate::state::SharedMarketState;
use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use tokio::sync::{broadcast, mpsc};
use tokio::time::{sleep, Duration};
use tokio_tungstenite::{connect_async, tungstenite::Message};

pub async fn run_massive_ingest(
    config: GatewayConfig,
    state: SharedMarketState,
    writer_sender: Option<mpsc::Sender<MarketEvent>>,
    compact_writer_sender: Option<mpsc::Sender<MarketEvent>>,
    bar_router: BarEventRouter,
    indicator_router: IndicatorEventRouter,
    event_sender: broadcast::Sender<MarketEvent>,
    metrics: SharedMetrics,
) {
    if config.massive_api_key.is_empty() {
        eprintln!("MASSIVE_API_KEY is not configured; qmd-gateway API is running without live ingest.");
        return;
    }
    let subscriptions = config.subscription_channels();
    if subscriptions.is_empty() {
        eprintln!("No Massive subscriptions configured.");
        return;
    }
    loop {
        match connect_async(&config.massive_ws_url).await {
            Ok((mut websocket, _response)) => {
                let auth = json!({"action": "auth", "params": config.massive_api_key}).to_string();
                if let Err(error) = websocket.send(Message::Text(auth.into())).await {
                    eprintln!("Massive auth send failed: {error}");
                    sleep(Duration::from_secs(3)).await;
                    continue;
                }
                let subscribe = json!({"action": "subscribe", "params": subscriptions.join(",")}).to_string();
                if let Err(error) = websocket.send(Message::Text(subscribe.into())).await {
                    eprintln!("Massive subscribe send failed: {error}");
                    sleep(Duration::from_secs(3)).await;
                    continue;
                }
                while let Some(message) = websocket.next().await {
                    match message {
                        Ok(Message::Text(text)) => {
                            if let Some(status) = massive_status_message(&text) {
                                eprintln!("Massive status: {status}");
                            }
                            match parse_massive_payload(&text) {
                                Ok(events) => {
                                    for event in events {
                                        let kind = match &event {
                                            MarketEvent::Trade(_) => "trade",
                                            MarketEvent::Quote(_) => "quote",
                                        };
                                        metrics.observe_event(kind, event.ts());
                                        state.apply_event(&event).await;
                                        if event_sender.send(event.clone()).is_err() {
                                            metrics.inc_event_broadcast_dropped();
                                        }
                                        if bar_router.try_send(event.clone()).is_err() {
                                            metrics.inc_bar_event_dropped();
                                            eprintln!("Bar engine shard queue is full; dropped one aggregation event.");
                                        }
                                        if indicator_router.try_send_event(event.clone()).is_err() {
                                            metrics.inc_indicator_event_dropped();
                                            eprintln!("Indicator shard queue is full; dropped one indicator event.");
                                        }
                                        if let Some(sender) = &compact_writer_sender {
                                            if sender.try_send(event.clone()).is_err() {
                                                metrics.inc_compact_event_queue_dropped();
                                                eprintln!("Compact event writer queue is full; dropped one compact event.");
                                            }
                                        }
                                        if let Some(sender) = &writer_sender {
                                            if sender.try_send(event).is_err() {
                                                metrics.inc_clickhouse_event_dropped();
                                                eprintln!("Raw ClickHouse writer queue is full; dropped one raw persistence event.");
                                            }
                                        }
                                    }
                                }
                                Err(error) => {
                                    metrics.inc_parse_failure();
                                    eprintln!("Massive parse failed: {error}");
                                }
                            }
                        }
                        Ok(Message::Binary(_)) => {}
                        Ok(Message::Ping(payload)) => {
                            let _ = websocket.send(Message::Pong(payload)).await;
                        }
                        Ok(Message::Close(frame)) => {
                            metrics.inc_massive_disconnect();
                            eprintln!("Massive websocket closed: {frame:?}");
                            break;
                        }
                        Ok(_) => {}
                        Err(error) => {
                            metrics.inc_massive_disconnect();
                            eprintln!("Massive websocket error: {error}");
                            break;
                        }
                    }
                }
            }
            Err(error) => {
                metrics.inc_massive_connect_failure();
                eprintln!("Massive websocket connect failed: {error}");
            }
        }
        sleep(Duration::from_secs(3)).await;
    }
}
