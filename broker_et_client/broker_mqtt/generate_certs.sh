#!/bin/sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$DIR"

echo "Génération de l'autorité racine CA pour Pledge MQTTS..."
openssl req -new -x509 -days 3650 -extensions v3_ca -keyout "$DIR/ca.key" -out "$DIR/ca.crt" -subj "/C=FR/ST=IDF/L=Paris/O=Pledge/CN=Pledge-Root-CA" -nodes

echo "Génération de la clé et certificat du serveur Mosquitto..."
openssl req -new -out "$DIR/server.csr" -keyout "$DIR/server.key" -subj "/C=FR/ST=IDF/L=Paris/O=Pledge/CN=broker_zone" -nodes
openssl x509 -req -in "$DIR/server.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" -CAcreateserial -out "$DIR/server.crt" -days 3650

echo "Ajustement des permissions pour l'utilisateur Mosquitto..."
chmod 644 "$DIR"/*.crt "$DIR"/*.key 2>/dev/null || true

echo "Certificats MQTTS générés avec succès dans $DIR"

