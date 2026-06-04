use crate::bars::BarEventRouter;
use crate::config::GatewayConfig;
use crate::event::{massive_status_message, parse_massive_payload, MarketEvent};
use crate::indicators::IndicatorEventRouter;
use crate::state::SharedMarketState;
use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use tokio::sync::{broadcast, mpsc};
use tokio::time::{sleep, Duration};
use tokio_tungstenite::{connect_async, tungstenite::Message};

pub async fn run_massive_ingest(
    config: GatewayConfig,
    state: SharedMarketState,
    writer_sender: mpsc::Sender<MarketEvent>,
    bar_router: BarEventRouter,
    indicator_router: IndicatorEventRouter,
    event_sender: broadcast::Sender<MarketEvent>,
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
                                        state.apply_event(&event).await;
                                        let _ = event_sender.send(event.clone());
                                        if bar_router.try_send(event.clone()).is_err() {
                                            eprintln!("Bar engine shard queue is full; dropped one aggregation event.");
                                        }
                                        if indicator_router.try_send_event(event.clone()).is_err() {
                                            eprintln!("Indicator shard queue is full; dropped one indicator event.");
                                        }
                                        if writer_sender.try_send(event).is_err() {
                                            eprintln!("ClickHouse writer queue is full; dropped one persistence event.");
                                        }
                                    }
                                }
                                Err(error) => eprintln!("Massive parse failed: {error}"),
                            }
                        }
                        Ok(Message::Binary(_)) => {}
                        Ok(Message::Ping(payload)) => {
                            let _ = websocket.send(Message::Pong(payload)).await;
                        }
                        Ok(Message::Close(frame)) => {
                            eprintln!("Massive websocket closed: {frame:?}");
                            break;
                        }
                        Ok(_) => {}
                        Err(error) => {
                            eprintln!("Massive websocket error: {error}");
                            break;
                        }
                    }
                }
            }
            Err(error) => eprintln!("Massive websocket connect failed: {error}"),
        }
        sleep(Duration::from_secs(3)).await;
    }
}
