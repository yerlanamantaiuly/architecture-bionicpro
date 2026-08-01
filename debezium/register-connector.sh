#!/bin/sh
set -eu

CONNECT_URL="${CONNECT_URL:-http://connect:8083}"
CONNECTOR_FILE="${CONNECTOR_FILE:-/debezium/connector-crm.json}"
NAME="bionicpro-crm-connector"

echo "Waiting for Kafka Connect at $CONNECT_URL ..."
i=0
until curl -sf "$CONNECT_URL/connectors" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 90 ]; then
    echo "Kafka Connect not ready"
    exit 1
  fi
  sleep 2
done

echo "Prepare Postgres for logical replication"
# best-effort; source-db cdc-prepare also runs
true

echo "Register / update connector: $NAME"
curl -sf -X DELETE "$CONNECT_URL/connectors/$NAME" >/dev/null 2>&1 || true
sleep 3

curl -sf -X POST -H "Content-Type: application/json" \
  --data @"$CONNECTOR_FILE" \
  "$CONNECT_URL/connectors"

echo
echo "Connector status:"
sleep 8
curl -sf "$CONNECT_URL/connectors/$NAME/status"
echo
echo "CDC connector registered."
