# Guide de Déploiement Docker - Pledge

Ce document référence l'ensemble des commandes Docker pour lancer l'architecture complète du projet Pledge.
Le déploiement peut se faire de deux manières :
1. **Local** (pour le développement, tout sur la même machine).
2. **Distribué / Réseau Tailscale** (pour simuler une véritable architecture Edge sur plusieurs ordinateurs).

---

## 🌐 Mode Distribué (Réseau Tailscale ou LAN)
Dans ce mode, vous déployez chaque composant sur des machines différentes. Le routage réseau se fait via des adresses IP (idéalement via un VPN comme Tailscale `100.x.x.x`). 
Les fichiers `docker-compose.yml` de base sont configurés pour ce mode.

### 1. Le Serveur Web (Backend FastAPI & Frontend React)
La machine centrale gérant l'API, la base de données et l'interface utilisateur.
* **Dossier :** `serveur_web`
* **Configuration :** Copiez le fichier `.env.example` en `.env` et renseignez les IP Tailscale des autres serveurs (Broker, Serveurs Crypto, et l'IP de cette propre machine).
* **Lancement :**
  ```bash
  cd serveur_web
  docker compose up -d --build
  ```
* **Accès :** Frontend sur le port `5173`, Backend API sur `8000`.

### 2. Le Broker MQTT
Le chef d'orchestre réseau qui transmet la télémétrie des Nœuds Edge au Serveur Web.
* **Dossier :** `broker_et_client/broker_mqtt`
* **Configuration :** Aucune (il ne fait qu'écouter).
* **Lancement :**
  ```bash
  cd broker_et_client/broker_mqtt
  docker compose up -d
  ```

### 3. Les Nœuds Edge (Stockage)
Les machines qui reçoivent, stockent et diffusent les flux vidéo. À lancer sur toutes vos machines de stockage.
* **Dossier :** `broker_et_client/client_mqtt`
* **Configuration :** Copiez `.env.example` en `.env` et définissez l'IP Tailscale du Broker, ainsi que l'IP Tailscale de CETTE machine Edge (pour que le Serveur Web puisse la trouver).
* **Lancement :**
  ```bash
  cd broker_et_client/client_mqtt
  docker compose up -d --build
  ```
* **Ports exposés :** `8444` par défaut (WSS).

### 4. Le Serveur Crypto
Génère et vérifie les signatures cryptographiques.
* **Dossier :** `serveur_crypto`
* **Configuration :** Optionnelle. (L'entrypoint générera automatiquement un certificat TLS de test).
* **Lancement :**
  ```bash
  cd serveur_crypto
  docker compose up -d --build
  ```
* **Ports exposés :** `8443` (HTTPS).

---

## 💻 Mode Local (Développement sur une seule machine)
Si vous souhaitez tout faire tourner sur votre PC sans vous soucier des adresses IP :

Utilisez systématiquement le fichier **`docker-compose.local.yml`** au lieu du classique `docker-compose.yml`. Il est déjà préconfiguré avec `host.docker.internal` et `localhost` !

**Pour chaque dossier (`serveur_web`, `broker_mqtt`, `client_mqtt`, `serveur_crypto`), lancez :**
```bash
docker compose -f docker-compose.local.yml up -d --build
```

---

## 🛠️ Commandes utiles

**Voir les logs d'un composant en direct :**
```bash
docker compose logs -f
```

**Arrêter un composant (sans supprimer ses données) :**
```bash
docker compose stop
```

**Arrêter et détruire les conteneurs d'un composant :**
```bash
docker compose down
```
