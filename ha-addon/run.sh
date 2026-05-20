#!/usr/bin/with-contenv bashio

bashio::log.info "Starting UAE PDF to OFX API..."

mkdir -p /data/ofx

exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port 8099 \
    --workers 1 \
    --log-level info
