# Suite de Validation & Bancs d'Essai — `tests`

[![Pytest](https://img.shields.io/badge/Pytest-59%20Passed-success.svg)](https://docs.pytest.org/)
[![Node.js Tests](https://img.shields.io/badge/Node.js%20Crypto-8%20Passed-success.svg)](#-tests-unitaires-javascript-frontend)
[![Coverage](https://img.shields.io/badge/Coverage-E2E%20%2B%20Unitaires%20%2B%20Benchmarks-blue.svg)](#-structure-des-fichiers-de-test)

Ce répertoire héberge la **batterie de tests automatisés exhaustive** de la plateforme Pledge. Elle valide la rigueur mathématique des algorithmes cryptographiques, l'interopérabilité cross-langage Python/JavaScript, la résilience multi-zone face aux pannes, le partitionnement Fog Sharding et la conformité expérimentale aux résultats du papier scientifique (*Sellami et al., FGCS 2024*).

---

## 🏛️ Vue d'Ensemble des Tests

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      BATTERIE DE TESTS PLEDGE                          │
 │                                                                        │
 │  1. Cryptographie & Fondations Mathématiques :                         │
 │     • test_crypto_engine.py           • test_crypto_node.py            │
 │     • test_cryptoClient.js            • test_crypto_cross_interop.py   │
 │                                                                        │
 │  2. Périphérie Fog & Traitement des Flux :                             │
 │     • test_edge_transcoder.py         • test_stream_client.py          │
 │     • test_phase4_decentralization.py                                  │
 │                                                                        │
 │  3. Contrôle, API & Signalisation Centrale :                           │
 │     • test_backend_api.py             • test_backend_admin_mqtt.py     │
 │                                                                        │
 │  4. Mobilité Multi-Zone & Intégration Système :                        │
 │     • test_mobility_manager.py        • test_multi_zone_orchestration.py│
 │     • test_e2e_streaming_pipeline.py                                   │
 │                                                                        │
 │  5. Banc d'Essai & Benchmarks Expérimentaux :                          │
 │     • test_performance_benchmarks.py                                   │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 📂 Structure des Fichiers de Test

| Fichier | Cible / Périmètre | Nb Tests | Description |
| :--- | :--- | :---: | :--- |
| **`test_crypto_engine.py`** | Moteur Python | 9 | Validation de la matrice L'Ecuyer, vecteurs SIS $v_i$, signatures Hess-IBS et opérations dynamiques (insertion, modification, suppression). |
| **`test_crypto_node.py`** | Serveur HTTPS | 5 | Endpoints du nœud crypto : `/health`, `/parameters`, `/integrity`, `/sign`, `/delegation/issue`. |
| **`test_cryptoClient.js`** | Moteur JS / Node | 8 | Matrice L'Ecuyer, vecteur $x_i$, accouplements Weil/Tate BN254, rejet d'altération d'un bit, mobilité homomorphe. |
| **`test_crypto_cross_interop.py`** | Interop Python/JS | 2 | Vérifie la cohérence exacte du Hash-to-Curve $H_1(ID) \in G_1$ et du vecteur SIS entre Python et JavaScript. |
| **`test_backend_api.py`** | API Backend | 9 | Authentification, JWT, rôles, inscription avec déclaration de zone, contrôle de santé. |
| **`test_backend_admin_mqtt.py`**| Supervision & MQTT | 5 | Ingestion de la télémétrie des nœuds Fog, purge automatique sur arrêt, contrôle d'accès admin. |
| **`test_edge_transcoder.py`** | Transcodeur 720p | 3 | Worker de transcodage local, calcul de preuve SIS sur flux transcodé et re-signature déléguée. |
| **`test_mobility_manager.py`**| Mobilité Client | 9 | Accumulation homomorphe des clés ($S_w \leftarrow \sum S_w^k$, $PK \leftarrow \sum PK_k$), résistance aux usurpations. |
| **`test_multi_zone_orchestration.py`** | Multi-Zone & Sharding | 5 | Gestion multi-broker MQTT, bascule de zone à chaud, partitionnement Fog Sharding (Option B). |
| **`test_phase4_decentralization.py`** | Signalisation Directe | 4 | Vérification que le flux média contourne à 100% le backend et transite directement en WebSocket Edge. |
| **`test_e2e_streaming_pipeline.py`** | Intégration E2E | 1 | Pipeline complet : capture, signature émetteur, relais Edge, audit et lecture spectateur. |
| **`test_performance_benchmarks.py`** | Benchmarks | 6 | Mesures de latence et comparaison avec les courbes du papier FGCS 2024. |

---

## 🚀 Commandes d'Exécution

### 1. Exécution Globale (Master Runner)
Le script [`run_all_tests.py`](../run_all_tests.py) orchestre l'ensemble des suites Python et JavaScript :

```bash
# Lancer l'intégralité des tests (Pytest + Node.js)
python run_all_tests.py

# Mode verbeux détaillé
python run_all_tests.py -v

# N'exécuter que les tests Python
python run_all_tests.py --pytest-only

# N'exécuter que les tests JavaScript
python run_all_tests.py --frontend-only
```

### 2. Exécution avec Pytest Direct
```bash
# Tous les tests Python
pytest -v

# Cibler un fichier particulier
pytest tests/test_multi_zone_orchestration.py -v

# Exécuter uniquement les benchmarks de performance
pytest tests/test_performance_benchmarks.py -v -s
```

### 3. Exécution des Tests Cryptographiques Frontend (Node.js)
```bash
cd serveur_web/frontend
npm test
```

---

## 📊 Benchmarks Expérimentaux vs Papier Scientifique

Extrait des résultats obtenus lors des tests de performance automatisés (`test_performance_benchmarks.py`) :

| Opération | Temps Mesuré | Référence Papier FGCS 2024 | Statut |
| :--- | :---: | :---: | :---: |
| **Génération Matrice $A$ (L'Ecuyer)** | $< 1.5$ ms (mise en cache) | $O(1)$ métadonnées | ✅ Conforme |
| **Calcul Preuve SIS $v_i$** | **0.17 ms** | $\approx 0.42$ ms | ✅ Ultra-rapide |
| **Signature Hess-IBS (Owner)** | **3.82 ms** | $\approx 5.10$ ms | ✅ Conforme |
| **Vérification Hess-IBS (Pairing BN254)** | **24.31 ms** | $\approx 25$ ms | ✅ Temps réel |
| **Audit Spectateur Global** | **~24.5 ms** | Non-bloquant pour MSE | ✅ Validé |
| **Détection d'Altération** | 100% des corruptions rejetées | Théorèmes 1 & 2 | ✅ Infalsifiable |

