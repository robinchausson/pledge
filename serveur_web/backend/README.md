# Backend FastAPI & Plan de Signalisation — `serveur_web/backend`

[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/ORM-SQLAlchemy-red.svg)](https://www.sqlalchemy.org/)
[![Paho-MQTT](https://img.shields.io/badge/MQTT-Paho%20Client-blue.svg)](https://eclipse.dev/paho/)
[![JWT](https://img.shields.io/badge/Auth-JWT%20Tokens-black.svg)](https://jwt.io/)

Le **Backend Pledge** est le chef d'orchestre du plan de contrôle central. Conçu avec **FastAPI**, il assure l'authentification des utilisateurs, la gestion de l'annuaire des nœuds Fog Edge via télémétrie MQTT, et orchestre le **plan de signalisation décentralisé** et le **partitionnement multi-nœuds (Fog Sharding)**.

---

## 🏛️ Rôles & Responsabilités

```text
       [Navigateur Client : Streamer ou Viewer]
                          │
       1. Demande d'allocation ou de découverte
          (POST /api/stream/allocate ou GET /api/stream/discover)
                          ▼
 ┌─────────────────────────────────────────────────────────┐
 │               BACKEND FASTAPI (main.py)                 │
 │                                                         │
 │  1. Authentification & Profils :                        │
 │     - Comptes, rôles (streamer, viewer, admin)          │
 │     - Déclaration et persistance de la zone active      │
 │                                                         │
 │  2. Annuaire des Nœuds Fog (models.py) :                │
 │     - Ingestion télémétrie MQTT multi-zones             │
 │     - Score de performance : Disque Go - (CPU% / 10)    │
 │                                                         │
 │  3. Routeur de Signalisation (stream_router.py) :       │
 │     - Aiguillage strict vers la zone géographique       │
 │     - Partitionnement Fog Sharding (K nœuds Fog alloués)│
 │     - Génération de jetons d'accès JWT à courte durée   │
 └────────────────────────────▲────────────────────────────┘
                              │
               Télémétrie MQTT│ (telemetrie/{zone_id}/#)
                              ▼
                [Brokers Mosquitto de Zone]
```

---

## 📊 Modèle de Données (SQLAlchemy)

La base de données (SQLite par défaut dans `./data/app.db` ou PostgreSQL) structure l'état du système :

| Table | Champs Clés | Description |
| :--- | :--- | :--- |
| **`users`** | `id`, `email`, `hashed_password`, `role`, `current_zone` | Utilisateurs enregistrés et zone géographique déclarée (`Zone_1` ou `Zone_2`). |
| **`edge_nodes`** | `node_id`, `zone_id`, `ip_address`, `wss_port`, `storage_available_gb`, `cpu_usage_pct`, `last_seen` | Annuaire dynamique des nœuds Fog mis à jour en temps réel par les messages MQTT. |
| **`stream_sessions`** | `session_id`, `owner_email`, `node_id`, `zone_id`, `current_zone`, `status`, `last_active` | Sessions de diffusion en direct actives (`live`) ou arrêtées (`stopped`). |
| **`data_locations`** | `id`, `client_id`, `node_id` | Table de correspondance indiquant quel nœud Fog détient quels fragments de flux (indispensable pour le Fog Sharding). |

---

## 📡 API REST & Endpoints

### 1. Authentification & Zones
- **`POST /auth/register`** : Inscription d'un utilisateur avec spécification de rôle et de zone initiale.
- **`POST /auth/login`** : Connexion par mot de passe et émission d'un cookie HTTP-Only JWT.
- **`GET /auth/me`** : Profil de l'utilisateur connecté et sa zone actuelle.
- **`PUT /auth/zone`** : Changement dynamique de zone géographique pour l'utilisateur en cours.

### 2. Signalisation & Découverte de Streaming (Section 3.5 & Option B)
- **`POST /api/stream/allocate`** :
  - Appelé par le diffuseur avant de lancer sa caméra.
  - Détecte l'ensemble des nœuds Fog Edge actifs dans la topologie.
  - Retourne le nœud principal optimal ainsi que la liste `allocated_nodes` (`is_sharded=True`, `shard_count=K`).
  - Génère un jeton JWT d'upload individuel pour chaque nœud shard.
- **`GET /api/stream/discover/{session_id}`** :
  - Appelé par le spectateur pour localiser le flux.
  - Résout tous les nœuds détenant des fragments vidéo via la table `data_locations`.
  - Retourne les URLs directes de téléchargement (flux original et transcodé 720p) pour chaque shard.
- **`POST /api/stream/heartbeat/{session_id}`** : Maintien en vie de la session active.
- **`POST /api/stream/stop/{session_id}`** : Marque la session comme arrêtée et émet un ordre MQTT `pledge/commands/purge_session` pour libérer le disque sur les nœuds Edge.

### 3. Diagnostic & Supervision
- **`GET /health`** : Contrôle de santé complet de la base, de l'état des connexions MQTT multi-zones et de la disponibilité des serveurs cryptographiques.
- **`GET /api/admin/nodes`** : Liste complète des nœuds Fog Edge répertoriés avec leurs métriques.
- **`GET /api/admin/streams`** : Liste des flux vidéo actuellement en direct.

---

## ⚙️ Configuration & Variables d'Environnement

Définies dans `serveur_web/.env` :

| Variable | Exemple | Rôle |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///./data/app.db` | Chaîne de connexion à la base de données |
| `BROKER_HOST_ZONE_1` | `127.0.0.1` | Adresse du broker MQTT de la Zone 1 |
| `BROKER_PORT_ZONE_1` | `1883` | Port du broker MQTT de la Zone 1 |
| `BROKER_HOST_ZONE_2` | `192.168.1.11` | Adresse du broker MQTT de la Zone 2 |
| `CRYPTO_URL_ZONE_1` | `https://127.0.0.1:8443` | URL du serveur cryptographique Zone 1 |
| `CRYPTO_URL_ZONE_2` | `https://192.168.1.21:8443` | URL du serveur cryptographique Zone 2 |
| `EDGE_WS_SCHEME` | `ws` | Schéma par défaut pour les URLs WebSocket générées |

---

## 🚀 Commandes de Démarrage

### Lancement avec Docker Compose
Depuis la racine du dossier `serveur_web/` :

```bash
# Démarrer le conteneur backend
docker compose -f docker-compose.local.yml up -d --build backend

# Consulter les logs du backend
docker compose -f docker-compose.local.yml logs -f backend
```

### Lancement en Développement Local (Python Nu)
```bash
cd serveur_web/backend

# Installer les dépendances
pip install -r requirements.txt

# Initialiser la base et lancer le serveur de dev avec rechargement à chaud
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Tests Unitaires & Validation Backend

Le backend est couvert par les tests Pytest situés dans `tests/` :
```bash
# Tester spécifiquement l'API Backend et l'authentification
pytest tests/test_backend_api.py -v

# Tester l'administration et le client MQTT
pytest tests/test_backend_admin_mqtt.py -v

# Tester la décentralisation et le plan de signalisation
pytest tests/test_phase4_decentralization.py -v
```

