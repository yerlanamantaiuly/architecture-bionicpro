-- CRM + telemetry source data for BionicPRO reports ETL
CREATE SCHEMA IF NOT EXISTS crm;
CREATE SCHEMA IF NOT EXISTS telemetry;

CREATE TABLE crm.clients (
    client_id       SERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    full_name       TEXT NOT NULL,
    email           TEXT,
    prosthesis_model TEXT NOT NULL,
    region          TEXT NOT NULL DEFAULT 'RU',
    contract_start  DATE NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE telemetry.sensor_events (
    event_id        BIGSERIAL PRIMARY KEY,
    username        TEXT NOT NULL,
    event_ts        TIMESTAMPTZ NOT NULL,
    event_date      DATE GENERATED ALWAYS AS ((event_ts AT TIME ZONE 'UTC')::date) STORED,
    steps           INTEGER NOT NULL DEFAULT 0,
    active_seconds  INTEGER NOT NULL DEFAULT 0,
    battery_pct     REAL NOT NULL,
    load_cycles     INTEGER NOT NULL DEFAULT 0,
    is_fall         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_sensor_username_date ON telemetry.sensor_events (username, event_date);

INSERT INTO crm.clients (username, full_name, email, prosthesis_model, region, contract_start) VALUES
    ('prothetic1', 'Prothetic User One', 'prothetic1@example.com', 'BionicPRO X1', 'RU', '2024-01-15'),
    ('user1', 'Demo User', 'user1@example.com', 'BionicPRO S', 'RU', '2024-03-01'),
    ('admin1', 'Admin User', 'admin1@example.com', 'BionicPRO X1', 'RU', '2023-11-01'),
    ('john.doe', 'John Doe', 'john.doe@example.com', 'BionicPRO X2', 'KZ', '2024-06-01'),
    ('ye.amantaiuly', 'Ерлан Амантайулы', 'ye.amantaiuly@yandex.ru', 'BionicPRO X1', 'KZ', '2025-01-10');

-- 14 days of synthetic telemetry for each client
INSERT INTO telemetry.sensor_events (username, event_ts, steps, active_seconds, battery_pct, load_cycles, is_fall)
SELECT
    c.username,
    (CURRENT_DATE - g.day_offset) + (h.hour * INTERVAL '1 hour') + (m.minute * INTERVAL '1 minute'),
    (80 + (random() * 40)::int),
    (30 + (random() * 90)::int),
    GREATEST(15, 95 - g.day_offset * 2 - h.hour)::real,
    (1 + (random() * 3)::int),
    (random() < 0.02)
FROM crm.clients c
CROSS JOIN generate_series(0, 13) AS g(day_offset)
CROSS JOIN generate_series(8, 20, 4) AS h(hour)
CROSS JOIN (VALUES (0), (15)) AS m(minute);
