# Guide de Lancement Local (Environnement de Développement avec Docker)

Ce guide explique comment lancer toute l'infrastructure de Pledge sur votre machine physique en utilisant **uniquement Docker**. Chaque brique (Broker, Crypto, Edge, Web) tournera dans son propre conteneur, simulant un réseau complet sur un seul PC.

> **⚠️ Attention aux conflits de Ports & IPs en Local :**
> 1. **Le Port 8443 :** Par défaut, le *Serveur Crypto* et le *Nœud Edge* écoutent tous les deux sur le port **8443**. Pour éviter le conflit sur la même machine, nous allons forcer le Nœud Edge à s'exposer sur le port **8444**.
> 2. **L'adresse `host.docker.internal` :** elle permet aux conteneurs locaux de joindre les services exposés par la machine hôte. Les fichiers `docker-compose.local.yml` sont déjà configurés ainsi, y compris sous Linux grâce à `host-gateway`.

Vous devrez ouvrir **4 terminaux** et les laisser tourner en parallèle (ou utiliser l'option `-d`).

---

### Terminal 1 : Le Broker MQTT (Le chef d'orchestre réseau)
L'image officielle Mosquitto.

```bash
cd broker_et_client/broker_mqtt
docker compose -f docker-compose.local.yml up -d --build
```
*(Port exposé : 1883)*

---

### Terminal 2 : Le Serveur Crypto
Fournit les API cryptographiques `/api/v1/crypto/...`.

```bash
cd serveur_crypto
docker compose -f docker-compose.local.yml up -d --build
```
*(Port exposé : 8443)*

---

### Terminal 3 : Le Nœud Edge (Client MQTT + WebSocket)
Ici, nous devons dire au conteneur de pointer vers le broker local et de s'exposer sur 8444.
Utilisez directement `broker_et_client/client_mqtt/docker-compose.local.yml` :

Puis lancez :
```bash
cd broker_et_client/client_mqtt
docker compose -f docker-compose.local.yml up -d --build
```

---

### Terminal 4 : Le Serveur Web (Backend + Frontend)
Utilisez directement `serveur_web/docker-compose.local.yml` :

Puis lancez :
```bash
cd serveur_web
docker compose up --build
```
*(Ports exposés : 8000 pour l'API, 5173 pour le React)*

---

### 🌐 Accéder à l'application
Une fois que les 4 terminaux tournent sans erreur :
1. Ouvrez votre navigateur et allez sur **http://localhost:5173**
2. Créez un compte "Streamer" pour envoyer un flux.
3. Ouvrez une navigation privée, créez un compte "Viewer" et entrez l'adresse email du streamer pour lire le flux.
