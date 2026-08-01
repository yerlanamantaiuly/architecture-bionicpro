# CDC CRM → Kafka → ClickHouse (Задание 4)

Цель: выгрузки/витрина не бьют OLTP CRM напрямую — изменения уходят через **Debezium CDC**.

## Поток

```
Postgres (crm.clients, telemetry.sensor_events)
    → Debezium (Kafka Connect)
    → Kafka topics bionicpro.crm.clients / bionicpro.telemetry.sensor_events
    → ClickHouse KafkaEngine
    → MaterializedView → crm_clients_cdc / telemetry_events_cdc / user_report_mart_cdc
    → reports-api GET /reports
```

## Файлы

| Путь | Назначение |
|------|------------|
| [`debezium/connector-crm.json`](../debezium/connector-crm.json) | Postgres connector |
| [`debezium/register-connector.sh`](../debezium/register-connector.sh) | Регистрация в Connect |
| [`clickhouse/init/02_cdc.sql`](../clickhouse/init/02_cdc.sql) | KafkaEngine + MV + mart |
| [`source-db/cdc-prepare.sql`](../source-db/cdc-prepare.sql) | REPLICATION + replica identity |

## Проверка

```bash
# Connector
curl -s http://10.2.67.21:8083/connectors/bionicpro-crm-connector/status | head

# Витрина CDC
docker exec bionic-clickhouse-1 clickhouse-client --query \
  "SELECT username, count() FROM bionicpro.user_report_mart_cdc GROUP BY username"

# Живое изменение CRM → появится в CH без Airflow batch
docker exec bionic-source-db-1 psql -U bionic -d bionicpro -c \
  "UPDATE crm.clients SET region='CDC' WHERE username='prothetic1';"
sleep 5
docker exec bionic-clickhouse-1 clickhouse-client --query \
  "SELECT username, region FROM bionicpro.crm_clients_cdc FINAL WHERE username='prothetic1'"
```

## API

`REPORT_MART=user_report_mart_cdc` — отчёты читаются из CDC-витрины (+ join к `crm_clients_cdc`).
