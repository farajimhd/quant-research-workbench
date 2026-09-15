//! External integration implementations. Constructors do not connect or start services.
#![forbid(unsafe_code)]
pub mod clickhouse;
pub mod event_writer;
pub mod ibkr;
pub mod live_decode;
pub mod live_pipeline;
pub mod maintenance;
pub mod massive;
pub mod massive_stream;
pub mod rest_acquisition;
