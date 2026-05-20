#!/bin/bash
set -e

PORT=${PORT:-8099}

echo "Starting FastAPI on port ${PORT}..."
mkdir -p /data/ofx

exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --workers 1 \
    --log-level info
