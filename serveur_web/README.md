# Plateforme Web Centrale — `serveur_web`

[![FastAPI](https://img.shields.io/badge/Backend-FastAPI%20:8000-009688.svg)](backend/README.md)
[![React](https://img.shields.io/badge/Frontend-React%2019%20:5173-61DAFB.svg)](frontend/README.md)
[![Docker](https://img.shields.io/badge/Docker-Compose%20v2-2496ED.svg)](#-commandes-de-lancement)

Ce répertoire regroupe la **plateforme applicative centrale** de Pledge :
1. **`backend/`** : L'API REST et le plan de signalisation FastAPI (gestion des utilisateurs, annuaire des nœuds Edge, souscription multi-broker MQTT, algorithme de partitionnement Fog Sharding).
2. **`frontend/`** : L'application web React 19 / Vite (studio de diffusion, lecteur vidéo MSE, moteur cryptographique client BN254 et tableau de bord d'intégrité en temps réel).

---

## 🏛️ Architecture & Rôle dans PLEDGE

La plateforme web centrale agit comme le **point d'entrée utilisateur** et le **plan de contrôle de signalisation** :

```text
                             [Navigateur Utilisateur]
                                 │              ▲
                 1. Authentification /          │ 3. Découverte de flux &
                    Allocation de nœuds         │    Lecture vidéo directe
                    (HTTP :8000)                │    (HTTP :5173)
                                 ▼              │
       ┌─────────────────────────────────────────────────────────┐
       │                SERVEUR WEB CENTRAL                      │
       │                                                         │
       │  ┌───────────────────────┐   ┌───────────────────────┐  │
       │  │   BACKEND FASTAPI     │   │    FRONTEND REACT     │  │
       │  │       (:8000)         │   │       (:5173)         │  │
       │  │ - Auth JWT & BDD      │   │ - Studio webcam       │  │
       │  │ - Signalisation flux  │   │ - Player vidéo MSE    │  │
       │  │ - Souscription MQTT   │   │ - Audit BN254 / SIS   │  │
       │  │ - Allocation Sharding │   │ - Agrégateur Shards   │  │
       │  └───────────▲───────────┘   └───────────────────────┘  │
       └──────────────┼──────────────────────────────────────────┘
                      │ Télémétrie MQTT (CPU, RAM, Disque)
                      ▼
         [Brokers MQTT & Nœuds Fog de Zone]
```

> **Principe fondamental :** Aucun bloc vidéo WebM n'est stocké ni acheminé par le serveur web central. Le backend aiguille les flux et délivre des jetons d'accès cryptographiques pour les WebSockets directs des nœuds Fog Edge.

---

## 📂 Structure du Répertoire

| Dossier | Composant | Technologies | Description |
| :--- | :--- | :--- | :--- |
| **[`backend/`](backend/README.md)** | Serveur d'API & Signalisation | FastAPI, SQLAlchemy, SQLite, Paho-MQTT, Pydantic | Gestion des comptes, annuaire des nœuds actifs, émission des jetons JWT, routage de flux avec affinité de zone et Fog Sharding. |
| **[`frontend/`](frontend/README.md)** | Application Web Client | React 19, Vite, @noble/curves, KaTeX, WebSockets | Interface utilisateur pour streamers et spectateurs, moteur cryptographique client (SIS et Hess-IBS) et audit public sans TPA. |

---

## ⚙️ Configuration Globale (`serveur_web/.env`)

Le fichier `.env` configure les liaisons entre le serveur web et les nœuds répartis :

```env
# URL publique du Backend accessible par le navigateur
LAN_URL_BACKEND=http://192.168.1.100:8000
VITE_API_URL=http://192.168.1.100:8000

# Ports d'écoute sur la machine hôte
BACKEND_PORT=8000
FRONTEND_PORT=5173

# Adresses des Brokers MQTT par Zone
BROKER_HOST_ZONE_1=192.168.1.10
BROKER_PORT_ZONE_1=1883
BROKER_TLS_ZONE_1=false
BROKER_HOST_ZONE_2=192.168.1.11
BROKER_PORT_ZONE_2=1883
BROKER_TLS_ZONE_2=false

# Serveurs Cryptographiques de Zone
CRYPTO_URL_ZONE_1=https://192.168.1.20:8443
CRYPTO_URL_ZONE_2=https://192.168.1.21:8443
CRYPTO_VERIFY_SSL=false

# Base de données (SQLite locale ou PostgreSQL distante)
DATABASE_URL=sqlite:///./data/app.db
EDGE_WS_SCHEME=ws
```

---

## 🚀 Commandes de Lancement

### Mode 1 : Lancement Local avec Docker Compose (Monomachine)
Ce mode démarre simultanément le Backend (FastAPI sur `:8000`) et le Frontend (Vite sur `:5173`) préconfigurés avec `host.docker.internal` :

```bash
cd serveur_web

# Démarrer le backend et le frontend en arrière-plan
docker compose -f docker-compose.local.yml up -d --build

# Consulter les logs unifiés
docker compose -f docker-compose.local.yml logs -f

# Arrêter les deux services
docker compose -f docker-compose.local.yml down
```

### Mode 2 : Lancement en Production / Réseau Physique LAN
```bash
cd serveur_web
cp .env.example .env
nano .env  # Adapter les adresses IP réelles de votre réseau local

docker compose up -d --build
```

### Mode 3 : Exécution Locale sans Docker (Bare-Metal)

#### 1. Démarrer le Backend
```bash
cd serveur_web/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

#### 2. Démarrer le Frontend (dans un autre terminal)
```bash
cd serveur_web/frontend
npm install
npm run dev -- --host
```

---

## 🔍 Vérification & Santé du Système

Après le démarrage, interrogez le endpoint de diagnostic du backend :

```bash
curl http://localhost:8000/health
```

Réponse attendue lorsque l'ensemble des liaisons est actif :
```json
{
  "status": "ok",
  "database": "ok",
  "mqtt": "connected",
  "mqtt_zones": {
    "Zone_1": "connected",
    "Zone_2": "connected"
  },
  "crypto_nodes": {
    "Zone_1": "ok",
    "Zone_2": "ok"
  }
}
```

Pour les spécifications complètes de chaque module :
- 👉 [Documentation technique du Backend FastAPI](backend/README.md)
- 👉 [Documentation technique du Frontend React](frontend/README.md)

