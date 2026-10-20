#!/bin/sh
set -e

CERT_DIR="/mosquitto/certs"
mkdir -p "$CERT_DIR"

if [ ! -s "$CERT_DIR/server.crt" ] || [ ! -s "$CERT_DIR/server.key" ]; then
    echo "[Mosquitto] Certificats TLS absents dans $CERT_DIR. Génération automatique du certificat serveur..."
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
        -keyout "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.crt" -subj "/CN=Pledge-Root-CA"
    openssl req -newkey rsa:2048 -nodes \
        -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" -subj "/CN=broker_zone"
    openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
        -CAcreateserial -out "$CERT_DIR/server.crt" -days 365
    chmod 644 "$CERT_DIR"/*.crt
    chmod 644 "$CERT_DIR"/*.key
    echo "[Mosquitto] Certificats générés avec succès."
fi

# S'assurer que l'utilisateur non-privilégié mosquitto (UID 1883) a accès aux certificats et aux données
chown -R mosquitto:mosquitto "$CERT_DIR" /mosquitto/data /mosquitto/log 2>/dev/null || true
chmod 644 "$CERT_DIR"/* 2>/dev/null || true

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf

