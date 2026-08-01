#!/bin/sh
set -eu

MC_ALIAS="local"
ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
ACCESS_KEY="${MINIO_ROOT_USER:-minioadmin}"
SECRET_KEY="${MINIO_ROOT_PASSWORD:-minioadmin}"
BUCKET="${MINIO_BUCKET:-bionicpro-reports}"

echo "Waiting for MinIO at $ENDPOINT ..."
i=0
until mc alias set "$MC_ALIAS" "$ENDPOINT" "$ACCESS_KEY" "$SECRET_KEY" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "MinIO not ready"
    exit 1
  fi
  sleep 1
done

mc mb --ignore-existing "$MC_ALIAS/$BUCKET"
# Public GET so CDN/Nginx can fetch objects without signing
mc anonymous set download "$MC_ALIAS/$BUCKET"
echo "Bucket $BUCKET ready (public download)"
mc ls "$MC_ALIAS/$BUCKET" || true
