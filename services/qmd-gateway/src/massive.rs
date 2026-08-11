use crate::bars::BarEventRouter;
use crate::config::GatewayConfig;
use crate::event::{massive_status_message, parse_massive_payload, MarketEvent};
use crate::indicators::IndicatorEventRouter;
use crate::live_market_state::LiveMarketStateRouter;
use crate::metrics::{SharedMetrics, TimingTarget};
use crate::state::{ScannerRowDelta, SharedMarketState};
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
    pub event_sender: broadcast::Sender<MarketEvent>,
    pub scanner_delta_sender: broadcast::Sender<ScannerRowDelta>,
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
    fanout.metrics.set_lane_state(
        "massive_feed",
        "connecting",
        "Connecting to the Massive stock websocket.",
    );
    loop {
        match connect_async(&config.massive_ws_url).await {
            Ok((mut websocket, _response)) => {
                let auth = json!({"action": "auth", "params": config.massive_api_key}).to_string();
                if let Err(error) = websocket.send(Message::Text(auth.into())).await {
                    fanout.metrics.record_lane_failure(
                        "massive_feed",
                        &format!("Authentication send failed: {error}"),
                    );
                    eprintln!("Massive auth send failed: {error}");
                    sleep(Duration::from_secs(3)).await;
                    continue;
                }
                let subscribe =
                    json!({"action": "subscribe", "params": subscriptions.join(",")}).to_string();
                if let Err(error) = websocket.send(Message::Text(subscribe.into())).await {
                    fanout.metrics.record_lane_failure(
                        "massive_feed",
                        &format!("Subscription send failed: {error}"),
                    );
                    eprintln!("Massive subscribe send failed: {error}");
                    sleep(Duration::from_secs(3)).await;
                    continue;
                }
                fanout.metrics.record_lane_success(
                    "massive_feed",
                    0,
                    "Authenticated and subscribed to the configured quote/trade channels.",
                );
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
                            fanout.metrics.record_lane_failure(
                                "massive_feed",
                                &format!("Websocket closed: {frame:?}"),
                            );
                            eprintln!("Massive websocket closed: {frame:?}");
                            break;
                        }
                        Ok(_) => {}
                        Err(error) => {
                            fanout.metrics.inc_massive_disconnect();
                            fanout.metrics.record_lane_failure(
                                "massive_feed",
                                &format!("Websocket error: {error}"),
                            );
                            eprintln!("Massive websocket error: {error}");
                            break;
                        }
                    }
                }
            }
            Err(error) => {
                fanout.metrics.inc_massive_connect_failure();
                fanout
                    .metrics
                    .record_lane_failure("massive_feed", &format!("Connection failed: {error}"));
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
    let scanner_delta = {
        let _core_scan_profile = fanout
            .metrics
            .sampled_timing(TimingTarget::CoreScanEvent, 1_024);
        fanout.state.apply_event(&event).await
    };
    // Admit the authoritative compact/raw records before any derived consumer
    // can apply backpressure. These bounded sends intentionally slow ingest
    // rather than dropping source events; replaceable broadcasts below remain
    // non-blocking and require lagging clients to resnapshot.
    enqueue_authoritative_event(
        event.clone(),
        fanout.compact_writer_sender.as_ref(),
        fanout.writer_sender.as_ref(),
        &fanout.metrics,
    )
    .await;
    if fanout.scanner_delta_sender.send(scanner_delta).is_err() {
        fanout.metrics.inc_event_broadcast_dropped();
    }
    if fanout
        .live_market_state_router
        .send_event(event.clone())
        .await
        .is_err()
    {
        eprintln!("Live market state receiver closed; could not route one market event.");
    }
    if fanout.event_sender.send(event.clone()).is_err() {
        fanout.metrics.inc_event_broadcast_dropped();
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
}

async fn enqueue_authoritative_event(
    event: MarketEvent,
    compact_writer_sender: Option<&mpsc::Sender<MarketEvent>>,
    writer_sender: Option<&mpsc::Sender<MarketEvent>>,
    metrics: &SharedMetrics,
) {
    if let Some(sender) = compact_writer_sender {
        if sender.send(event.clone()).await.is_err() {
            metrics.inc_compact_event_queue_dropped();
            eprintln!("Compact event writer receiver closed; could not route one compact event.");
        }
    }
    if let Some(sender) = writer_sender {
        if sender.send(event).await.is_err() {
            metrics.inc_clickhouse_event_dropped();
            eprintln!(
                "Raw ClickHouse writer receiver closed; could not route one raw persistence event."
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::TradeEvent;
    use chrono::{TimeZone, Utc};
    use serde_json::json;

    fn trade(sequence: u64) -> MarketEvent {
        let ts = Utc.with_ymd_and_hms(2026, 8, 11, 15, 0, 0).unwrap();
        MarketEvent::Trade(TradeEvent {
            conditions: vec![],
            exchange: 4,
            ingest_ts: ts,
            participant_ts: None,
            price: 100.0,
            raw: json!({}),
            sequence,
            size: 10.0,
            tape: 1,
            ticker: "AAPL".to_string(),
            trade_id: sequence.to_string(),
            trf_id: 0,
            trf_ts: None,
            ts,
        })
    }

    #[tokio::test]
    async fn compact_authority_is_admitted_before_raw_backpressure() {
        let (compact_sender, mut compact_receiver) = mpsc::channel(1);
        let (raw_sender, mut raw_receiver) = mpsc::channel(1);
        raw_sender.send(trade(1)).await.unwrap();

        let event = trade(2);
        let metrics = SharedMetrics::new();
        let task = tokio::spawn(async move {
            enqueue_authoritative_event(event, Some(&compact_sender), Some(&raw_sender), &metrics)
                .await;
        });

        let admitted = tokio::time::timeout(Duration::from_millis(100), compact_receiver.recv())
            .await
            .expect("compact authority was blocked behind the full raw queue")
            .expect("compact receiver closed");
        assert_eq!(admitted.ticker(), "AAPL");
        assert!(
            !task.is_finished(),
            "raw backpressure should remain lossless"
        );
        raw_receiver.recv().await.unwrap();
        task.await.unwrap();
    }
}
