# Guide de Lancement Local (Tout sur une Seule Machine) - Pledge

Ce guide détaille comment lancer **l'intégralité de la plateforme Pledge sur un seul ordinateur** en utilisant Docker. Chaque composant s'exécute dans son propre conteneur, simulant l'architecture distribuée complète sans dépendre d'adresses IP externes.

---

## 🎯 Architecture des Ports Locaux

Pour éviter les conflits d'écoute sur la même machine, chaque service possède son port dédié :

| Composant | Fichier Compose | Port Local | Description |
| :--- | :--- | :--- | :--- |
| **Broker MQTT** | `broker_et_client/broker_mqtt/docker-compose.local.yml` | `1883` | Relais télémétrie |
| **Serveur Crypto** | `serveur_crypto/docker-compose.local.yml` | `8443` | API HTTPS (SIS & Hess-IBS) |
| **Nœud Edge** | `broker_et_client/client_mqtt/docker-compose.local.yml` | `8444` | Stockage et streaming WS |
| **Backend API** | `serveur_web/docker-compose.local.yml` | `8000` | API FastAPI & Auth |
| **Frontend Web** | `serveur_web/docker-compose.local.yml` | `5173` | Interface utilisateur React |

> **Astuce `host.docker.internal` :** Tous les fichiers `docker-compose.local.yml` sont préconfigurés avec l'alias `host.docker.internal:host-gateway` pour fonctionner de manière transparente sous Windows, macOS et Linux.

---

## 🚀 Procédure de Lancement Pas-à-Pas

Ouvrez un terminal (PowerShell ou Bash) et exécutez les commandes suivantes dans l'ordre :

### 1. Démarrer le Broker MQTT
```bash
cd broker_et_client/broker_mqtt
docker compose -f docker-compose.local.yml up -d --build
cd ../..
```

### 2. Démarrer le Serveur Crypto
```bash
cd serveur_crypto
docker compose -f docker-compose.local.yml up -d --build
cd ..
```

### 3. Démarrer le Nœud Edge
```bash
cd broker_et_client/client_mqtt
docker compose -f docker-compose.local.yml up -d --build
cd ../..
```

### 4. Démarrer le Serveur Web (Backend + Frontend)
```bash
cd serveur_web
docker compose -f docker-compose.local.yml up -d --build
cd ..
```

---

## 🔍 Vérification des Services

Une fois les conteneurs lancés :

1. **Vérifier l'état de santé du Backend et des liaisons :**
   Ouvrez `http://localhost:8000/health` dans votre navigateur.
   Vous devez obtenir :
   ```json
   {
     "status": "ok",
     "database": "ok",
     "mqtt": "connected",
     "crypto_nodes": {"Zone_1": "ok"}
   }
   ```

2. **Accéder à l'application web :**
   Rendez-vous sur **http://localhost:5173**

---

## 🎥 Tester le Streaming en Direct

1. **Émetteur (Streamer) :**
   - Sur `http://localhost:5173`, cliquez sur **Créer un compte**.
   - Choisissez le rôle **Streamer** (ex: `alice@pledge.local`).
   - Cliquez sur **Démarrer la caméra** et autorisez l'accès à la webcam.
   - Le voyant passe à `En ligne` et le compteur de blocs commence à s'incrémenter.

2. **Visionneur (Viewer) :**
   - Ouvrez une fenêtre de **navigation privée** sur `http://localhost:5173`.
   - Créez un compte avec le rôle **Viewer** (ex: `bob@pledge.local`).
   - Dans le lecteur, le flux d'Alice apparaît automatiquement dans la liste des **Flux en direct**.
   - Cliquez sur le flux : la vidéo démarre instantanément en temps réel avec vérification d'intégrité SIS.

---

## 🛑 Arrêt des Services

Pour arrêter l'ensemble des conteneurs locaux :
```bash
cd serveur_web && docker compose -f docker-compose.local.yml down && cd ..
cd broker_et_client/client_mqtt && docker compose -f docker-compose.local.yml down && cd ../..
cd serveur_crypto && docker compose -f docker-compose.local.yml down && cd ..
cd broker_et_client/broker_mqtt && docker compose -f docker-compose.local.yml down && cd ../..
```
