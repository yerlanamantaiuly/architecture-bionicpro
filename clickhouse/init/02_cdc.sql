-- Assignment 4: KafkaEngine + MaterializedView CDC mart
CREATE DATABASE IF NOT EXISTS bionicpro;

-- Re-apply friendly: drop Kafka consumers / MVs first (MergeTree data kept)
DROP VIEW IF EXISTS bionicpro.user_report_mart_cdc_mv;
DROP VIEW IF EXISTS bionicpro.telemetry_events_cdc_mv;
DROP VIEW IF EXISTS bionicpro.crm_clients_cdc_mv;
DROP TABLE IF EXISTS bionicpro.telemetry_events_kafka;
DROP TABLE IF EXISTS bionicpro.crm_clients_kafka;

-- CRM dimension from Debezium
CREATE TABLE IF NOT EXISTS bionicpro.crm_clients_cdc
(
    client_id Int32,
    username String,
    full_name String,
    email String,
    prosthesis_model String,
    region String,
    contract_start Date,
    is_active UInt8,
    __op String,
    _version DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(_version)
ORDER BY username;

-- Telemetry facts from Debezium
CREATE TABLE IF NOT EXISTS bionicpro.telemetry_events_cdc
(
    event_id Int64,
    username String,
    event_ts DateTime64(3, 'UTC'),
    event_date Date,
    steps Int32,
    active_seconds Int32,
    battery_pct Float32,
    load_cycles Int32,
    is_fall UInt8,
    __op String,
    _version DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(_version)
ORDER BY (username, event_date, event_id);

-- CDC reporting mart (aggregated by SummingMergeTree)
CREATE TABLE IF NOT EXISTS bionicpro.user_report_mart_cdc
(
    username String,
    report_date Date,
    steps_count Int64,
    active_seconds Int64,
    battery_pct_sum Float64,
    battery_samples Int64,
    load_cycles Int64,
    fall_events Int64,
    events_count Int64
) ENGINE = SummingMergeTree()
ORDER BY (username, report_date);

-- Kafka sources (Debezium unwrap → flat JSON)
CREATE TABLE IF NOT EXISTS bionicpro.crm_clients_kafka
(
    client_id Nullable(Int32),
    username Nullable(String),
    full_name Nullable(String),
    email Nullable(String),
    prosthesis_model Nullable(String),
    region Nullable(String),
    contract_start Nullable(Int32),
    is_active Nullable(Bool),
    __op Nullable(String),
    __table Nullable(String),
    __deleted Nullable(String)
) ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'bionicpro.crm.clients',
    kafka_group_name = 'ch_crm_clients',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 1,
    kafka_skip_broken_messages = 1000;

CREATE TABLE IF NOT EXISTS bionicpro.telemetry_events_kafka
(
    event_id Nullable(Int64),
    username Nullable(String),
    -- Debezium often emits ISO-8601 string for timestamptz
    event_ts Nullable(String),
    event_date Nullable(Int32),
    steps Nullable(Int32),
    active_seconds Nullable(Int32),
    battery_pct Nullable(Float32),
    load_cycles Nullable(Int32),
    is_fall Nullable(Bool),
    __op Nullable(String),
    __table Nullable(String),
    __deleted Nullable(String)
) ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'bionicpro.telemetry.sensor_events',
    kafka_group_name = 'ch_telemetry_events_v2',
    kafka_format = 'JSONEachRow',
    kafka_num_consumers = 1,
    kafka_skip_broken_messages = 100;

CREATE MATERIALIZED VIEW IF NOT EXISTS bionicpro.crm_clients_cdc_mv TO bionicpro.crm_clients_cdc AS
SELECT
    assumeNotNull(client_id) AS client_id,
    assumeNotNull(username) AS username,
    coalesce(full_name, '') AS full_name,
    coalesce(email, '') AS email,
    coalesce(prosthesis_model, '') AS prosthesis_model,
    coalesce(region, '') AS region,
    toDate(coalesce(contract_start, 0)) AS contract_start,
    toUInt8(coalesce(is_active, true)) AS is_active,
    coalesce(__op, 'r') AS __op,
    now() AS _version
FROM bionicpro.crm_clients_kafka
WHERE username IS NOT NULL AND coalesce(__deleted, 'false') != 'true';

-- event_ts: ISO-8601 string; event_date: days since epoch
CREATE MATERIALIZED VIEW IF NOT EXISTS bionicpro.telemetry_events_cdc_mv TO bionicpro.telemetry_events_cdc AS
SELECT
    assumeNotNull(event_id) AS event_id,
    assumeNotNull(username) AS username,
    parseDateTime64BestEffort(assumeNotNull(event_ts), 3, 'UTC') AS event_ts,
    toDate(coalesce(event_date, 0)) AS event_date,
    coalesce(steps, 0) AS steps,
    coalesce(active_seconds, 0) AS active_seconds,
    coalesce(battery_pct, 0) AS battery_pct,
    coalesce(load_cycles, 0) AS load_cycles,
    toUInt8(coalesce(is_fall, false)) AS is_fall,
    coalesce(__op, 'r') AS __op,
    now() AS _version
FROM bionicpro.telemetry_events_kafka
WHERE username IS NOT NULL AND coalesce(__deleted, 'false') != 'true';

-- Second MV from same Kafka table → mart (no batch extract from CRM OLTP)
CREATE MATERIALIZED VIEW IF NOT EXISTS bionicpro.user_report_mart_cdc_mv
TO bionicpro.user_report_mart_cdc AS
SELECT
    assumeNotNull(username) AS username,
    toDate(coalesce(event_date, 0)) AS report_date,
    toInt64(coalesce(steps, 0)) AS steps_count,
    toInt64(coalesce(active_seconds, 0)) AS active_seconds,
    toFloat64(coalesce(battery_pct, 0)) AS battery_pct_sum,
    toInt64(1) AS battery_samples,
    toInt64(coalesce(load_cycles, 0)) AS load_cycles,
    toInt64(toUInt8(coalesce(is_fall, false))) AS fall_events,
    toInt64(1) AS events_count
FROM bionicpro.telemetry_events_kafka
WHERE username IS NOT NULL AND coalesce(__deleted, 'false') != 'true';
