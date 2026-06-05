mod api;
mod classify;
mod clickhouse;
mod config;
mod extract;
mod intelligence;
mod massive;
mod metrics;
mod model;
mod state;

use crate::api::{app, AppState};
use crate::clickhouse::NewsClickHouse;
use crate::config::NewsGatewayConfig;
use crate::massive::run_news_pollers;
use crate::metrics::SharedMetrics;
use crate::model::{NewsArticle, NewsArticleSummary};
use crate::state::SharedNewsState;
use std::net::SocketAddr;
use tokio::sync::{broadcast, mpsc};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = NewsGatewayConfig::from_env();
    let bind: SocketAddr = config.bind.parse()?;
    let metrics = SharedMetrics::new();
    let news = SharedNewsState::new(config.recent_history_limit);
    let (writer_sender, writer_receiver) = mpsc::channel::<NewsArticle>(config.writer_channel_capacity);
    let (article_sender, _article_receiver) = broadcast::channel::<NewsArticleSummary>(10_000);

    let writer = NewsClickHouse::new(config.clone());
    tokio::spawn(writer.run(writer_receiver));
    tokio::spawn(run_news_pollers(
        config.clone(),
        news.clone(),
        writer_sender,
        article_sender.clone(),
        metrics.clone(),
    ));

    let app = app(AppState {
        articles: article_sender,
        config,
        metrics,
        news,
    });
    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
