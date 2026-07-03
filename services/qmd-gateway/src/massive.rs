use crate::bars::BarEventRouter;
use crate::config::GatewayConfig;
use crate::event::{massive_status_message, parse_massive_payload, MarketEvent};
use crate::indicators::IndicatorEventRouter;
use crate::live_market_state::LiveMarketStateRouter;
use crate::metrics::SharedMetrics;
use crate::reference_tradability::SharedReferenceTradabilityStore;
use crate::state::SharedMarketState;
use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use tokio::sync::{broadcast, mpsc};
use tokio::time::{sleep, Duration};
use tokio_tungstenite::{connect_async, tungstenite::Message};

#[derive(Clone)]
pub struct MarketEventFanout {
    pub state: SharedMarketState,
    pub writer_sender: Option<mpsc::Sender<MarketEvent>>,
    pub compact_writer_sender: Option<mpsc::Sender<MarketEvent>>,
    pub bar_router: BarEventRouter,
    pub indicator_router: IndicatorEventRouter,
    pub live_market_state_router: LiveMarketStateRouter,
    pub reference_tradability: SharedReferenceTradabilityStore,
    pub event_sender: broadcast::Sender<MarketEvent>,
    pub metrics: SharedMetrics,
}

pub async fn run_massive_ingest(config: GatewayConfig, fanout: MarketEventFanout) {
    if config.massive_api_key.is_empty() {
        eprintln!(
            "MASSIVE_API_KEY is not configured; qmd-gateway API is running without live ingest."
        );
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
                let subscribe =
                    json!({"action": "subscribe", "params": subscriptions.join(",")}).to_string();
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
                                        fanout_market_event(event, &fanout).await;
                                    }
                                }
                                Err(error) => {
                                    fanout.metrics.inc_parse_failure();
                                    eprintln!("Massive parse failed: {error}");
                                }
                            }
                        }
                        Ok(Message::Binary(_)) => {}
                        Ok(Message::Ping(payload)) => {
                            let _ = websocket.send(Message::Pong(payload)).await;
                        }
                        Ok(Message::Close(frame)) => {
                            fanout.metrics.inc_massive_disconnect();
                            eprintln!("Massive websocket closed: {frame:?}");
                            break;
                        }
                        Ok(_) => {}
                        Err(error) => {
                            fanout.metrics.inc_massive_disconnect();
                            eprintln!("Massive websocket error: {error}");
                            break;
                        }
                    }
                }
            }
            Err(error) => {
                fanout.metrics.inc_massive_connect_failure();
                eprintln!("Massive websocket connect failed: {error}");
            }
        }
        sleep(Duration::from_secs(3)).await;
    }
}

pub async fn fanout_market_event(event: MarketEvent, fanout: &MarketEventFanout) {
    let kind = match &event {
        MarketEvent::Trade(_) => "trade",
        MarketEvent::Quote(_) => "quote",
    };
    fanout.metrics.observe_event(kind, event.ts());
    fanout.state.apply_event(&event).await;
    if fanout
        .live_market_state_router
        .send_event(event.clone())
        .await
        .is_err()
    {
        eprintln!("Live market state receiver closed; could not route one market event.");
    }
    let reference_emit_allowed = fanout
        .reference_tradability
        .is_emit_allowed(event.ticker())
        .await;
    if reference_emit_allowed {
        if fanout.event_sender.send(event.clone()).is_err() {
            fanout.metrics.inc_event_broadcast_dropped();
        }
    } else {
        fanout.metrics.inc_reference_filtered_event();
    }
    if fanout.bar_router.send(event.clone()).await.is_err() {
        fanout.metrics.inc_bar_event_dropped();
        eprintln!("Bar engine receiver closed; could not route one aggregation event.");
    }
    if fanout
        .indicator_router
        .send_event(event.clone())
        .await
        .is_err()
    {
        fanout.metrics.inc_indicator_event_dropped();
        eprintln!("Indicator shard receiver closed; could not route one indicator event.");
    }
    if let Some(sender) = &fanout.compact_writer_sender {
        if sender.send(event.clone()).await.is_err() {
            fanout.metrics.inc_compact_event_queue_dropped();
            eprintln!("Compact event writer receiver closed; could not route one compact event.");
        }
    }
    if let Some(sender) = &fanout.writer_sender {
        if sender.send(event).await.is_err() {
            fanout.metrics.inc_clickhouse_event_dropped();
            eprintln!(
                "Raw ClickHouse writer receiver closed; could not route one raw persistence event."
            );
        }
    }
}
