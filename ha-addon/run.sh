#!/usr/bin/with-contenv bashio

bashio::log.info "Starting UAE PDF to OFX API..."

PORT=$(bashio::config 'port')
bashio::log.info "Listening on port ${PORT}"

mkdir -p /data/ofx

exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --workers 1 \
    --log-level info
