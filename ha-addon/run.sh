#!/bin/bash
set -e

echo "Starting FastAPI on port 8099..."
mkdir -p /data/ofx

exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8099 \
    --workers 1 \
    --log-level info
