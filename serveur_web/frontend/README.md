# Client Web & Moteur Cryptographique — `serveur_web/frontend`

[![React](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-6+-646CFF.svg)](https://vitejs.dev/)
[![Noble Curves](https://img.shields.io/badge/@noble%2Fcurves-BN254%20%2F%20Alt--bn128-orange.svg)](https://github.com/paulmillr/noble-curves)
[![Tests](https://img.shields.io/badge/Crypto%20Tests-8%2F8%20Passed-success.svg)](#-tests-unitaires-du-moteur-cryptographique)

Le **Frontend Pledge** est une Single Page Application (SPA) réactive bâtie avec **React 19** et **Vite**. Elle intègre un **moteur cryptographique autonome côté client** (`cryptoClient.js`) capable d'effectuer des preuves matricielles SIS et des accouplements bilinéaires de Weil/Tate directement dans le navigateur, assurant un **audit d'intégrité public sans tiers de confiance (Zero-TPA)** conforme à la Phase 5 du papier de recherche (*Sellami et al., FGCS 2024*).

---

## 🏛️ Architecture & Composants Clés

```text
 ┌────────────────────────────────────────────────────────────────────────┐
 │                      APPLICATION FRONTEND REACT                        │
 │                                                                        │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │              MOTEUR CRYPTOGRAPHIQUE (cryptoClient.js)            │  │
 │  │                                                                  │  │
 │  │  • Générateur matriciel L'Ecuyer (A 64x1536 en O(1) en mémoire)  │  │
 │  │  • Décomposition binaire x_i in {0,1}^1536 (SHA-256 x 6)        │  │
 │  │  • Preuve SIS locale : v_i = (A · x_i) mod 4099  [0.17 ms]       │  │
 │  │  • Accouplement BN254 : e(w2, P) · e(H1(ID), -PK)^w1 [~24 ms]    │  │
 │  │  • Accouplement délégué Fog à 3 pairings (transcodage 720p)      │  │
 │  │  • ClientMobilityManager : Accumulation homomorphe Sw et PK      │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 │                                                                        │
 │  ┌───────────────────────────────┐  ┌───────────────────────────────┐  │
 │  │    STUDIO DIFFUSEUR           │  │      LECTEUR SPECTATEUR       │  │
 │  │   (StreamerInterface)         │  │     (ViewerInterface)         │  │
 │  │ • Capture Webcam WebM         │  │ • Multi-Sockets WebSocket     │  │
 │  │ • Calcul SIS vi & Signature   │  │ • Resequencing Buffer         │  │
 │  │ • Sharding : Bloc 0 diffusé,  │  │ • Décodeur MSE (MediaSource)  │  │
 │  │   puis D_i -> FN_{(i-1) mod K}│  │ • Audit en temps réel HUD     │  │
 │  └───────────────────────────────┘  └───────────────────────────────┘  │
 └────────────────────────────────────────────────────────────────────────┘
```

---

## 🔑 Fonctionnalités Cryptographiques Client (`cryptoClient.js`)

1. **Reconstruction Matricielle de L'Ecuyer (1994)** :
   - Génère déterministement la matrice publique $A \in \mathbb{Z}_q^{l \times m}$ ($l=64, m=1536, q=4099$) à partir des paramètres $paramA = (I_0=1234, a=3141, C_0=567)$.
   - Mise en cache en mémoire vive pour des calculs ultérieurs instantanés en $O(1)$.
2. **Preuve Matricielle SIS (Short Integer Solution)** :
   - Hachage SHA-256 répété 6 fois ($256 \times 6 = 1536$ bits) pour former le vecteur binaire $x_i \in \{0, 1\}^{1536}$.
   - Multiplication matricielle $v_i = (A \cdot x_i) \bmod 4099$ réalisée en **0.17 ms**.
3. **Vérification Hess-IBS par Accouplements Bilinéaires (BN254)** :
   - Évalue la validité mathématique de la signature $\sigma = (w_1, w_2)$ sans faire confiance au réseau :
     $$r' = e(w_2, P) \cdot e(H_1(ID_w), -PK)^{w_1}, \quad w_1 \stackrel{?}{= H_2(V', paramA, r')}$$
4. **Validation de Délégation Fog (Transcodage 720p)** :
   - Évalue 3 accouplements bilinéaires pour authentifier qu'une modification de résolution a été légitimement déléguée par le diffuseur à un nœud Fog spécifique :
     $$r' = e(w_2, P) \cdot e(w_1 \cdot (-H_1(ID_w)), PK) \cdot e(w_1 \cdot (-H_1(ID_d)), PK_d)$$
5. **Gestionnaire de Mobilité Client (`ClientMobilityManager`)** :
   - Conforme à la Section 3.4 du papier : lorsque le diffuseur bascule de zone, ses parts secrètes et clés publiques s'accumulent homomorphiquement :
     $$S_w \leftarrow S_w + S_w^k, \quad PK \leftarrow PK + PK_k$$
   - Le flux continue sans coupure ni réinitialisation de session.

---

## 📺 Lecteur Vidéo & Resequencing Buffer (Fog Sharding)

Lorsqu'une session utilise le **Fog Sharding (Option B)** :
- **Connexions WebSocket Concurrentes** : Le lecteur ouvre simultanément un flux WebSocket vers chaque nœud Fog hébergeant des fragments du flux.
- **Protection de l'Amorçage MSE** : En WebM, le `SourceBuffer` du navigateur ne peut décoder les Clusters vidéo que si le **Bloc 0 (EBML + Tracks Header)** est injecté en premier. Le buffer retient tout fragment tant que le bloc 0 n'est pas parvenu.
- **File de Réordonnancement Stricte (`resequencingMap`)** : Réassemble et réordonne les paquets non contigus ($0, 1, 2, 3\dots$) même en présence de gigue (*jitter*) réseau entre les différents nœuds Fog.
- **Tableau de Bord d'Intégrité en Direct** :
  - 🟢 **Flux intègre** : Preuve SIS et signature Hess-IBS validées.
  - 🟡 **720p Transcodé** : Délégation Fog validée avec succès.
  - 🔴 **Altération détectée** : Coupure instantanée du lecteur si un seul bit a été altéré en transit.
  - ⚡ **Fog Sharding (N Nœuds)** : Indication du nombre de nœuds Fog participant à la livraison.

---

## 🚀 Commandes de Démarrage

### 1. Installation des Dépendances
```bash
cd serveur_web/frontend
npm install
```

### 2. Lancement en Mode Développement (Vite HMR)
```bash
# Démarre le serveur local sur http://localhost:5173
npm run dev

# Exposer sur le réseau local (accessible par les autres machines)
npm run dev -- --host
```

### 3. Compilation pour la Production
```bash
npm run build
```
Les fichiers compilés et optimisés sont générés dans le dossier `dist/`.

---

## 🧪 Tests Unitaires du Moteur Cryptographique

Une suite de tests dédiée valide l'ensemble des algorithmes cryptographiques implémentés dans `cryptoClient.js` :

```bash
npm test
```

Résultats validés (8/8 tests réussis) :
```text
=== DÉBUT DES TESTS UNITAIRES FRONTEND CRYPTO (PHASE 5) ===
[Test 1] Génération déterministe de la matrice A (L'Ecuyer 1994)... -> Succès
[Test 2] Vecteur binaire x_i in {0, 1}^m... -> Succès
[Test 3] Calcul SIS local v_i = (A * x_i) mod q... -> Succès (0.17 ms)
[Test 4] Cycle complet Hess-IBS (Setup, Sign, Public Verify)... -> Succès
[Test 5] Détection de corruption de trame... -> Succès
[Test 6] Vérification de signature déléguée Fog (Transcodage)... -> Succès
[Test 7] Audit d'un paquet bloc vidéo reçu par le viewer... -> Succès (~24 ms)
[Test 8] Support Client de la Mobilité Homomorphe (Section 3.4)... -> Succès
=== TOUS LES TESTS DU MODULE FRONTEND PASSENT AVEC SUCCÈS (8/8) ===
```

---

## ⚙️ Configuration d'Environnement

Le fichier `.env` ou les variables Vite permettent d'ajuster l'URL du backend :

```env
# URL d'accès à l'API Backend FastAPI
VITE_API_URL=http://localhost:8000
```
Pour un accès depuis d'autres appareils du réseau local, remplacez `localhost` par l'IP LAN de votre machine hôte (ex: `http://192.168.1.100:8000`).
