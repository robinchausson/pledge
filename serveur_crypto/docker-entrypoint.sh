#!/bin/sh
set -eu

cert_file="${TLS_CERT_FILE:-/certs/tls.crt}"
key_file="${TLS_KEY_FILE:-/certs/tls.key}"

mkdir -p "$(dirname "$cert_file")"
if [ ! -s "$cert_file" ] || [ ! -s "$key_file" ]; then
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -keyout "$key_file" -out "$cert_file" -subj "/CN=crypto-node"
fi

exec uvicorn crypto_node:app --host 0.0.0.0 --port 8443 \
    --ssl-certfile "$cert_file" --ssl-keyfile "$key_file"