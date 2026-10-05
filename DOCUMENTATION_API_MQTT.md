# Documentation de l'Architecture : API et MQTT (Pledge)

Ce document centralise l'ensemble des flux de communication de l'application Pledge, divisés entre le **Plan de Contrôle** (MQTT, API Backend, Crypto) et le **Plan de Données** (WebSockets vidéo).

---

## 📡 1. Télémétrie MQTT (Control Plane)

Les nœuds Edge (Edge Nodes) signalent leur état et leur disponibilité en temps réel au Backend via le broker MQTT central.

- **Broker MQTT** : `broker_zone` (Exposé sur le port `1883`)
- **Émetteur (Publisher)** : Le nœud Edge (`client_edge.py`)
- **Abonné (Subscriber)** : Le Backend (`mqtt_client.py`)
- **Fréquence d'envoi** : Toutes les 5 secondes.

### Format du Topic
Le topic est structuré géographiquement/logiquement :
`telemetrie/{ZONE_ID}/{NODE_ID}`
> Exemple : `telemetrie/Zone_1/Local_Edge_Node`

### Payload JSON (Exemple)
```json
{
  "timestamp": "2026-10-05T15:20:00.000Z",
  "ip_address": "host.docker.internal",
  "wss_port": 8444,
  "storage_available_gb": 42.5,
  "cpu_usage_pct": 12.4
}
```
*Ces données permettent au Backend d'alimenter sa base de données (table `edge_nodes`) et de choisir le meilleur nœud (avec le plus de stockage dispo) lorsqu'un Streamer commence à diffuser.*

---

## 🌐 2. Endpoints du Serveur Web (Backend)
**URL de base :** `http://localhost:8000`

### 🔐 Authentification (Session persistante via Cookies)
- **`POST /auth/register`** : Création d'un compte (viewer ou streamer). Renvoie les infos utilisateur.
- **`POST /auth/login`** : Connexion. Pose automatiquement des cookies HTTP-Only (`access_token` et `refresh_token`).
- **`POST /auth/logout`** : Déconnexion. Détruit les cookies.
- **`GET /auth/me`** : Utilisé par React à chaque actualisation (F5) pour vérifier le cookie et maintenir la session sans coupure.

### 🎥 Routage Vidéo (WebSockets)
Ces routes sont le point d'entrée pour le Frontend. Le Backend se charge ensuite de faire le pont vers l'Edge Node et le Crypto Node.
- **`WS /api/stream/publish/{client_id}`** : 
  - Utilisé par l'interface **Streamer**.
  - Reçoit les blocs vidéo `.webm`, demande la signature au Crypto Node, puis achemine le tout vers le meilleur nœud Edge.
- **`WS /api/stream/watch/{client_id}`** :
  - Utilisé par l'interface **Viewer**.
  - Lit les blocs vidéo depuis le nœud Edge où le flux est stocké, fait vérifier l'intégrité via le Crypto Node, puis l'envoie au lecteur vidéo du navigateur.

---

## 🛡️ 3. Endpoints du Serveur Crypto
**URL de base :** `https://host.docker.internal:8443`
*(Serveur obligatoire en HTTPS avec certificat TLS auto-signé)*

Ce serveur s'occupe des calculs cryptographiques lourds (Pairing sur courbe elliptique bn128, Hachage homomorphe).

- **`POST /api/v1/crypto/signature`**
  - **Payload** : `{"fragment_data": "base64_string", "paramA": 42}`
  - **Description** : Appelé par le Backend lorsqu'un Streamer envoie un fragment vidéo. Calcule une matrice V_matrix et une signature sur courbe elliptique.
  - **Réponse** : `{ "V_matrix": [...], "signature_point": [...] }`

- **`POST /api/v1/crypto/integrity`**
  - **Payload** : `{"fragment_data": "base64_string", "paramA": 42}`
  - **Description** : Appelé par le Backend lorsqu'un Viewer lit un flux depuis un nœud Edge. Permet de recalculer la matrice et de vérifier mathématiquement qu'aucune altération n'a eu lieu sur l'Edge.

---

## ⚡ 4. Endpoints du Nœud Edge (Data Plane)
**URL de base :** `ws://host.docker.internal:8444`

Bien qu'exposé localement, **seul le Backend est censé y accéder**. L'Edge ne communique jamais en direct avec le Frontend Web (Navigateur), pour des raisons de sécurité.

- **`WS /publish/{client_id}`** : Reçoit les fragments signés depuis le Backend et les stocke (pour l'instant en mémoire RAM).
- **`WS /watch/{client_id}`** : Permet au Backend de récupérer en flux continu les fragments stockés pour un certain diffuseur.
