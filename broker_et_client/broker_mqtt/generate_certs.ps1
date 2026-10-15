$CertsDir = Join-Path $PSScriptRoot "certs"
if (-not (Test-Path $CertsDir)) {
    New-Item -ItemType Directory -Path $CertsDir -Force | Out-Null
}

Write-Host "Génération de l'autorité racine CA pour Pledge MQTTS..."
openssl req -new -x509 -days 3650 -extensions v3_ca -keyout (Join-Path $CertsDir "ca.key") -out (Join-Path $CertsDir "ca.crt") -subj "/C=FR/ST=IDF/L=Paris/O=Pledge/CN=Pledge-Root-CA" -nodes

Write-Host "Génération de la clé et certificat du serveur Mosquitto..."
openssl req -new -out (Join-Path $CertsDir "server.csr") -keyout (Join-Path $CertsDir "server.key") -subj "/C=FR/ST=IDF/L=Paris/O=Pledge/CN=broker_zone" -nodes
openssl x509 -req -in (Join-Path $CertsDir "server.csr") -CA (Join-Path $CertsDir "ca.crt") -CAkey (Join-Path $CertsDir "ca.key") -CAcreateserial -out (Join-Path $CertsDir "server.crt") -days 3650

Write-Host "Certificats MQTTS générés avec succès dans $CertsDir"

