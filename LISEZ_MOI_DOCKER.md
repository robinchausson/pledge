# Guide de Déploiement Distribué (LAN Réseau Physique) - Pledge

Ce document référence l'ensemble des procédures et commandes pour déployer l'architecture décentralisée **Pledge** sur des machines physiques indépendantes au sein d'un réseau local (LAN).

---

## 🏗️ Architecture des Composants

L'infrastructure repose sur 4 briques autonomes pouvant être exécutées sur 4 ordinateurs distincts :

1. **Le Broker MQTT (Mosquitto)** : Plan de contrôle réseau, relaie la télémétrie des nœuds Edge vers le Backend.
2. **Le Serveur Crypto (FastAPI / BN128 / SIS)** : Génère et vérifie l'intégrité matricielle (SIS) et les signatures (Hess-IBS) de chaque zone.
3. **Le ou les Nœuds Edge (Storage & Streaming)** : Reçoivent, isolent et diffusent les flux vidéo sur le plan de données.
4. **Le Serveur Web (Backend FastAPI & Frontend React)** : Orchestre les sessions, sélectionne le nœud Edge adéquat et sert l'interface utilisateur.

---

## 🛡️ Matrice des Ports Réseau (Pare-feu LAN)

Assurez-vous que les ports suivants sont autorisés sur le pare-feu de chaque machine hôte respective :

| Machine / Rôle | Port Entrant | Protocole | Description |
| :--- | :--- | :--- | :--- |
| **Machine Broker** | `1883` | TCP | Écoute MQTT |
| **Machine Crypto** | `8443` | TCP / HTTPS | API d'intégrité et de signature |
| **Machine Edge** | `8444` | TCP / WS | Upload et Download des blocs vidéo |
| **Machine Web (API)** | `8000` | TCP / HTTP & WS | API FastAPI, Auth, WebSocket client |
| **Machine Web (UI)** | `5173` | TCP / HTTP | Interface web Vite / React |

---

## 📋 Ordre de Démarrage Impératif

Pour garantir l'établissement des connexions réseau sans délai d'attente, démarrez les services dans cet ordre :

```
Étape 1 : Broker MQTT ──► Étape 2 : Serveur Crypto ──► Étape 3 : Nœud(s) Edge ──► Étape 4 : Serveur Web
```

---

### Étape 1 : Le Broker MQTT

* **Machine :** Machine dédiée ou serveur de réseau local.
* **Dossier :** `broker_et_client/broker_mqtt`
* **Configuration :** Copiez `.env.example` en `.env` (si vous souhaitez changer le port `MQTT_PORT`).
* **Lancement :**
  ```bash
  cd broker_et_client/broker_mqtt
  docker compose up -d
  ```
* **Vérification :**
  ```bash
  docker compose logs -f broker_mqtt
  ```

---

### Étape 2 : Le Serveur Crypto

* **Machine :** Machine dédiée à la cryptographie pour la Zone 1.
* **Dossier :** `serveur_crypto`
* **Configuration :** Copiez `.env.example` en `.env`. Renseignez `CRYPTO_SECRET_SCALAR` si vous souhaitez une clé fixe.
* **Lancement :**
  ```bash
  cd serveur_crypto
  docker compose up -d --build
  ```
* **Vérification :**
  ```bash
  curl -k https://localhost:8443/health
  # Réponse attendue : {"status":"ok","initialized":true,"curve":"bn128","version":"1.0"}
  ```

---

### Étape 3 : Le ou les Nœuds Edge

* **Machine :** À exécuter sur chaque machine de stockage / Edge.
* **Dossier :** `broker_et_client/client_mqtt`
* **Configuration :** Copiez `.env.example` en `.env` et adaptez les variables :
  - `NODE_ID` : Nom unique de ce nœud (ex: `Noeud_Julian`).
  - `NODE_HOST` : Adresse IP LAN de CETTE machine Edge (ex: `192.168.1.66`).
  - `BROKER_HOST` : Adresse IP LAN de la Machine Broker (Étape 1).
  - `EDGE_WS_PORT` : Port d'écoute (par défaut `8444`).
* **Lancement :**
  ```bash
  cd broker_et_client/client_mqtt
  docker compose up -d --build
  ```
* **Vérification :**
  ```bash
  docker compose logs -f client_telemetrie
  # Vous devez voir : Connecté au Broker MQTT ...
  ```

---

### Étape 4 : Le Serveur Web (Backend + Frontend)

* **Machine :** Machine hébergeant l'application web centrale.
* **Dossier :** `serveur_web`
* **Configuration :** Copiez `.env.example` en `.env` et renseignez :
  - `LAN_URL_BACKEND` : URL d'accès de cette machine avec le port 8000 (ex: `http://192.168.1.50:8000`).
  - `BROKER_HOST` : Adresse IP de la Machine Broker (Étape 1).
  - `CRYPTO_URL_ZONE_1` : URL HTTPS du Serveur Crypto (ex: `https://192.168.1.138:8443`).
* **Lancement :**
  ```bash
  cd serveur_web
  docker compose up -d --build
  ```
* **Vérification :**
  Accédez depuis un navigateur à :
  - **Interface utilisateur :** `http://<IP_SERVEUR_WEB>:5173`
  - **Santé de l'API :** `http://<IP_SERVEUR_WEB>:8000/health`

---

## 🔒 Sécurité et Bonnes Pratiques

1. **Secrets et Fichiers `.env` :** Ne committez jamais les fichiers `.env` ou les clés privées du dossier `certs/`.
2. **Mode WS vs WSS :** En développement local ou LAN interne, le protocole WebSocket fonctionne par défaut en `ws://` (HTTP). En production, définissez `EDGE_WS_SCHEME=wss` et placez vos certificats TLS dans le volume du conteneur.
3. **Persistance :** Les blocs vidéo sont conservés dans le volume Docker nommé `edge_storage_data` sur chaque nœud Edge.
