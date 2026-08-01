-- Enable CDC privileges for Debezium (safe to re-run)
ALTER USER bionic WITH REPLICATION;

-- Replica identity FULL helps updates/deletes carry complete row images
ALTER TABLE crm.clients REPLICA IDENTITY FULL;
ALTER TABLE telemetry.sensor_events REPLICA IDENTITY FULL;
