# Nœud Fog Edge de Stockage & Streaming — `client_mqtt`

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.14-blue.svg)](https://www.python.org/)
[![Protocol](https://img.shields.io/badge/Streaming-WebSocket%20Direct-green.svg)](#-routage-des-flux-websocket)
[![Transcoding](https://img.shields.io/badge/Edge%20Worker-720p%20Transcoding-orange.svg)](#-transcodage-périphérique-et-délégation)
[![Sharding](https://img.shields.io/badge/Fog%20Sharding-Option%20B-success.svg)](#-support-du-sharding-multi-nœuds-option-b)

Le **Nœud Fog Edge** est le composant d'infrastructure périphérique exécuté sur les machines locales (PC de zone, Nvidia Jetson, serveur local). Il prend en charge le stockage décentralisé des flux vidéo, la distribution directe sans passer par le serveur central, le transcodage adaptatif 720p et la publication de télémétrie de santé via MQTT.

---

## 🏛️ Architecture & Fonctionnalités

```text
                                [Streamer]
                                    │
               WebSocket /upload/{id}│ (Bypass Serveur Web)
                                    ▼
       ┌─────────────────────────────────────────────────────────┐
       │                 NŒUD FOG EDGE (client_edge.py)          │
       │                                                         │
       │  1. Réception & Stockage :                              │
       │     - Écriture asynchrone des blocs : bloc_0.dat, ...   │
       │     - Sauvegarde permanente de l'en-tête : init_header  │
       │                                                         │
       │  2. Worker de Transcodage (edge_transcoder.py) :        │
       │     - Transcodage en arrière-plan vers 720p             │
       │     - Re-signature déléguée Hess-IBS (3 accouplements)  │
       │                                                         │
       │  3. Agent MQTT Intégré :                                │
       │     - Envoi télémétrie (CPU, RAM, Disque Go) / 5s       │
       │     - Surveillance TTL inactivité (60s)                 │
       │     - Purge sur commande (pledge/commands/purge_session)│
       └────────────────────────────┬────────────────────────────┘
                                    │
             WebSocket /download/{id} (Original ou 720p transcodé)
                                    ▼
                                [Viewer]
```

---

## ⚡ Caractéristiques Principales

### 1. Routage des Flux WebSocket Direct
Le serveur WebSocket asynchrone (`websockets` / `asyncio`) écoute sur le port `8444` (ou `8447` en multi-instance) et expose deux endpoints principaux :
- **`WS /upload/{client_id}?token={jwt}`** :
  - Authentification du diffuseur via token signé.
  - Réception de la clé de délégation $DK_d$ et de la clé publique de zone $PK_s$.
  - Enregistrement immédiat des blocs bruts sur disque dans `/donnees_isolees/{client_id}/`.
  - Transmission au worker de transcodage sans bloquer la boucle d'événements.
- **`WS /download/{client_id}?mode=live&quality=1080p&token={jwt}`** :
  - Diffusion en direct vers le spectateur.
  - Support de la sélection de qualité : `quality=original` (1080p natif) ou `quality=720p` (flux transcodé et re-signé).

### 2. Support du Sharding Multi-Nœuds (Option B)
Conformément aux sections 3.1 & 3.2 de Sellami et al. (*FGCS 2024*) :
- Lorsqu'une session est partitionnée sur $K$ nœuds Fog, chaque nœud ne reçoit qu'un sous-ensemble non-contigu des blocs (ex: Nœud A = blocs 0, 1, 3, 5... ; Nœud B = blocs 0, 2, 4, 6...).
- La boucle de lecture [`envoi_flux_video()`](client_edge.py) recherche dynamiquement le prochain bloc disponible $\ge compteur\_bloc$. Elle ne bloque jamais en attendant les blocs manquants attribués aux autres nœuds Fog.

### 3. Transcodage Périphérique et Délégation (Section 3.6.2)
Le module [`edge_transcoder.py`](edge_transcoder.py) exécute un worker dédié en arrière-plan :
- Génère les blocs allégés `bloc_{id}_720p.dat`.
- Calcule la nouvelle preuve matricielle SIS $v_{trans} = (A \cdot x_{trans}) \bmod q$.
- Appose la **signature déléguée** :
  $$\sigma = (w_1, w_2), \quad w_2 = DK_d + w_1 \cdot y$$
- Permet au spectateur d'auditer la conformité du transcodage avec l'identité d'origine du diffuseur.

---

## ⚙️ Configuration & Variables d'Environnement

Définies dans le fichier `.env` :

| Variable | Exemple | Description |
| :--- | :--- | :--- |
| `NODE_ID` | `Noeud_Edge_Zone_1` | Nom unique de ce nœud Edge dans la topologie |
| `ZONE_ID` | `Zone_1` | Zone d'appartenance (`Zone_1` ou `Zone_2`) |
| `NODE_HOST` | `192.168.1.50` | Adresse IP réelle joignable par les diffuseurs et spectateurs |
| `EDGE_WS_PORT` | `8444` | Port d'écoute WebSocket (`8444` pour Zone 1, `8447` pour Zone 2 colocalisée) |
| `EDGE_WS_SCHEME` | `ws` | Protocole (`ws` pour HTTP clair, `wss` si certificats TLS montés) |
| `BROKER_HOST` | `192.168.1.10` | Hôte du broker MQTT de zone |
| `BROKER_PORT` | `1883` | Port du broker MQTT de zone |
| `CRYPTO_URL` | `https://192.168.1.20:8443` | URL HTTPS du serveur cryptographique de la zone |
| `EDGE_DATA_DIR` | `/donnees_isolees` | Répertoire de stockage des blocs vidéo dans le conteneur |
| `TELEMETRY_INTERVAL_SEC` | `5` | Fréquence d'émission de la télémétrie en secondes |

---

## 🚀 Commandes de Démarrage

### Scénario 1 : Démarrage Local (Simulation Monomachine)
```bash
cd broker_et_client/client_mqtt

# Démarrer le conteneur préconfiguré pour la machine locale
docker compose -f docker-compose.local.yml up -d --build

# Visualiser les logs de streaming en direct
docker compose -f docker-compose.local.yml logs -f

# Arrêter le nœud
docker compose -f docker-compose.local.yml down
```

### Scénario 2 : Démarrage sur Machine Dédiée (ex: Nvidia Jetson ou second PC)
```bash
cd broker_et_client/client_mqtt

# Copier et éditer la configuration d'environnement
cp .env.example .env
nano .env  # Renseigner l'IP réelle de la machine dans NODE_HOST

# Démarrer le conteneur Edge
docker compose up -d --build
```

### Scénario 3 : Exécution Native Python (Développement sans Docker)
```bash
# Installer les dépendances
pip install -r requirements.txt

# Configurer les variables et lancer le script
NODE_ID=Local_Edge NODE_HOST=127.0.0.1 EDGE_WS_PORT=8444 python client_edge.py
```

---

## 🔍 Diagnostics & Validation

1. **Vérifier l'écoute du port WebSocket** :
   ```powershell
   Test-NetConnection -ComputerName localhost -Port 8444
   ```
2. **Surveiller la télémétrie émise par le nœud** :
   ```bash
   mosquitto_sub -h localhost -p 1883 -t "telemetrie/#" -v
   ```
   Vous devez observer un JSON toutes les 5 secondes :
   ```json
   {"node_id":"Noeud_Edge_Zone_1","zone_id":"Zone_1","ip_address":"192.168.1.50","port_wss":8444,"stockage_dispo_gb":85.4,"cpu_usage_pct":14.2}
   ```
3. **Tester la purge automatique** :
   Le nœud supprime automatiquement les flux inactifs depuis plus de 60 secondes pour préserver le stockage périphérique.

