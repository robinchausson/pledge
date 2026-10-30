# Sous-Système Périphérique Fog & Messagerie — `broker_et_client`

Ce répertoire regroupe les deux composants fondamentaux formant la **couche périphérique (Fog Edge Layer)** de Pledge pour une zone géographique donnée :
1. **`broker_mqtt/`** : Le bus de contrôle et de télémétrie local basé sur Eclipse Mosquitto.
2. **`client_mqtt/`** : Le nœud Fog Edge autonome assurant la distribution vidéo par WebSocket direct, le transcodage 720p à la volée et la re-signature déléguée.

---

## 🏛️ Rôle dans l'Architecture PLEDGE

Dans le modèle Fog Computing (Sellami et al., *FGCS 2024*), les nœuds Fog sont déployés au plus près des utilisateurs afin de décharger le cloud central. Ce répertoire matérialise cette séparation stricte entre **plan de contrôle** et **plan de données** :

```text
 ┌────────────────────────────────────────────────────────┐
 │            SERVEUR CENTRAL (FastAPI Backend)           │
 └───────────────────────────┬────────────────────────────┘
                             │
            PLAN DE CONTRÔLE │ (Souscription Télémétrie MQTT)
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │       1. BROKER MQTT LOCAL (broker_mqtt) :1883         │
 │   - Topic : telemetrie/{zone_id}/{node_id}             │
 │   - Télémétrie CPU, stockage disque disponible         │
 └───────────────────────────▲────────────────────────────┘
                             │ Publication télémétrie (toutes les 5s)
 ┌───────────────────────────┴────────────────────────────┐
 │         2. NŒUD FOG EDGE (client_mqtt) :8444           │
 │   - Serveur WebSocket direct (Upload & Download)       │
 │   - Stockage local des fragments /donnees_isolees      │
 │   - Transcodage local 720p (edge_transcoder.py)        │
 │   - Re-signature déléguée avec clé proxy (DK_d, PK_d)  │
 └───────────────────────────▲────────────────────────────┘
                             │
              PLAN DE DONNÉES│ (WebSockets Directs - Pas de transit central)
                             ▼
                [DIFFUSEURS & SPECTATEURS]
```

---

## 📂 Contenu du Répertoire

| Répertoire | Service Déployé | Technologies | Rôle Principal |
| :--- | :--- | :--- | :--- |
| **[`broker_mqtt/`](broker_mqtt/README.md)** | Courtier Mosquitto | Eclipse Mosquitto, TLS 1.3 | Acheminement de la télémétrie vers le backend central et distribution des ordres de purge. |
| **[`client_mqtt/`](client_mqtt/README.md)** | Nœud Fog Edge | Python 3.11/3.14, Asyncio, WebSockets, Paho-MQTT, FFmpeg | Hébergement et streaming direct des blocs WebM, partitionnement Fog Sharding, transcodage 720p. |

---

## ⚙️ Caractéristiques Techniques

1. **Séparation Stricte Télémétrie / Médias** :
   - Le flux vidéo (de 1 à plusieurs Mo/s) ne traverse **jamais** le broker MQTT.
   - Le broker MQTT n'achemine que des paquets légers JSON de métriques (CPU %, Go disponibles, adresses IP/ports) toutes les 5 secondes.
2. **Support Multi-Zone Réparti** :
   - Chaque zone possède son propre broker et ses propres nœuds Fog.
   - Les nœuds Fog se déclarent automatiquement auprès de leur broker de zone dès le démarrage.
3. **Partitionnement / Sharding de Blocs Vidéo (Option B)** :
   - Lorsqu'une zone compte plusieurs nœuds Fog actifs, le diffuseur envoie le bloc 0 (en-tête d'initialisation WebM) à tous les nœuds, puis alterne les blocs $D_1, D_2, D_3\dots$ entre les nœuds.
   - Le nœud Fog sert ses blocs sans bloquer lorsqu'une séquence non contiguë lui est assignée.

---

## 🚀 Commandes de Démarrage Rapide

### Démarrer la pile complète du Fog (Broker + Nœud Edge) en local

```bash
# 1. Démarrer le broker MQTT local
cd broker_mqtt
docker compose -f docker-compose.local.yml up -d --build
cd ..

# 2. Démarrer le nœud Fog Edge local
cd client_mqtt
docker compose -f docker-compose.local.yml up -d --build
cd ..
```

### Vérifier le bon fonctionnement

```bash
# Vérifier la présence du broker sur le port 1883
netstat -an | grep 1883   # Linux / macOS
Test-NetConnection -Port 1883 -ComputerName 127.0.0.1  # Windows PowerShell

# Vérifier la présence du serveur de streaming Edge sur le port 8444
Test-NetConnection -Port 8444 -ComputerName 127.0.0.1
```

Pour les configurations détaillées, consultez les documentations individuelles :
- 👉 [Documentation détaillée du Broker Mosquitto](broker_mqtt/README.md)
- 👉 [Documentation détaillée du Nœud Fog Edge](client_mqtt/README.md)
