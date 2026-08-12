#!/bin/bash
# eshu-logger — independent health heartbeat
# Reads DASHBOARD_URL from /etc/eshu/dashboard_url.
# Reports gateway health to the dashboard via POST /api/gateway-heartbeat.
# Intentionally simple: no sed replacements, no template variables.
# Survives all gateway/poller script updates.

set -eo pipefail

DASHBOARD_URL=$(cat /etc/eshu/dashboard_url 2>/dev/null || echo "")
if [ -z "$DASHBOARD_URL" ]; then
    logger -t eshu-logger "No /etc/eshu/dashboard_url — exiting. Set DASHBOARD_URL and rerun."
    exit 1
fi

TARGET_IP=$(hostname -I | awk '{print $1}')
HOST_NAME=$(hostname)
INTERVAL=30

while true; do
    POLLER_OK=0; GATEWAY_OK=0; CAN_REACH=0

    systemctl is-active --quiet eshu-poller.service 2>/dev/null && POLLER_OK=1
    [ -f /usr/local/bin/eshu-gateway.sh ] && bash -n /usr/local/bin/eshu-gateway.sh >/dev/null 2>&1 && GATEWAY_OK=1
    curl -m 5 -s "$DASHBOARD_URL/api/version" >/dev/null 2>&1 && CAN_REACH=1

    logger -t eshu-logger "Heartbeat: poller=$POLLER_OK gw=$GATEWAY_OK reach=$CAN_REACH"

    curl -m 5 -s -X POST "$DASHBOARD_URL/api/gateway-heartbeat" \
        -H "Content-Type: application/json" \
        -d "{\"ip\":\"$TARGET_IP\",\"hostname\":\"$HOST_NAME\",\"poller_ok\":$POLLER_OK,\"gateway_ok\":$GATEWAY_OK,\"can_reach\":$CAN_REACH}" \
        >/dev/null 2>&1 || true

    sleep "$INTERVAL"
done
