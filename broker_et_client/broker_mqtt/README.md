# Courtier MQTT de Zone — Eclipse Mosquitto

[![Mosquitto](https://img.shields.io/badge/Mosquitto-2.0+-purple.svg)](https://mosquitto.org/)
[![Ports](https://img.shields.io/badge/Ports-1883%20%7C%208883-blue.svg)](#-ports--protocoles)
[![Security](https://img.shields.io/badge/Security-TLS%201.3%20%2F%20MQTTS-green.svg)](#-gestion-des-certificats-tls)

Ce service encapsule un broker **Eclipse Mosquitto** dédié à une zone géographique ou logique donnée dans l'infrastructure Pledge. Il constitue le bus de communication asynchrone du **plan de contrôle** : il collecte la télémétrie des nœuds Fog Edge et achemine les ordres d'administration émis par le serveur web central.

---

## 🏛️ Architecture & Rôle dans PLEDGE

Dans chaque zone géographique autonome (ex: *Zone 1*, *Zone 2*), une instance indépendante de ce broker est déployée :

```text
 ┌────────────────────────────────────────────────────────┐
 │           SERVEUR WEB CENTRAL (FastAPI Backend)        │
 │   - Souscrit simultanément à :                         │
 │     • telemetrie/Zone_1/#                              │
 │     • telemetrie/Zone_2/#                              │
 │   - Publie sur : pledge/commands/purge_session         │
 └───────────────────────────▲────────────────────────────┘
                             │ TCP / MQTT (:1883) ou MQTTS (:8883)
 ┌───────────────────────────┴────────────────────────────┐
 │               BROKER MOSQUITTO DE ZONE                 │
 │   - Port 1883 : Flux MQTT standard                     │
 │   - Port 8883 : Flux sécurisé MQTTS (TLS 1.2 / 1.3)    │
 │   - Persistance des messages dans /mosquitto/data      │
 └───────────────────────────▲────────────────────────────┘
                             │
 ┌───────────────────────────┴────────────────────────────┐
 │               NŒUDS FOG EDGE LOCAUX                    │
 │   - Publie toutes les 5s sa télémétrie :               │
 │     Topic : telemetrie/{zone_id}/{node_id}             │
 │     Payload : {ip, wss_port, cpu, storage_available}   │
 │   - Écoute les ordres : pledge/commands/purge_session  │
 └────────────────────────────────────────────────────────┘
```

> **Important :** Conformément aux principes de conception du projet, **aucun flux vidéo lourd ne transite par ce broker**. Seuls des métadonnées légères et des signaux de contrôle y circulent.

---

## 🔌 Ports & Protocoles

| Port Écoute | Protocole | Chiffrement | Utilisation |
| :---: | :---: | :---: | :--- |
| **`1883`** | MQTT | Non (TCP clair) | Télémétrie locale rapide et développement |
| **`8883`** | MQTTS | **TLS 1.2 / 1.3** | Communications inter-machines sécurisées en environnement de production |

---

## 📡 Taxonomie des Topics MQTT

| Topic | Sens | Fréquence | Contenu du Payload (JSON) |
| :--- | :---: | :---: | :--- |
| `telemetrie/{zone_id}/{node_id}` | Nœud Edge $\to$ Backend | 5 secondes | `{"ip_address": "192.168.1.20", "port_wss": 8444, "stockage_dispo_gb": 120.5, "cpu_usage_pct": 12.3}` |
| `pledge/commands/purge_session` | Backend $\to$ Nœuds Edge | Sur arrêt flux | `{"session_id": "alice@pledge.local"}` — Commande la libération du disque sur les nœuds hébergeant la session |
| `pledge/events/#` | Tout composant | Événementiel | Événements d'audit et notifications de statut |

---

## 🔒 Gestion des Certificats TLS

Le conteneur intègre un mécanisme d'auto-amorçage via son script d'entrée [`entrypoint.sh`](entrypoint.sh) :
1. **Démarrage sans certificats existants** : Si le répertoire `./certs/` est vide, une Autorité de Certification racine (`ca.crt`) et un certificat serveur (`server.crt` / `server.key`) sont automatiquement créés par OpenSSL au premier lancement.
2. **Utilisation en Production** : Vous pouvez déposer vos propres certificats validés dans `./certs/` :
   - `./certs/ca.crt` (Certificat de l'autorité racine)
   - `./certs/server.crt` (Certificat serveur)
   - `./certs/server.key` (Clé privée du serveur, permissions 644/600)
3. **Génération Manuelle** :
   - Sous Linux / Bash : `./generate_certs.sh`
   - Sous Windows PowerShell : `.\generate_certs.ps1`

---

## 🚀 Commandes de Démarrage

### Mode 1 : Lancement Local (Simulation Monomachine)
Ce mode mappe le port hôte sur `1883` et utilise `host.docker.internal` :

```bash
# Se placer dans le répertoire du broker
cd broker_et_client/broker_mqtt

# Démarrer le conteneur en arrière-plan
docker compose -f docker-compose.local.yml up -d --build

# Consulter les logs du broker
docker compose -f docker-compose.local.yml logs -f

# Arrêter le conteneur
docker compose -f docker-compose.local.yml down
```

### Mode 2 : Lancement en Réseau LAN Distribué (PC Dédié)
Dans un déploiement où chaque machine héberge une zone spécifique :

```bash
# Vérifier ou adapter le fichier .env
cp .env.example .env

# Démarrer le service Mosquitto
docker compose up -d --build

# Vérifier l'état de santé
docker compose ps
```

---

## 🔍 Validation & Tests Manuels

Pour tester la réception des messages avec les utilitaires `mosquitto-clients` :

```bash
# Écouter tous les messages de télémétrie de la Zone 1
mosquitto_sub -h localhost -p 1883 -t "telemetrie/Zone_1/#" -v

# Simuler l'envoi d'un signal de télémétrie d'un nœud Fog
mosquitto_pub -h localhost -p 1883 -t "telemetrie/Zone_1/Edge_Test" \
  -m '{"ip_address":"127.0.0.1","port_wss":8444,"stockage_dispo_gb":50.0,"cpu_usage_pct":5.2}'

# Tester la connexion TLS sur le port 8883
mosquitto_sub -h localhost -p 8883 --cafile certs/ca.crt -t "telemetrie/#" -v
```

---

## 🛠️ Dépannage Fréquent

- **`Permission denied: /mosquitto/certs/server.key`** :
  L'utilisateur Mosquitto tourne avec l'UID `1883`. Le script `entrypoint.sh` ajuste automatiquement les permissions (`chown -R 1883:1883 /mosquitto/certs`), mais sous Linux assurez-vous que le répertoire hôte `certs/` ne soit pas verrouillé en `root:root`.
- **`Bind: Une tentative d'accès à un socket de manière interdite` (Windows)** :
  Le port `1883` ou `8883` est déjà utilisé par un service local (ex: autre instance Mosquitto ou service IoT Windows). Arrêtez le service concurrent avec `netstat -ano | findstr 1883` ou changez le port dans `.env`.

