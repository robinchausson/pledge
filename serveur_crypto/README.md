# Nœud Cryptographique & Serveur d'Identification — `serveur_crypto`

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.14-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Curve](https://img.shields.io/badge/Curve-BN254%20%2F%20Alt--bn128-orange.svg)](https://github.com/ethereum/py_ecc)
[![TLS](https://img.shields.io/badge/TLS-HTTPS%20:8443-green.svg)](#-sécurité-tls--certificats)

Service autonome d'infrastructure cryptographique déployé par zone géographique ou logique. Il implémente les phases 1, 2 et 3 du schéma d'intégrité de référence (*Sellami et al., FGCS 2024*) :
- **Phase 1** : Preuve d'intégrité matricielle SIS (L'Ecuyer 1994) et signature basée sur l'identité (Hess-IBS sur BN254).
- **Phase 2** : Serveur d'identification de zone ($IS_s$) dérivant la part secrète $S_w^s = t_s \cdot H_1(ID_w)$ et gestion de la mobilité multi-zone.
- **Phase 3** : Émission et vérification des clés de délégation de signature pour le transcodage périphérique Fog.

---

## 🏛️ Architecture & Rôle dans PLEDGE

```text
 Client Appelant (Streamer, Edge Transcoder ou Backend)
                       │
                       │ HTTPS :8443 (ou :8445)
                       ▼
 ┌────────────────────────────────────────────────────────┐
 │           NŒUD CRYPTOGRAPHIQUE FASTAPI (crypto_node.py)│
 │                                                        │
 │  1. Paramètres publics & Clé Maîtresse :               │
 │     - Scalaire secret de zone : ts                     │
 │     - Clé publique maîtresse : PK_s = ts · P_pub       │
 │                                                        │
 │  2. Preuve d'Intégrité SIS (Short Integer Solution) :  │
 │     - Générateur matriciel L'Ecuyer (l=64, m=1536)     │
 │     - Calcul matriciel : V = (A · X) mod 4099          │
 │                                                        │
 │  3. Signature Hess-IBS & Accouplements BN254 :         │
 │     - Signature directe : Sw = ts · H1(ID_w)           │
 │     - Délégation Fog proxy : DK_d = Sw + y · H1(ID_d)  │
 │     - Audit à 3 accouplements : e(w2, P) · e(...)      │
 └────────────────────────────────────────────────────────┘
```

---

## 📐 Paramètres Cryptographiques Conformes au Papier

### 1. Générateur Matriciel SIS (L'Ecuyer 1994)
La matrice $A \in \mathbb{Z}_q^{l \times m}$ est générée de manière déterministe en $O(1)$ à partir des paramètres publics $paramA = (I_0, a, C_0)$ sans avoir à stocker les $64 \times 1536 = 98\,304$ coefficients :
$$I_{n+1} = (a \cdot I_n + C_n) \bmod q, \quad C_{n+1} = \lfloor(a \cdot I_n + C_n) / q\rfloor$$

| Paramètre | Symbole | Valeur du Papier | Description |
| :--- | :---: | :---: | :--- |
| **Lignes** | $l$ | `64` | Dimension du vecteur de preuve résumée |
| **Colonnes** | $m$ | `1536` | Dimension du vecteur binaire développé |
| **Modulo** | $q$ | `4099` | Premier supérieur à $2^{12}$ |
| **Graine $I_0$** | $I_0$ | `1234` | État initial de congruence linéaire |
| **Multiplicateur $a$** | $a$ | `3141` | Facteur de récurrence pseudo-aléatoire |
| **Report $C_0$** | $C_0$ | `567` | Valeur initiale du report de division |

### 2. Schéma de Hess-IBS sur Courbe BN254
- **Groupes** : $G_1, G_2$ de torsion sur la courbe elliptique `alt_bn128` (ordre $r \approx 2^{254}$).
- **Accouplement bilinéaire optimal** : $e: G_1 \times G_2 \to G_T$.
- **Challenge** : $w_1 = H_2(V, paramA, r) \in \mathbb{Z}_r^*$.
- **Signature** : $\sigma = (w_1, w_2) \in \mathbb{Z}_r^* \times G_1$.

---

## 📡 Endpoints de l'API HTTPS (`:8443`)

| Méthode | Route | Rôle & Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Contrôle de santé Docker et sonde backend (`{"status": "ok", "zone_id": "Zone_1"}`) |
| `GET` | `/api/v1/crypto/parameters` | Retourne $PK_s$, $paramA$ et les dimensions SIS ($l, m, q$) |
| `POST` | `/api/v1/auth/identify` | Émet la part secrète $S_w^s = t_s \cdot H_1(ID_w)$ pour un diffuseur |
| `POST` | `/api/v1/crypto/mobility/accumulate` | Calcule l'accumulation homomorphe des clés ($S_w \leftarrow \sum S_w^k$, $PK \leftarrow \sum PK_k$) |
| `POST` | `/api/v1/crypto/integrity` | Calcule le vecteur SIS $V = (A \cdot x_i) \bmod q$ pour un fragment binaire |
| `POST` | `/api/v1/crypto/sign` | Signe un bloc vidéo selon le schéma Hess-IBS |
| `POST` | `/api/v1/crypto/verify` | Vérifie publiquement la signature d'un bloc par couplage de Weil/Tate |
| `POST` | `/api/v1/crypto/delegation/issue` | Génère une clé de délégation Fog $DK_d$ pour un nœud Edge |
| `POST` | `/api/v1/crypto/signature/delegate` | Appose la signature de transcodage avec clé déléguée |
| `POST` | `/api/v1/crypto/verify/delegate` | Vérification de signature déléguée avec 3 accouplements bilinéaires |

---

## 🔒 Sécurité TLS & Certificats

Le conteneur est configuré pour exiger HTTPS :
1. **Mode Développement / Local** : Le script [`docker-entrypoint.sh`](docker-entrypoint.sh) génère automatiquement une paire certificat/clé autosignée dans `./certs/` si absente.
2. **Mode Production** : Déposer le certificat et la clé signés par votre autorité dans `certs/tls.crt` et `certs/tls.key`.

---

## ⚙️ Configuration d'Environnement

Définie dans le fichier `.env` ou injectée par Docker Compose :

| Variable | Exemple | Description |
| :--- | :--- | :--- |
| `ZONE_ID` | `Zone_1` | Identifiant de zone géographique |
| `CRYPTO_SECRET_SCALAR` | `123456789` | Entier secret $t_s \in [1, r-1]$ dérivant la clé publique maîtresse |
| `CRYPTO_PORT` | `8443` | Port d'écoute HTTPS externe du conteneur |
| `TLS_CERT_FILE` | `/certs/tls.crt` | Emplacement du certificat TLS dans le conteneur |
| `TLS_KEY_FILE` | `/certs/tls.key` | Emplacement de la clé privée TLS dans le conteneur |

---

## 🚀 Commandes de Démarrage

### Mode 1 : Démarrage Local (Simulation Monomachine)
```bash
cd serveur_crypto

# Démarrer le conteneur sur le port 8443
docker compose -f docker-compose.local.yml up -d --build

# Vérifier le contrôle de santé
curl -k https://localhost:8443/health

# Arrêter le conteneur
docker compose -f docker-compose.local.yml down
```

### Mode 2 : Démarrage en Réseau LAN Dédié
```bash
cd serveur_crypto
cp .env.example .env

# Démarrer le service
docker compose up -d --build
```

### Mode 3 : Exécution Native Python (Développement sans Docker)
```bash
# Installer les dépendances
pip install -r requirements.txt

# Lancer le serveur avec Uvicorn en HTTPS
CRYPTO_SECRET_SCALAR=123456789 uvicorn crypto_node:app \
  --host 0.0.0.0 --port 8443 \
  --ssl-certfile certs/tls.crt --ssl-keyfile certs/tls.key
```

---

## 🧪 Exemple d'Appel API avec cURL

### Récupération des paramètres publics
```bash
curl -k https://localhost:8443/api/v1/crypto/parameters
```

### Calcul d'une preuve d'intégrité SIS
```bash
curl -k -X POST https://localhost:8443/api/v1/crypto/integrity \
  -H "Content-Type: application/json" \
  -d '{"payload_b64":"VGVzdCBkZSBibG9jIHZpZMOpbyBQbGVkZ2U=","paramA":{"I0":1234,"a":3141,"C0":567}}'
```
