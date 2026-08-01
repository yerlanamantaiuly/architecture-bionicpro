"""
BionicPRO reports ETL (Assignment 2 / Task 2).

Extract CRM clients + prosthesis telemetry from PostgreSQL,
load into ClickHouse staging, build per-user daily mart,
update etl_watermark for API period checks.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg2
import psycopg2.extras
from airflow import DAG
from airflow.operators.python import PythonOperator
from clickhouse_driver import Client as CHClient

logger = logging.getLogger(__name__)

SOURCE_PG = {
    "host": os.getenv("SOURCE_DB_HOST", "source-db"),
    "port": int(os.getenv("SOURCE_DB_PORT", "5432")),
    "dbname": os.getenv("SOURCE_DB_NAME", "bionicpro"),
    "user": os.getenv("SOURCE_DB_USER", "bionic"),
    "password": os.getenv("SOURCE_DB_PASSWORD", "bionic"),
}

CH_HOST = os.getenv("CLICKHOUSE_HOST", "clickhouse")
CH_PORT = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CH_USER = os.getenv("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
CH_DB = os.getenv("CLICKHOUSE_DB", "bionicpro")

default_args = {
    "owner": "bionicpro",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
}


def _pg():
    return psycopg2.connect(**SOURCE_PG)


def _ch() -> CHClient:
    return CHClient(
        host=CH_HOST,
        port=CH_PORT,
        user=CH_USER,
        password=CH_PASSWORD or "",
        database=CH_DB,
    )


def extract_load_crm(**_context) -> int:
    with _pg() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT client_id, username, full_name, COALESCE(email, '') AS email,
                   prosthesis_model, region, contract_start, is_active::int AS is_active
            FROM crm.clients
            """
        )
        rows = cur.fetchall()

    if not rows:
        logger.warning("No CRM clients found")
        return 0

    ch = _ch()
    ch.execute("TRUNCATE TABLE crm_clients")
    payload = [
        (
            int(r["client_id"]),
            r["username"],
            r["full_name"],
            r["email"],
            r["prosthesis_model"],
            r["region"],
            r["contract_start"],
            int(r["is_active"]),
        )
        for r in rows
    ]
    ch.execute(
        """
        INSERT INTO crm_clients
        (client_id, username, full_name, email, prosthesis_model, region, contract_start, is_active)
        VALUES
        """,
        payload,
    )
    logger.info("Loaded %s CRM clients into ClickHouse", len(payload))
    return len(payload)


def extract_load_telemetry(**_context) -> int:
    with _pg() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT event_id, username, event_ts, event_date, steps, active_seconds,
                   battery_pct, load_cycles, is_fall::int AS is_fall
            FROM telemetry.sensor_events
            ORDER BY event_id
            """
        )
        rows = cur.fetchall()

    if not rows:
        logger.warning("No telemetry events found")
        return 0

    ch = _ch()
    ch.execute("TRUNCATE TABLE telemetry_events")
    # chunk inserts
    batch_size = 5000
    total = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i : i + batch_size]
        payload = [
            (
                int(r["event_id"]),
                r["username"],
                r["event_ts"].replace(tzinfo=None) if hasattr(r["event_ts"], "replace") else r["event_ts"],
                r["event_date"],
                int(r["steps"]),
                int(r["active_seconds"]),
                float(r["battery_pct"]),
                int(r["load_cycles"]),
                int(r["is_fall"]),
            )
            for r in chunk
        ]
        ch.execute(
            """
            INSERT INTO telemetry_events
            (event_id, username, event_ts, event_date, steps, active_seconds,
             battery_pct, load_cycles, is_fall)
            VALUES
            """,
            payload,
        )
        total += len(payload)

    logger.info("Loaded %s telemetry events into ClickHouse", total)
    return total


def build_mart(**_context) -> int:
    batch_id = uuid4().hex[:12]
    ch = _ch()
    # Replace mart contents for a clean rebuild each run
    ch.execute("TRUNCATE TABLE user_report_mart")
    ch.execute(
        """
        INSERT INTO user_report_mart
        (username, full_name, email, prosthesis_model, region, report_date,
         steps_count, active_minutes, battery_avg, load_cycles, fall_events,
         events_count, etl_batch_id, processed_at)
        SELECT
            t.username,
            any(c.full_name) AS full_name,
            any(c.email) AS email,
            any(c.prosthesis_model) AS prosthesis_model,
            any(c.region) AS region,
            t.event_date AS report_date,
            sum(t.steps) AS steps_count,
            intDiv(sum(t.active_seconds), 60) AS active_minutes,
            avg(t.battery_pct) AS battery_avg,
            sum(t.load_cycles) AS load_cycles,
            sum(t.is_fall) AS fall_events,
            count() AS events_count,
            %(batch_id)s AS etl_batch_id,
            now() AS processed_at
        FROM telemetry_events AS t
        INNER JOIN crm_clients AS c ON c.username = t.username
        GROUP BY t.username, t.event_date
        """,
        {"batch_id": batch_id},
    )
    count = ch.execute("SELECT count() FROM user_report_mart FINAL")[0][0]
    logger.info("Mart rebuilt, batch=%s rows(final)=%s", batch_id, count)
    return int(count)


def update_watermark(**_context) -> str:
    ch = _ch()
    rows = ch.execute("SELECT max(event_date) FROM telemetry_events")
    max_date = rows[0][0] if rows and rows[0][0] else None
    if max_date is None:
        logger.warning("No telemetry dates — watermark not updated")
        return ""

    ch.execute(
        """
        INSERT INTO etl_watermark (pipeline, max_source_date, updated_at)
        VALUES
        """,
        [("bionicpro_reports_etl", max_date, datetime.utcnow())],
    )
    logger.info("Watermark set to %s", max_date)
    return str(max_date)


with DAG(
    dag_id="bionicpro_reports_etl",
    default_args=default_args,
    description="CRM + telemetry → ClickHouse user_report_mart",
    schedule_interval="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["bionicpro", "reports", "etl"],
) as dag:
    t_crm = PythonOperator(task_id="extract_load_crm", python_callable=extract_load_crm)
    t_tel = PythonOperator(
        task_id="extract_load_telemetry", python_callable=extract_load_telemetry
    )
    t_mart = PythonOperator(task_id="build_user_report_mart", python_callable=build_mart)
    t_wm = PythonOperator(task_id="update_etl_watermark", python_callable=update_watermark)

    [t_crm, t_tel] >> t_mart >> t_wm
