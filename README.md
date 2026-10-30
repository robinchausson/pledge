# PLEDGE — Plateforme de Streaming Fog Décentralisée avec Intégrité Vérifiable

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)
[![MQTT](https://img.shields.io/badge/MQTT-Mosquitto-660066.svg)](https://mosquitto.org/)
[![Curve](https://img.shields.io/badge/Curves-BN254%20%2F%20Alt--bn128-orange.svg)](https://github.com/ethereum/py_ecc)
[![Docker](https://img.shields.io/badge/Docker-Compose%20v2-2496ED.svg)](https://www.docker.com/)
[![Paper](https://img.shields.io/badge/Paper-FGCS%202024-success.svg)](https://doi.org/10.1016/j.future.2023.08.016)

> Implémentation de référence du modèle d'intégrité de données vérifiable en architecture Fog Computing d'après les travaux de recherche :  
> **"A verifiable data integrity scheme for distributed data sharing in fog computing architecture"**  
> *Youssef Sellami, Youcef Imine, Antoine Gallais — Future Generation Computer Systems 150 (2024), pp. 64–77.*

---

## 📖 Sommaire

1. [Vue d'Ensemble du Projet](#-vue-densemble-du-projet)
2. [Piliers Cryptographiques & Innovations](#-piliers-cryptographiques--innovations)
3. [Architecture Système Globale](#-architecture-système-globale)
4. [Arborescence du Dépôt](#-arborescence-du-dépôt)
5. [Matrice des Ports Réseau](#-matrice-des-ports-réseau)
6. [Démarrage Rapide](#-démarrage-rapide)
   - [Scénario A : Déploiement Local Monomachine (Simulation complète)](#scénario-a--déploiement-local-monomachine-simulation-complète)
   - [Scénario B : Déploiement Distribué Multi-Machine (LAN Réel)](#scénario-b--déploiement-distribué-multi-machine-lan-réel)
7. [Validation & Banc de Tests](#-validation--banc-de-tests)
8. [Documentation des Sous-Systèmes](#-documentation-des-sous-systèmes)

---

## 🌟 Vue d'Ensemble du Projet

Dans les architectures de streaming vidéo en périphérie de réseau (*Fog Computing*), les nœuds Fog situés à proximité des utilisateurs finaux réduisent la latence et économisent la bande passante de transit. Cependant, ces nœuds intermédiaires ne sont **pas de confiance absolue** : ils peuvent corrompre les flux, subir des pannes ou injecter des trames frauduleuses.

**PLEDGE** résout ce défi en combinant :
- **Un plan de données décentralisé direct en WebSocket (WS/WSS)** : Le flux vidéo ne transite jamais par le serveur web central, éliminant tout goulot d'étranglement centralisé.
- **Un partitionnement de blocs Fog (Block Sharding / Striping)** : Les blocs vidéo d'une session sont distribués en round-robin sur plusieurs nœuds Fog indépendants, tandis que le spectateur les agrège et les réordonne en temps réel dans un buffer MSE.
- **Un audit public sans tiers de confiance (Zero-TPA Client Verification)** : Chaque spectateur vérifie mathématiquement dans son navigateur l'authenticité et l'intégrité de chaque bloc vidéo en moins de 25 ms grâce à des accouplements de Weil/Tate sur courbe BN254.
- **Une mobilité multi-zone transparente** : Un diffuseur traversant plusieurs zones accumule homomorphiquement ses clés de signature sans interrompre sa diffusion.
- **Un transcodage périphérique délégué** : Les nœuds Fog peuvent transcoder le flux original (ex: 1080p -> 720p) et re-signer les trames au moyen d'un jeton de délégation vérifiable à 3 accouplements bilinéaires.

---

## 🔐 Piliers Cryptographiques & Innovations

| Concept Mathématique | Implémentation PLEDGE | Rôle dans l'Architecture |
| :--- | :--- | :--- |
| **Générateur Matriciel Pseudo-Aléatoire** | L'Ecuyer (1994) : $I_{n+1} = (a I_n + C_n) \bmod q$ | Reconstruction $O(1)$ de la matrice publique $A \in \mathbb{Z}_q^{l \times m}$ ($l=64, m=1536, q=4099$). |
| **Preuve d'Intégrité SIS** | Short Integer Solution : $v_i = (A \cdot x_i) \bmod q$ | Compression matricielle d'un bloc vidéo en vecteur 64 dimensions résistant aux collisions. |
| **Signature Basée sur l'Identité (IBS)** | Schéma de Hess sur courbe elliptique BN254 (Alt-bn128) | Signature déterministe $S_w = t_s \cdot H_1(ID_w) \in G_1$ et challenge $w_1 = H_2(V, paramA, r)$. |
| **Mobilité Homomorphe** | Addition d'éléments de groupe : $S_w \leftarrow \sum S_w^k$, $PK \leftarrow \sum PK_k$ | Continuité de signature lors du changement de zone sans réinitialisation de session. |
| **Délégation de Transcodage** | Clé de proxy $DK_d = S_w + y \cdot H_1(ID_d)$ | Autorise un nœud Fog à modifier le flux (720p) tout en prouvant la délégation avec 3 pairings. |
| **Audit Public Client** | Pairing $e: G_1 \times G_2 \to G_T$ dans le navigateur (@noble/curves) | Validation locale de chaque paquet vidéo en ~24 ms, détection et rejet immédiat de tout bit altéré. |

---

## 🏗️ Architecture Système Globale

```
                          ┌────────────────────────────────────────────────────────┐
                          │                SERVEUR WEB CENTRAL (LAN)               │
                          │   Backend FastAPI (:8000)   │   Frontend React (:5173) │
                          │   - Auth JWT                │   - Tableau de bord live │
                          │   - Routage & Sharding      │   - Moteur SIS/Hess-IBS  │
                          │   - Souscription MQTT multi │   - Décodeur MSE player  │
                          └───────────┬────────────────────────────────┬───────────┘
                                      │ Télémétrie MQTT                │ Signalisation & Découverte
                                      ▼                                ▼
         ╔═════════════════════════════════════════╗       ╔═════════════════════════════════════════╗
         ║         ZONE 1 (ex: PC Fixe)            ║       ║         ZONE 2 (ex: Jetson / PC 2)      ║
         ╠═════════════════════════════════════════╣       ╠═════════════════════════════════════════╣
         ║ • Broker MQTT Mosquitto (:1883 / :8883) ║       ║ • Broker MQTT Mosquitto (:1883 / :8883) ║
         ║ • Nœud Crypto Zone 1 (:8443)           ║       ║ • Nœud Crypto Zone 2 (:8443)           ║
         ║ • Nœud Fog Edge Zone 1 (:8444)          ║       ║ • Nœud Fog Edge Zone 2 (:8444 / :8447)  ║
         ╚═════════════════════════════════════════╝       ╚═════════════════════════════════════════╝
                                      ▲                                ▲
                       Upload direct  │ WebSocket                      │ WebSocket
                       Blocs impairs  │                                │ Blocs pairs
                                      │                                │
                       ┌──────────────┴────────────────────────────────┴─────────────┐
                       │                   DIFFUSEUR (STREAMER)                      │
                       │   - Capture Webcam / MediaRecorder                          │
                       │   - Calcul SIS local vi & Signature Hess-IBS Sw             │
                       │   - Partitionnement Sharding : Bloc 0 diffusé à tous,       │
                       │     puis Round-Robin D_i -> FN_{(i-1) mod K}                │
                       └─────────────────────────────────────────────────────────────┘
                                      │                                │
                     Download direct  │ WebSocket                      │ WebSocket
                     Fragments flux   │                                │ Fragments flux
                                      ▼                                ▼
                       ┌─────────────────────────────────────────────────────────────┐
                       │                  SPECTATEUR (VIEWER)                        │
                       │   - Connexions WebSocket multi-nœuds concurrentes           │
                       │   - Resequencing Buffer (réordonnancement strict 0, 1, 2...)│
                       │   - Audit mathématique BN254 sans TPA (SIS + Hess-IBS)      │
                       │   - Injection continue dans le SourceBuffer MSE             │
                       └─────────────────────────────────────────────────────────────┘
```

---

## 📁 Arborescence du Dépôt

```text
pledge/
├── docker-compose.yml              # Orchestration multi-services conteneurisée
├── docker-compose.local.yml        # Orchestration locale tout-en-un (ports mappés)
├── run_all_tests.py                # Lanceur global de l'ensemble des suites de tests
│
├── broker_et_client/               # Plan de messagerie MQTT et nœuds de périphérie Fog
│   ├── broker_mqtt/                # Courtier Mosquitto (bus MQTT, certificats TLS)
│   └── client_mqtt/                # Nœud Fog Edge (télémétrie MQTT, streaming WS, transcodage)
│
├── serveur_crypto/                 # Nœud cryptographique de zone (SIS, Hess-IBS, BN128, KGC)
│
├── serveur_web/                    # Plateforme web centrale
│   ├── backend/                    # API REST FastAPI, modèles SQLAlchemy, plan de signalisation
│   └── frontend/                   # Client React 19, player MSE, moteur crypto client (@noble)
│
└── tests/                          # 59+ tests automatisés (Unitaires, E2E, Benchmarks, Interop)
```

---

## 🔌 Matrice des Ports Réseau

| Service | Port Interne | Port Hôte (Local) | Protocole | Rôle & Usage |
| :--- | :---: | :---: | :---: | :--- |
| **Backend API** | `8000` | `8000` | TCP / HTTP & WS | Contrôleur central, authentification JWT, annuaire de signalisation |
| **Frontend Web** | `5173` | `5173` | TCP / HTTP | Interface utilisateur React / Vite (studio & lecteur) |
| **Broker Mosquitto Z1** | `1883` / `8883` | `1883` / `8883` | TCP / MQTT(S) | Télémétrie et bus d'événements Zone 1 |
| **Broker Mosquitto Z2** | `1883` / `8883` | `1884` / `8884` | TCP / MQTT(S) | Télémétrie et bus d'événements Zone 2 |
| **Nœud Crypto Z1** | `8443` | `8443` | TCP / HTTPS | Preuves SIS et paramètres publics de la Zone 1 |
| **Nœud Crypto Z2** | `8443` | `8445` | TCP / HTTPS | Preuves SIS et paramètres publics de la Zone 2 |
| **Nœud Fog Edge Z1** | `8444` | `8444` | TCP / WS(S) | Upload / Download streaming direct et stockage Zone 1 |
| **Nœud Fog Edge Z2** | `8447` | `8447` | TCP / WS(S) | Upload / Download streaming direct et stockage Zone 2 |

---

## 🚀 Démarrage Rapide

### Scénario A : Déploiement Local Monomachine (Simulation complète)

Pour lancer **l'intégralité de la plateforme sur une seule machine** (PC de développement) sans aucun conflit de port :

```bash
# 1. Cloner le projet
git clone https://github.com/robinchausson/pledge.git
cd pledge

# 2. Démarrer tous les conteneurs avec la configuration locale
docker compose -f docker-compose.local.yml up -d --build

# 3. Vérifier le statut de l'ensemble des conteneurs
docker compose -f docker-compose.local.yml ps
```

Une fois démarré :
- 🌐 **Interface Web :** Ouvrir [http://localhost:5173](http://localhost:5173)
- 🔍 **Contrôle de Santé :** Ouvrir [http://localhost:8000/health](http://localhost:8000/health)
- 🛑 **Arrêt complet :** `docker compose -f docker-compose.local.yml down`

---

### Scénario B : Déploiement Distribué Multi-Machine (LAN Réel)

Pour exécuter chaque zone sur une machine physique distincte (ex: PC Principal pour Zone 1, Nvidia Jetson pour Zone 2, Ordinateur Portable pour le Web) :

#### 1. Sur la machine Zone 1 (ex: `192.168.1.10`)
```bash
# Broker MQTT Zone 1
cd broker_et_client/broker_mqtt && docker compose up -d

# Serveur Crypto Zone 1
cd ../../serveur_crypto && docker compose up -d

# Nœud Fog Edge Zone 1
cd ../broker_et_client/client_mqtt && docker compose up -d
```

#### 2. Sur la machine Zone 2 (ex: `192.168.1.20`)
```bash
# Adapter les fichiers .env avec ZONE_ID=Zone_2 et le scalaire secret correspondant
cd broker_et_client/broker_mqtt && docker compose up -d
cd ../../serveur_crypto && docker compose up -d
cd ../broker_et_client/client_mqtt && docker compose up -d
```

#### 3. Sur la machine Web Centrale (ex: `192.168.1.100`)
```bash
cd serveur_web
# Renseigner dans serveur_web/.env les adresses IP réelles de la Zone 1 et Zone 2
docker compose up -d --build
```

---

## 🧪 Validation & Banc de Tests

Le projet intègre une suite de tests automatisés exhaustive couvrant la chaîne cryptographique, les liaisons MQTT, le transcodage périphérique, les attaques en transit et les benchmarks de performance.

```bash
# Exécution de la batterie de tests complète (Python + JavaScript)
python run_all_tests.py

# Exécution ciblée de la suite Pytest (59 tests)
pytest -v

# Exécution ciblée des tests cryptographiques frontend (8 tests)
cd serveur_web/frontend && npm test
```

Résultats clés mesurés :
- **Génération matricielle L'Ecuyer ($A$) :** $< 1.5$ ms (mise en cache en mémoire vive).
- **Calcul d'intégrité SIS ($v_i$) :** **0.17 ms** par fragment.
- **Vérification Hess-IBS dans le navigateur :** **~24 ms** (compatible flux direct avec blocs de 600–1000 ms).
- **Résistance aux altérations :** Rejet à 100% de toute trame corrompue (1 bit modifié = invalidation immédiate).

---

## 📚 Documentation des Sous-Systèmes

Chaque composant dispose de son propre `README.md` détaillé contenant sa documentation technique, son format d'API et ses commandes d'exploitation dédiées :

- 📦 [`broker_et_client/`](broker_et_client/README.md) — Architecture du plan de périphérie Fog & bus MQTT
  - 📡 [`broker_et_client/broker_mqtt/`](broker_et_client/broker_mqtt/README.md) — Broker Mosquitto, certificats TLS et bus d'événements
  - 🖥️ [`broker_et_client/client_mqtt/`](broker_et_client/client_mqtt/README.md) — Nœud Edge de streaming WebSocket, transcodage 720p et sharding
- 🔑 [`serveur_crypto/`](serveur_crypto/README.md) — Nœud cryptographique de zone, courbes BN254, schéma de Hess et SIS
- 🌐 [`serveur_web/`](serveur_web/README.md) — Orchestrateur web central multi-zone
  - ⚙️ [`serveur_web/backend/`](serveur_web/backend/README.md) — API FastAPI, signalisation décentralisée, gestionnaire de sessions et sharding
  - 🎨 [`serveur_web/frontend/`](serveur_web/frontend/README.md) — Interface React, player MSE, moteur crypto client et tableau de bord d'audit
- 🧪 [`tests/`](tests/README.md) — Suite de validation, benchmarks expérimentaux et tests d'interopérabilité
