# Guide de Déploiement Multi-Zone Distribué (LAN Réseau Physique) - Pledge

Ce document référence l'ensemble des procédures, configurations et commandes pour déployer l'architecture décentralisée multi-zone **Pledge** sur des machines physiques indépendantes au sein d'un réseau local (LAN), ou sur une seule machine en simulation multi-zone.

L'infrastructure implémente le modèle **Fog Computing décentralisé** et le schéma d'intégrité vérifiable (Sellami et al., *FGCS 2024*).

---

## 🏗️ Topologie Multi-Zone & Architecture des Composants

L'architecture est structurée en **deux zones géographiques ou logiques autonomes** et un **plan de coordination central** :

```
                        ┌──────────────────────────────────────────────┐
                        │         SERVEUR WEB CENTRAL (LAN)            │
                        │    Backend FastAPI (8000) & React UI (5173)   │
                        └──────────────┬────────────────┬──────────────┘
                                       │                │
            ┌──────────────────────────┘                └──────────────────────────┐
            ▼                                                                      ▼
 ╔══════════════════════════════════════════╗               ╔══════════════════════════════════════════╗
 ║        ZONE 1 : PC Fixe Principal        ║               ║       ZONE 2 : Nvidia Jetson / Second PC ║
 ╠══════════════════════════════════════════╣               ╠══════════════════════════════════════════╣
 ║ 1. Broker MQTT Zone 1 (Port 1883 / 8883) ║               ║ 1. Broker MQTT Zone 2 (Port 1883 / 8883) ║
 ║ 2. Crypto Node Zone 1 (Port 8443)        ║               ║ 2. Crypto Node Zone 2 (Port 8443)        ║
 ║    -> Seed Scalar : 123456789            ║               ║    -> Seed Scalar : 987654321            ║
 ║ 3. Nœud Edge Zone 1   (Port 8444)        ║               ║ 3. Nœud Edge Zone 2   (Port 8444 ou 8447)║
 ║    -> Stockage / Streaming direct WS/WSS ║               ║    -> Stockage / Streaming direct WS/WSS ║
 ╚══════════════════════════════════════════╝               ╚══════════════════════════════════════════╝
```

1. **Brokers MQTT Décentralisés (Mosquitto par zone)** :
   - Chaque zone possède son propre bus de messagerie pour isoler le trafic de télémétrie locale.
   - Le backend central s'abonne simultanément à tous les brokers de zone (`telemetrie/Zone_1/#`, `telemetrie/Zone_2/#`).
