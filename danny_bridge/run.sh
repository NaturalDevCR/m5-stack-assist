#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
# Arranque de la app: resuelve el broker MQTT y lanza el puente con /data/options.json.
set -e

if bashio::config.has_value 'mqtt_host'; then
    bashio::log.info "MQTT: broker configurado manualmente"
    MQTT_HOST="$(bashio::config 'mqtt_host')"
    MQTT_PORT="$(bashio::config 'mqtt_port' 1883)"
    MQTT_USERNAME="$(bashio::config 'mqtt_username' '')"
    MQTT_PASSWORD="$(bashio::config 'mqtt_password' '')"
elif bashio::services.available 'mqtt'; then
    bashio::log.info "MQTT: usando el broker de Home Assistant (servicio mqtt)"
    MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
else
    bashio::log.warning "MQTT: no hay broker; instala la app Mosquitto o configura mqtt_host"
    MQTT_HOST=""
fi
export MQTT_HOST MQTT_PORT MQTT_USERNAME MQTT_PASSWORD

exec python3 -m danny_bridge addon --options /data/options.json
