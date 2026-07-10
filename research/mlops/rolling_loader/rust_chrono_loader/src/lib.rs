use std::collections::VecDeque;
use std::ffi::c_char;
use std::ptr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const VERSION: &str = "rolling_loader_rust/0.1.0";

#[repr(C)]
#[derive(Clone, Copy)]
pub struct RustQueueProfileConfig {
    pub ticker_count: u32,
    pub origins_per_ticker: u32,
    pub event_stream_len: u32,
    pub event_feature_count: u32,
    pub batch_size: u32,
    pub realtime_read_workers: u32,
    pub prefetch_read_workers: u32,
    pub realtime_process_workers: u32,
    pub prefetch_process_workers: u32,
    pub prefetch_ticker_count: u32,
    pub read_sleep_us: u32,
    pub process_sleep_us: u32,
}

impl Default for RustQueueProfileConfig {
    fn default() -> Self {
        Self {
            ticker_count: 8_000,
            origins_per_ticker: 512,
            event_stream_len: 1_024,
            event_feature_count: 25,
            batch_size: 1_024,
            realtime_read_workers: 32,
            prefetch_read_workers: 16,
            realtime_process_workers: 32,
            prefetch_process_workers: 16,
            prefetch_ticker_count: 4_000,
            read_sleep_us: 0,
            process_sleep_us: 0,
        }
    }
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct RustQueueProfileStats {
    pub status: i32,
    pub elapsed_ns: u64,
    pub read_jobs_enqueued: u64,
    pub read_jobs_finished: u64,
    pub process_jobs_enqueued: u64,
    pub process_jobs_finished: u64,
    pub realtime_read_jobs: u64,
    pub prefetch_read_jobs: u64,
    pub realtime_process_jobs: u64,
    pub prefetch_process_jobs: u64,
    pub read_priority_steals: u64,
    pub process_priority_steals: u64,
    pub read_worker_ns: u64,
    pub process_worker_ns: u64,
    pub samples: u64,
    pub batches: u64,
    pub cache_tickers: u64,
    pub event_cache_rebuilds: u64,
    pub event_cache_appends: u64,
    pub event_cache_reused: u64,
    pub bytes_allocated: u64,
    pub checksum_bits: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Priority {
    Realtime,
    Prefetch,
}

struct ReadJob {
    ticker_id: u32,
    origins: u32,
    priority: Priority,
}

struct LoadedTickerData {
    ticker_id: u32,
    origins: u32,
    stream_len: usize,
    feature_count: usize,
    events: Arc<Vec<f32>>,
    ordinals: Arc<Vec<u64>>,
    priority: Priority,
}

struct ProcessJob {
    data: LoadedTickerData,
}

struct JobQueue<T> {
    state: Mutex<VecDeque<T>>,
    cv: Condvar,
}

impl<T> JobQueue<T> {
    fn new() -> Self {
        Self {
            state: Mutex::new(VecDeque::new()),
            cv: Condvar::new(),
        }
    }

    fn push(&self, value: T) {
        let mut guard = self.state.lock().expect("queue mutex poisoned");
        guard.push_back(value);
        self.cv.notify_one();
    }

    fn try_pop(&self) -> Option<T> {
        self.state.lock().expect("queue mutex poisoned").pop_front()
    }

    fn is_empty(&self) -> bool {
        self.state.lock().expect("queue mutex poisoned").is_empty()
    }
}

struct QueuePair<T> {
    realtime: Arc<JobQueue<T>>,
    prefetch: Arc<JobQueue<T>>,
}

impl<T> QueuePair<T> {
    fn new() -> Self {
        Self {
            realtime: Arc::new(JobQueue::new()),
            prefetch: Arc::new(JobQueue::new()),
        }
    }

    fn is_empty(&self) -> bool {
        self.realtime.is_empty() && self.prefetch.is_empty()
    }
}

struct EventTickerState {
    stream: Vec<f32>,
    ordinals: Vec<u64>,
    last_ordinal: u64,
}

struct RuntimeShared {
    cfg: RustQueueProfileConfig,
    read_queues: QueuePair<ReadJob>,
    process_queues: QueuePair<ProcessJob>,
    shutdown: AtomicBool,
    read_jobs_enqueued: AtomicU64,
    read_jobs_finished: AtomicU64,
    process_jobs_enqueued: AtomicU64,
    process_jobs_finished: AtomicU64,
    realtime_read_jobs: AtomicU64,
    prefetch_read_jobs: AtomicU64,
    realtime_process_jobs: AtomicU64,
    prefetch_process_jobs: AtomicU64,
    read_priority_steals: AtomicU64,
    process_priority_steals: AtomicU64,
    read_worker_ns: AtomicU64,
    process_worker_ns: AtomicU64,
    samples: AtomicU64,
    event_cache_rebuilds: AtomicU64,
    event_cache_appends: AtomicU64,
    event_cache_reused: AtomicU64,
    bytes_allocated: AtomicU64,
    checksum_bits: AtomicU64,
    event_cache: Vec<Mutex<Option<EventTickerState>>>,
}

impl RuntimeShared {
    fn new(cfg: RustQueueProfileConfig) -> Arc<Self> {
        let cache_slots = cfg
            .ticker_count
            .saturating_add(cfg.prefetch_ticker_count)
            .max(1) as usize;
        Arc::new(Self {
            cfg,
            read_queues: QueuePair::new(),
            process_queues: QueuePair::new(),
            shutdown: AtomicBool::new(false),
            read_jobs_enqueued: AtomicU64::new(0),
            read_jobs_finished: AtomicU64::new(0),
            process_jobs_enqueued: AtomicU64::new(0),
            process_jobs_finished: AtomicU64::new(0),
            realtime_read_jobs: AtomicU64::new(0),
            prefetch_read_jobs: AtomicU64::new(0),
            realtime_process_jobs: AtomicU64::new(0),
            prefetch_process_jobs: AtomicU64::new(0),
            read_priority_steals: AtomicU64::new(0),
            process_priority_steals: AtomicU64::new(0),
            read_worker_ns: AtomicU64::new(0),
            process_worker_ns: AtomicU64::new(0),
            samples: AtomicU64::new(0),
            event_cache_rebuilds: AtomicU64::new(0),
            event_cache_appends: AtomicU64::new(0),
            event_cache_reused: AtomicU64::new(0),
            bytes_allocated: AtomicU64::new(0),
            checksum_bits: AtomicU64::new(0),
            event_cache: (0..cache_slots).map(|_| Mutex::new(None)).collect(),
        })
    }
}

fn sanitize_config(mut cfg: RustQueueProfileConfig) -> RustQueueProfileConfig {
    let default = RustQueueProfileConfig::default();
    if cfg.ticker_count == 0 {
        cfg.ticker_count = default.ticker_count;
    }
    if cfg.origins_per_ticker == 0 {
        cfg.origins_per_ticker = default.origins_per_ticker;
    }
    if cfg.event_stream_len == 0 {
        cfg.event_stream_len = default.event_stream_len;
    }
    if cfg.event_feature_count == 0 {
        cfg.event_feature_count = default.event_feature_count;
    }
    if cfg.batch_size == 0 {
        cfg.batch_size = default.batch_size;
    }
    cfg.realtime_read_workers = cfg.realtime_read_workers.max(1);
    cfg.prefetch_read_workers = cfg.prefetch_read_workers.max(1);
    cfg.realtime_process_workers = cfg.realtime_process_workers.max(1);
    cfg.prefetch_process_workers = cfg.prefetch_process_workers.max(1);
    cfg
}

fn pop_with_priority<T>(
    queues: &QueuePair<T>,
    worker_is_prefetch: bool,
    steal_counter: &AtomicU64,
) -> Option<T> {
    if let Some(job) = queues.realtime.try_pop() {
        if worker_is_prefetch {
            steal_counter.fetch_add(1, Ordering::Relaxed);
        }
        return Some(job);
    }
    queues.prefetch.try_pop()
}

fn enqueue_read(shared: &RuntimeShared, job: ReadJob) {
    shared.read_jobs_enqueued.fetch_add(1, Ordering::Relaxed);
    match job.priority {
        Priority::Realtime => shared.read_queues.realtime.push(job),
        Priority::Prefetch => shared.read_queues.prefetch.push(job),
    }
}

fn enqueue_process(shared: &RuntimeShared, job: ProcessJob) {
    shared.process_jobs_enqueued.fetch_add(1, Ordering::Relaxed);
    match job.data.priority {
        Priority::Realtime => shared.process_queues.realtime.push(job),
        Priority::Prefetch => shared.process_queues.prefetch.push(job),
    }
}

fn read_worker(shared: Arc<RuntimeShared>, worker_is_prefetch: bool) {
    loop {
        if shared.shutdown.load(Ordering::Acquire) && shared.read_queues.is_empty() {
            return;
        }
        let Some(job) = pop_with_priority(
            &shared.read_queues,
            worker_is_prefetch,
            &shared.read_priority_steals,
        ) else {
            thread::sleep(Duration::from_micros(200));
            continue;
        };
        if shared.cfg.read_sleep_us > 0 {
            thread::sleep(Duration::from_micros(shared.cfg.read_sleep_us as u64));
        }
        match job.priority {
            Priority::Realtime => shared.realtime_read_jobs.fetch_add(1, Ordering::Relaxed),
            Priority::Prefetch => shared.prefetch_read_jobs.fetch_add(1, Ordering::Relaxed),
        };
        let started = Instant::now();
        let data = load_ticker_data(&shared.cfg, job);
        shared
            .read_worker_ns
            .fetch_add(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
        shared.bytes_allocated.fetch_add(
            ((data.events.len() * std::mem::size_of::<f32>())
                + (data.ordinals.len() * std::mem::size_of::<u64>())) as u64,
            Ordering::Relaxed,
        );
        enqueue_process(&shared, ProcessJob { data });
        shared.read_jobs_finished.fetch_add(1, Ordering::Release);
    }
}

fn process_worker(shared: Arc<RuntimeShared>, worker_is_prefetch: bool) {
    loop {
        if shared.shutdown.load(Ordering::Acquire) && shared.process_queues.is_empty() {
            return;
        }
        let Some(job) = pop_with_priority(
            &shared.process_queues,
            worker_is_prefetch,
            &shared.process_priority_steals,
        ) else {
            thread::sleep(Duration::from_micros(200));
            continue;
        };
        if shared.cfg.process_sleep_us > 0 {
            thread::sleep(Duration::from_micros(shared.cfg.process_sleep_us as u64));
        }
        match job.data.priority {
            Priority::Realtime => shared.realtime_process_jobs.fetch_add(1, Ordering::Relaxed),
            Priority::Prefetch => shared.prefetch_process_jobs.fetch_add(1, Ordering::Relaxed),
        };
        let started = Instant::now();
        process_ticker_data(&shared, job.data);
        shared
            .process_worker_ns
            .fetch_add(started.elapsed().as_nanos() as u64, Ordering::Relaxed);
        shared.process_jobs_finished.fetch_add(1, Ordering::Release);
    }
}

fn load_ticker_data(cfg: &RustQueueProfileConfig, job: ReadJob) -> LoadedTickerData {
    let stream_len = cfg.event_stream_len.max(1) as usize;
    let feature_count = cfg.event_feature_count.max(1) as usize;
    let origins = job.origins.max(1);
    let rows = stream_len + origins as usize;
    let mut events = vec![0.0_f32; rows * feature_count];
    let mut ordinals = vec![0_u64; rows];
    let ticker_base = job.ticker_id as f32 * 0.001;
    for row in 0..rows {
        ordinals[row] = row as u64 + 1;
        let row_base = row * feature_count;
        for col in 0..feature_count {
            events[row_base + col] = ticker_base + row as f32 * 0.000_001 + col as f32 * 0.01;
        }
    }
    LoadedTickerData {
        ticker_id: job.ticker_id,
        origins,
        stream_len,
        feature_count,
        events: Arc::new(events),
        ordinals: Arc::new(ordinals),
        priority: job.priority,
    }
}

fn process_ticker_data(shared: &RuntimeShared, data: LoadedTickerData) {
    let mut scratch = vec![0.0_f32; data.stream_len * data.feature_count];
    let mut local_samples = 0_u64;
    let mut local_checksum = 0_u64;
    let mut local_rebuilds = 0_u64;
    let mut local_appends = 0_u64;
    let mut local_reused = 0_u64;
    if let Some(slot) = shared.event_cache.get(data.ticker_id as usize) {
        let mut guard = slot.lock().expect("event cache mutex poisoned");
        if guard.is_none() {
            local_rebuilds += 1;
            let end = data.stream_len;
            *guard = Some(EventTickerState {
                stream: data.events[0..end * data.feature_count].to_vec(),
                ordinals: data.ordinals[0..end].to_vec(),
                last_ordinal: data.ordinals[end - 1],
            });
        }
        let state = guard.as_mut().expect("ticker cache initialized");
        for origin_index in 0..data.origins as usize {
            let target_offset = data.stream_len - 1 + origin_index;
            let target_ordinal = data.ordinals[target_offset];
            if target_ordinal > state.last_ordinal {
                append_event_rows(state, &data, target_offset);
                local_appends += target_ordinal.saturating_sub(state.last_ordinal).max(1);
                state.last_ordinal = target_ordinal;
            } else {
                local_reused += 1;
            }
            scratch.copy_from_slice(&state.stream);
            local_checksum ^= sample_checksum_bits(&scratch, data.feature_count);
            local_samples += 1;
        }
    }
    shared.samples.fetch_add(local_samples, Ordering::Relaxed);
    shared
        .event_cache_rebuilds
        .fetch_add(local_rebuilds, Ordering::Relaxed);
    shared
        .event_cache_appends
        .fetch_add(local_appends, Ordering::Relaxed);
    shared
        .event_cache_reused
        .fetch_add(local_reused, Ordering::Relaxed);
    shared
        .checksum_bits
        .fetch_xor(local_checksum, Ordering::Relaxed);
}

fn append_event_rows(state: &mut EventTickerState, data: &LoadedTickerData, target_offset: usize) {
    let target_ordinal = data.ordinals[target_offset];
    if target_ordinal <= state.last_ordinal {
        return;
    }
    let count = (target_ordinal - state.last_ordinal) as usize;
    let stream_len = data.stream_len;
    let features = data.feature_count;
    let append_start = target_offset + 1 - count;
    if count >= stream_len {
        let start = (target_offset + 1 - stream_len) * features;
        let end = (target_offset + 1) * features;
        state.stream.copy_from_slice(&data.events[start..end]);
        state
            .ordinals
            .copy_from_slice(&data.ordinals[target_offset + 1 - stream_len..target_offset + 1]);
        return;
    }
    state
        .stream
        .copy_within(count * features..stream_len * features, 0);
    let target_tail_start = (stream_len - count) * features;
    let source_start = append_start * features;
    let source_end = (target_offset + 1) * features;
    state.stream[target_tail_start..].copy_from_slice(&data.events[source_start..source_end]);
    state.ordinals.copy_within(count..stream_len, 0);
    state.ordinals[stream_len - count..]
        .copy_from_slice(&data.ordinals[append_start..target_offset + 1]);
}

fn sample_checksum_bits(values: &[f32], feature_count: usize) -> u64 {
    if values.is_empty() {
        return 0;
    }
    let mut acc = 0_u64;
    let step = feature_count.max(1);
    for index in (0..values.len()).step_by(step * 64).take(32) {
        acc ^= values[index].to_bits() as u64;
        acc = acc.rotate_left(7);
    }
    acc
}

fn collect_stats(shared: &RuntimeShared, elapsed_ns: u64, status: i32) -> RustQueueProfileStats {
    let cache_tickers = shared
        .event_cache
        .iter()
        .filter(|slot| slot.lock().map(|guard| guard.is_some()).unwrap_or(false))
        .count() as u64;
    let samples = shared.samples.load(Ordering::Relaxed);
    let batches = samples / shared.cfg.batch_size.max(1) as u64;
    RustQueueProfileStats {
        status,
        elapsed_ns,
        read_jobs_enqueued: shared.read_jobs_enqueued.load(Ordering::Relaxed),
        read_jobs_finished: shared.read_jobs_finished.load(Ordering::Relaxed),
        process_jobs_enqueued: shared.process_jobs_enqueued.load(Ordering::Relaxed),
        process_jobs_finished: shared.process_jobs_finished.load(Ordering::Relaxed),
        realtime_read_jobs: shared.realtime_read_jobs.load(Ordering::Relaxed),
        prefetch_read_jobs: shared.prefetch_read_jobs.load(Ordering::Relaxed),
        realtime_process_jobs: shared.realtime_process_jobs.load(Ordering::Relaxed),
        prefetch_process_jobs: shared.prefetch_process_jobs.load(Ordering::Relaxed),
        read_priority_steals: shared.read_priority_steals.load(Ordering::Relaxed),
        process_priority_steals: shared.process_priority_steals.load(Ordering::Relaxed),
        read_worker_ns: shared.read_worker_ns.load(Ordering::Relaxed),
        process_worker_ns: shared.process_worker_ns.load(Ordering::Relaxed),
        samples,
        batches,
        cache_tickers,
        event_cache_rebuilds: shared.event_cache_rebuilds.load(Ordering::Relaxed),
        event_cache_appends: shared.event_cache_appends.load(Ordering::Relaxed),
        event_cache_reused: shared.event_cache_reused.load(Ordering::Relaxed),
        bytes_allocated: shared.bytes_allocated.load(Ordering::Relaxed),
        checksum_bits: shared.checksum_bits.load(Ordering::Relaxed),
    }
}

fn run_profile(cfg: RustQueueProfileConfig) -> RustQueueProfileStats {
    let cfg = sanitize_config(cfg);
    let shared = RuntimeShared::new(cfg);
    let started = Instant::now();
    let mut handles = Vec::new();
    for _ in 0..cfg.realtime_read_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || read_worker(worker_shared, false)));
    }
    for _ in 0..cfg.prefetch_read_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || read_worker(worker_shared, true)));
    }
    for _ in 0..cfg.realtime_process_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || process_worker(worker_shared, false)));
    }
    for _ in 0..cfg.prefetch_process_workers {
        let worker_shared = Arc::clone(&shared);
        handles.push(thread::spawn(move || process_worker(worker_shared, true)));
    }
    for ticker in 0..cfg.ticker_count {
        enqueue_read(
            &shared,
            ReadJob {
                ticker_id: ticker,
                origins: cfg.origins_per_ticker,
                priority: Priority::Realtime,
            },
        );
    }
    for offset in 0..cfg.prefetch_ticker_count {
        enqueue_read(
            &shared,
            ReadJob {
                ticker_id: cfg.ticker_count + offset,
                origins: cfg.origins_per_ticker,
                priority: Priority::Prefetch,
            },
        );
    }
    let total_read = cfg.ticker_count as u64 + cfg.prefetch_ticker_count as u64;
    loop {
        let read_done = shared.read_jobs_finished.load(Ordering::Acquire);
        let process_enqueued = shared.process_jobs_enqueued.load(Ordering::Acquire);
        let process_done = shared.process_jobs_finished.load(Ordering::Acquire);
        if read_done >= total_read && process_done >= process_enqueued {
            break;
        }
        thread::sleep(Duration::from_millis(1));
    }
    shared.shutdown.store(true, Ordering::Release);
    for handle in handles {
        let _ = handle.join();
    }
    collect_stats(&shared, started.elapsed().as_nanos() as u64, 0)
}

#[no_mangle]
pub extern "C" fn rolling_loader_rust_profile(
    config: *const RustQueueProfileConfig,
    stats: *mut RustQueueProfileStats,
) -> i32 {
    if stats.is_null() {
        return -1;
    }
    let cfg = if config.is_null() {
        RustQueueProfileConfig::default()
    } else {
        unsafe { *config }
    };
    let result = run_profile(cfg);
    unsafe {
        ptr::write(stats, result);
    }
    0
}

#[no_mangle]
pub extern "C" fn rolling_loader_rust_version(buffer: *mut c_char, buffer_len: usize) -> usize {
    let bytes = VERSION.as_bytes();
    if buffer.is_null() || buffer_len == 0 {
        return bytes.len();
    }
    let copy_len = bytes.len().min(buffer_len.saturating_sub(1));
    unsafe {
        ptr::copy_nonoverlapping(bytes.as_ptr(), buffer as *mut u8, copy_len);
        *buffer.add(copy_len) = 0;
    }
    bytes.len()
}
