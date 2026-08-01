CREATE DATABASE IF NOT EXISTS bionicpro;

CREATE TABLE IF NOT EXISTS bionicpro.crm_clients
(
    client_id UInt32,
    username String,
    full_name String,
    email String,
    prosthesis_model String,
    region String,
    contract_start Date,
    is_active UInt8,
    loaded_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY username;

CREATE TABLE IF NOT EXISTS bionicpro.telemetry_events
(
    event_id UInt64,
    username String,
    event_ts DateTime,
    event_date Date,
    steps UInt32,
    active_seconds UInt32,
    battery_pct Float32,
    load_cycles UInt32,
    is_fall UInt8,
    loaded_at DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(loaded_at)
ORDER BY (username, event_date, event_id);

-- Mart: one row per user per day — fast lookup by username
CREATE TABLE IF NOT EXISTS bionicpro.user_report_mart
(
    username String,
    full_name String,
    email String,
    prosthesis_model String,
    region String,
    report_date Date,
    steps_count UInt64,
    active_minutes UInt32,
    battery_avg Float32,
    load_cycles UInt64,
    fall_events UInt32,
    events_count UInt32,
    etl_batch_id String,
    processed_at DateTime
) ENGINE = ReplacingMergeTree(processed_at)
ORDER BY (username, report_date);

CREATE TABLE IF NOT EXISTS bionicpro.etl_watermark
(
    pipeline String,
    max_source_date Date,
    updated_at DateTime
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY pipeline;
