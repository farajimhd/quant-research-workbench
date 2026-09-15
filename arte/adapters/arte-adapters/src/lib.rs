//! External integration implementations. Constructors do not connect or start services.
#![forbid(unsafe_code)]
pub mod clickhouse;
pub mod event_writer;
pub mod ibkr;
pub mod live_decode;
pub mod live_pipeline;
pub mod maintenance;
pub mod maintenance_pool;
pub mod maintenance_runtime;
pub mod massive;
pub mod massive_stream;
pub mod ownership;
pub mod request_governor;
pub mod rest_acquisition;
pub mod stoppable_fetcher;
pub mod strategy_journal;
