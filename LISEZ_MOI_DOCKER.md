# Guide de Déploiement Docker - Pledge

Ce document référence l'ensemble des commandes Docker pour lancer l'architecture complète du projet Pledge. Chaque composant est conteneurisé.

L'architecture est composée de 4 blocs principaux, qu'il faut lancer (idéalement) dans cet ordre :

---

### 1. Le Broker MQTT (Le chef d'orchestre réseau)
Il gère la télémétrie entre les nœuds Edge et le Serveur Web. Un seul broker par zone (ou un broker centralisé selon votre réseau).

* **Dossier :** `broker_et_client/broker_mqtt`
* **Commande pour le lancer :**
  ```bash
  cd broker_et_client/broker_mqtt
  docker compose up -d --build
  ```
* **Ports exposés :** `1883`

---

### 2. Le Serveur Crypto (La sécurité SIS & Hess-IBS)
Le nœud cryptographique autonome qui génère les preuves d'intégrité et les signatures. Un serveur par Zone Edge.

* **Dossier :** `serveur_crypto`
* **Prérequis :** Définir un scalaire secret (optionnel en dev, recommandé en prod).
* **Commande pour le lancer :**
  ```bash
  cd serveur_crypto
  docker compose up -d --build
  ```
* **Ports exposés :** `8443` (HTTPS)

---

### 3. Les Nœuds Edge (Le Stockage)
Les machines qui stockent physiquement les fragments vidéo et s'annoncent via MQTT. À lancer sur chaque machine "nœud" de vos zones.

* **Dossier :** `broker_et_client/client_mqtt`
* **Prérequis :** Éditer le fichier `docker-compose.yml` pour configurer `NODE_ID`, `ZONE_ID`, `BROKER_IP` et `NODE_IP` avec les vraies valeurs de la machine.
* **Commande pour le lancer :**
  ```bash
  cd broker_et_client/client_mqtt
  docker compose up -d --build
  ```
* **Ports exposés :** `8443` (WSS pour le téléchargement/envoi de fragments). *Note: Si un serveur crypto tourne sur la même machine, changez le port du nœud Edge dans son docker-compose (ex: 8444).*

---

### 4. Le Serveur Web (Backend FastAPI & Frontend React)
Le cerveau central qui gère l'authentification, le routage des flux WSS, l'interrogation de la crypto et l'interface utilisateur.

* **Dossier :** `serveur_web`
* **Prérequis :** Vous pouvez ajuster les adresses du Broker et des Serveurs Crypto de zones dans le `docker-compose.yml`.
* **Commande pour le lancer :**
  ```bash
  cd serveur_web
  docker compose up -d --build
  ```
* **Ports exposés :**
  - Backend API : `8000`
  - Frontend React : `5173` (Accessible via `http://localhost:5173`)

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