2. **Serveurs Cryptographiques de Zone (FastAPI / BN128 / SIS L'Ecuyer)** :
   - Chaque zone génère ses clés publiques maîtresses $PK_s$ via son propre scalaire secret $t_s$.
   - **Zone 1** : `CRYPTO_SECRET_SCALAR=123456789`
   - **Zone 2** : `CRYPTO_SECRET_SCALAR=987654321`
   - Délivrent les clés de délégation de signature aux nœuds Fog lors de la mobilité d'un utilisateur.
3. **Nœuds Fog Edge de Stockage & Streaming (WebSocket direct)** :
   - Reçoivent les flux vidéo directement des streamers (contournement du serveur central).
   - Effectuent le transcodage 720p local et la re-signature déléguée des blocs vidéo.
4. **Serveur Web Central (Backend FastAPI & Frontend React)** :
   - Plan de contrôle, authentification JWT, gestion de session et aiguillage intelligent des streamers vers le nœud Edge optimal.

---

## 🛡️ Matrice des Ports Réseau & Pare-feu LAN

### Cas A : Déploiement Physique Multi-Machine (1 Machine par Zone + 1 Machine Web)

Dans un déploiement où chaque zone tourne sur un hôte physique distinct (ex: PC fixe pour Zone 1, Nvidia Jetson pour Zone 2, Ordinateur portable pour le Web) :

| Hôte / Machine | Service Déployé | Port Écoute | Protocole | Rôle & Description |
| :--- | :--- | :--- | :--- | :--- |
| **PC Fixe (Zone 1)** | Broker MQTT Zone 1 | `1883` / `8883` | TCP / MQTT(S) | Bus de messages et télémétrie Zone 1 |
| **PC Fixe (Zone 1)** | Crypto Node Zone 1 | `8443` | TCP / HTTPS | Preuves SIS et signature Hess-IBS Zone 1 |
| **PC Fixe (Zone 1)** | Nœud Edge Zone 1 | `8444` | TCP / WS(S) | Upload / Download direct des flux vidéo |
| **Jetson (Zone 2)** | Broker MQTT Zone 2 | `1883` / `8883` | TCP / MQTT(S) | Bus de messages et télémétrie Zone 2 |
| **Jetson (Zone 2)** | Crypto Node Zone 2 | `8443` | TCP / HTTPS | Preuves SIS et signature Hess-IBS Zone 2 |
| **Jetson (Zone 2)** | Nœud Edge Zone 2 | `8444` | TCP / WS(S) | Upload / Download direct des flux vidéo |
| **Serveur Web** | Backend API FastAPI | `8000` | TCP / HTTP & WS | Contrôleur central, Auth, Signalisation |
| **Serveur Web** | Frontend Vite / React | `5173` | TCP / HTTP | Interface web utilisateur |

> **Pare-feu :** Assurez-vous d'ouvrir ces ports dans le pare-feu (`ufw` sous Ubuntu/JetPack, Pare-feu Windows avec fonctions avancées sous Windows).

---

### Cas B : Déploiement Colocalisé (Toutes les Zones sur le Même Ordinateur)

Pour exécuter les deux zones simultanément sur la même machine hôte (via `docker compose up -d` ou `docker-compose.local.yml`), les ports hôtes sont séparés pour éviter les conflits :

| Service | Port Hôte Zone 1 | Port Hôte Zone 2 | Port Interne Conteneur |
| :--- | :--- | :--- | :--- |
| **Broker Mosquitto** | `1883` / `8883` | `1884` / `8884` | `1883` / `8883` |
| **Serveur Crypto** | `8443` | `8445` | `8443` |
| **Nœud Fog Edge** | `8444` | `8447` | `8444` (Z1) / `8447` (Z2) |
| **Backend Web** | - | - | `8000` |
| **Frontend Web** | - | - | `5173` |

---

## 📋 Ordre de Démarrage Impératif

Pour garantir l'établissement des liaisons réseau sans timeout :

```
Étape 1 : Brokers MQTT (Zone 1 & 2)
   │
   ▼
Étape 2 : Serveurs Crypto (Zone 1 & 2)
   │
   ▼
Étape 3 : Nœuds Fog Edge (Zone 1 & 2)
   │
   ▼
Étape 4 : Serveur Web Central (Backend + Frontend)
```

---

## 🚀 Procédure de Déploiement Multi-Machine (PC Fixe + Jetson)

### Exemple d'adressage IP du réseau local :
- **PC Fixe (Zone 1) :** `192.168.1.10` (Broker) / `192.168.1.20` (Crypto & Edge)
- **Nvidia Jetson (Zone 2) :** `192.168.1.11` (Broker) / `192.168.1.21` (Crypto & Edge)
- **Machine Web Centrale :** `192.168.1.100`

---

### Étape 1 : Déploiement de la Zone 1 (PC Fixe Principal)

Sur le PC fixe hébergeant la Zone 1 :

#### 1.1 Broker MQTT Zone 1
```bash
cd broker_et_client/broker_mqtt
cp .env.example .env
# Vérifiez que MQTT_PORT=1883
docker compose up -d
```

#### 1.2 Serveur Crypto Zone 1
```bash
cd ../../serveur_crypto
cp .env.example .env
# Configuration dans .env :
# ZONE_ID=Zone_1
# CRYPTO_PORT=8443
# CRYPTO_SECRET_SCALAR=123456789
docker compose up -d --build
```
Vérification :
```bash
curl -k https://localhost:8443/health
# Réponse attendue : {"status":"ok","initialized":true,"curve":"bn128","version":"1.0"}
```

#### 1.3 Nœud Edge Zone 1
```bash
cd ../broker_et_client/client_mqtt
cp .env.example .env
# Configuration dans .env :
# NODE_ID=Noeud_Edge_Zone_1
# ZONE_ID=Zone_1
# NODE_HOST=192.168.1.20      # IP LAN réelle du PC fixe
# EDGE_WS_PORT=8444
# BROKER_HOST=192.168.1.10    # IP du Broker Zone 1
# CRYPTO_URL=https://192.168.1.20:8443
docker compose up -d --build
```

---

### Étape 2 : Déploiement de la Zone 2 (Nvidia Jetson / Second PC)

Sur la Nvidia Jetson (architecture ARM64 / aarch64) ou sur le second PC :

> **Note Jetson :** Les images Docker Python s'exécutent de façon transparente en multi-architecture sous Linux aarch64. `docker compose` compilera localement les modules C/C++ requis (gcc, py_ecc).

#### 2.1 Broker MQTT Zone 2
```bash
cd broker_et_client/broker_mqtt
cp .env.example .env
# Si hébergé sur une IP propre : MQTT_PORT=1883
docker compose up -d
```

#### 2.2 Serveur Crypto Zone 2
```bash
cd ../../serveur_crypto
cp .env.example .env
# Configuration dans .env :
# ZONE_ID=Zone_2
# CRYPTO_PORT=8443
# CRYPTO_SECRET_SCALAR=987654321
docker compose up -d --build
```
Vérification :
```bash
curl -k https://localhost:8443/health
```

#### 2.3 Nœud Edge Zone 2
```bash
cd ../broker_et_client/client_mqtt
cp .env.example .env
# Configuration dans .env :
# NODE_ID=Noeud_Edge_Jetson_Zone_2
# ZONE_ID=Zone_2
# NODE_HOST=192.168.1.21      # IP LAN réelle de la Jetson
# EDGE_WS_PORT=8444
# BROKER_HOST=192.168.1.11    # IP du Broker Zone 2
# CRYPTO_URL=https://192.168.1.21:8443
docker compose up -d --build
```

---

### Étape 3 : Déploiement du Serveur Web Central

Sur la machine centrale hébergeant le Backend et le Frontend :

#### 3.1 Configuration des variables multi-zone
```bash
cd serveur_web
cp .env.example .env
```
Éditez le fichier `serveur_web/.env` avec les coordonnées réelles des deux zones :
```env
# URL d'accès public
LAN_URL_BACKEND=http://192.168.1.100:8000
VITE_API_URL=http://192.168.1.100:8000

BACKEND_PORT=8000
FRONTEND_PORT=5173

# --- BROKERS MQTT PAR ZONE ---
BROKER_HOST_ZONE_1=192.168.1.10
BROKER_PORT_ZONE_1=1883
BROKER_TLS_ZONE_1=false

BROKER_HOST_ZONE_2=192.168.1.11
BROKER_PORT_ZONE_2=1883
BROKER_TLS_ZONE_2=false

# --- SERVEURS CRYPTO PAR ZONE ---
CRYPTO_URL_ZONE_1=https://192.168.1.20:8443
CRYPTO_URL_ZONE_2=https://192.168.1.21:8443
CRYPTO_VERIFY_SSL=false

DATABASE_URL=sqlite:///./data/app.db
EDGE_WS_SCHEME=ws
```

#### 3.2 Lancement
```bash
docker compose up -d --build
```

---

## 💻 Démarrage Tout-en-Un (Docker Compose Global ou Local)

Si vous souhaitez démarrer l'ensemble des 8 conteneurs (2 Brokers, 2 Crypto Nodes, 2 Nœuds Edge, Backend, Frontend) sur une **seule machine de développement** :

### Option A : Déploiement Standard
```bash
docker compose up -d --build
```

### Option B : Déploiement Local Rapide
```bash
docker compose -f docker-compose.local.yml up -d --build
```

L'ensemble des services sera immédiatement opérationnel sur les ports suivants :
- **Frontend Web :** `http://localhost:5173`
- **Backend API :** `http://localhost:8000`
- **Broker Zone 1 :** `localhost:1883`
- **Broker Zone 2 :** `localhost:1884`
- **Crypto Zone 1 :** `https://localhost:8443`
- **Crypto Zone 2 :** `https://localhost:8445`
- **Edge Node Zone 1 :** `ws://localhost:8444`
- **Edge Node Zone 2 :** `ws://localhost:8447`

---

## 🔍 Validation et Contrôle de Santé

### 1. Contrôle de Santé Global du Backend
Interrogez le endpoint `/health` du backend :
```bash
curl http://localhost:8000/health
```
Réponse attendue avec les deux zones connectées et opérationnelles :
```json
{
  "status": "ok",
  "timestamp": "2026-10-20T10:00:00.000000",
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

### 2. Surveillance de la Télémétrie MQTT Multi-Zone
Pour observer les messages de télémétrie émis par les nœuds Fog Edge de chaque zone :
```bash
# Pour Zone 1 :
docker exec -it pledge_broker_mqtt_zone_1 mosquitto_sub -t "telemetrie/#" -v

# Pour Zone 2 :
docker exec -it pledge_broker_mqtt_zone_2 mosquitto_sub -t "telemetrie/#" -v
```

### 3. Test de Mobilité Inter-Zone (Streamer Nomad)
1. Ouvrez `http://<IP_SERVEUR_WEB>:5173` dans le navigateur.
2. Créez un compte Streamer et démarrez la diffusion en **Zone 1**.
3. Observez l'allocation du nœud Edge Zone 1 (`ws://...:8444`).
4. Déclenchez le changement de zone vers **Zone 2** depuis l'interface ou simulez la migration :
   - Le backend sollicite le Crypto Node de la Zone 2 pour émettre la clé de délégation $DK_d$.
   - Le nœud Edge de la Zone 2 transcode et re-signe les blocs vidéo sans rupture de service pour les spectateurs.

---

## 🛑 Arrêt des Services

### Arrêt d'un déploiement distribué :
Sur chaque machine respective :
```bash
docker compose down
```

### Arrêt du déploiement global sur une machine :
```bash
docker compose down
# ou pour le mode local :
docker compose -f docker-compose.local.yml down
```
Ajoutez `-v` si vous souhaitez également réinitialiser les volumes de base de données et de stockage de blocs vidéo :
```bash
docker compose down -v
```
