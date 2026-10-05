# Guide de Lancement Local (Environnement de Développement avec Docker)

Ce guide explique comment lancer toute l'infrastructure de Pledge sur votre machine physique en utilisant **uniquement Docker**. Chaque brique (Broker, Crypto, Edge, Web) tournera dans son propre conteneur, simulant un réseau complet sur un seul PC.

> **⚠️ Attention aux conflits de Ports & IPs en Local :**
> 1. **Le Port 8443 :** Par défaut, le *Serveur Crypto* et le *Nœud Edge* écoutent tous les deux sur le port **8443**. Pour éviter le conflit sur la même machine, nous allons forcer le Nœud Edge à s'exposer sur le port **8444**.
> 2. **L'adresse IP `host.docker.internal` :** Sous Windows/Mac, pour qu'un conteneur Docker puisse communiquer avec un autre conteneur exposé sur votre PC (ex: Le backend qui parle au noeud Edge), il faut utiliser l'adresse spéciale `host.docker.internal` au lieu de `127.0.0.1` ou de l'IP LAN.

Vous devrez ouvrir **4 terminaux** et les laisser tourner en parallèle (ou utiliser l'option `-d`).

---

### Terminal 1 : Le Broker MQTT (Le chef d'orchestre réseau)
L'image officielle Mosquitto.

```bash
cd broker_et_client/broker_mqtt
docker compose up --build
```
*(Port exposé : 1883)*

---

### Terminal 2 : Le Serveur Crypto
Fournit les API cryptographiques `/api/v1/crypto/...`.

```bash
cd serveur_crypto
docker compose up --build
```
*(Port exposé : 8443)*

---

### Terminal 3 : Le Nœud Edge (Client MQTT + WSS)
Ici, nous devons dire au conteneur de pointer vers le broker local et de s'exposer sur 8444.
Ouvrez le fichier `broker_et_client/client_mqtt/docker-compose.yml` et modifiez temporairement ces valeurs :

```yaml
    ports:
      - "8444:8443"  # <-- Changement ici pour exposer sur 8444 sur l'hôte
    environment:
      - BROKER_IP=host.docker.internal  # <-- Changement ici
      - BROKER_PORT=1883
      - NODE_ID=Local_Edge_Node
      - ZONE_ID=Zone_1
      - PORT_WSS=8443
      - NODE_IP=host.docker.internal  # <-- Pour que le backend sache où le trouver
```
Puis lancez :
```bash
cd broker_et_client/client_mqtt
docker compose up --build
```

---

### Terminal 4 : Le Serveur Web (Backend + Frontend)
Ici aussi, on configure les variables d'environnement dans le `serveur_web/docker-compose.yml` :

```yaml
  backend:
    # ...
    environment:
      - BROKER_IP=host.docker.internal
      - BROKER_PORT=1883
      # Note : En dev HTTP simple via Uvicorn, utilisez http://
      - CRYPTO_URL_ZONE_1=http://host.docker.internal:8443
  frontend:
    # ...
    environment:
      - VITE_API_URL=http://localhost:8000
```
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
