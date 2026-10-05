# Nœud cryptographique de zone

Service Docker autonome chargé des opérations cryptographiques d'une zone : publication des paramètres publics, calcul d'une preuve d'intégrité SIS et génération d'une signature de Hess-IBS.

Le nœud expose uniquement une API HTTPS. Il ne contient ni broker MQTT, ni client MQTT, ni interface d'administration, ni logique de streaming. Le broker de la zone peut tourner dans un autre conteneur sur la même machine, mais il n'est pas géré par ce projet.

## 1. Architecture

```text
Client appelant
      |
      | HTTPS :8443
      v
Nœud crypto Docker
      |
      +-- paramètres publics de zone
      +-- calcul SIS de V
      +-- signature Hess-IBS

Broker MQTT de zone : service externe, non contacté par ce nœud
```

Une instance du nœud doit être déployée par zone. Chaque instance possède sa propre clé de zone et son propre certificat TLS.

## 2. Prérequis

- Docker Engine ou Docker Desktop ;
- Docker Compose v2 ;
- un scalaire secret de zone conservé hors du dépôt ;
- en production, un certificat TLS et sa clé privée signés par l'autorité de certification utilisée par les clients.

Le service utilise Python 3.11 dans l'image Docker et les bibliothèques suivantes :

- FastAPI et Uvicorn pour l'API HTTPS ;
- NumPy pour le calcul matriciel SIS ;
- `py_ecc` pour les opérations BN128 et le pairing.

## 3. Démarrage

Toutes les commandes se lancent depuis la racine du dépôt, là où se trouve `docker-compose.yml`.

### Démarrage standard

```bash
docker compose up -d --build
```

Le conteneur s'appelle `crypto_node_zone_alpha` et écoute sur `https://localhost:8443`.

### Vérification du service

```bash
docker compose ps
docker compose logs -f crypto-node
curl -k https://localhost:8443/health
```

Réponse attendue pour le contrôle de santé :

```json
{"status":"ok"}
```

### Arrêt et suppression du conteneur

```bash
docker compose down
```

`docker compose down` arrête uniquement le nœud crypto. Il ne modifie aucun broker externe.

## 4. Configuration

La configuration est définie dans `docker-compose.yml`.

| Variable | Valeur par défaut | Description |
| --- | --- | --- |
| `CRYPTO_SECRET_SCALAR` | vide | Scalaire secret utilisé pour dériver la clé publique de zone. À définir en production. |
| `TLS_CERT_FILE` | `/certs/tls.crt` | Chemin du certificat TLS dans le conteneur. |
| `TLS_KEY_FILE` | `/certs/tls.key` | Chemin de la clé privée TLS dans le conteneur. |

### Définir le secret de zone

Le scalaire doit être un entier strictement compris entre `1` et `curve_order - 1`, où `curve_order` est l'ordre du groupe BN128.

Sous Linux/macOS :

```bash
export CRYPTO_SECRET_SCALAR='123456789'
docker compose up -d --build
```

Sous PowerShell :

```powershell
$env:CRYPTO_SECRET_SCALAR = "123456789"
docker compose up -d --build
```

Ne versionnez jamais cette valeur dans Git. Il est recommandé d'utiliser un gestionnaire de secrets en production et de conserver la même valeur après chaque redémarrage.

Si la variable est absente, le nœud génère un scalaire aléatoire au démarrage. Cette identité est temporaire : la clé publique changera au prochain redémarrage.

## 5. TLS

Le script `docker-entrypoint.sh` vérifie les fichiers indiqués par `TLS_CERT_FILE` et `TLS_KEY_FILE` :

- s'ils existent, ils sont utilisés ;
- s'ils sont absents, un certificat RSA autosigné valable 365 jours est généré dans le volume `./certs`.

Le certificat autosigné est pratique pour le développement. Les clients doivent alors désactiver la vérification du certificat, par exemple avec `curl -k`. Ce comportement ne doit pas être utilisé tel quel en production.

Pour la production :

1. placer le certificat dans `certs/tls.crt` ;
2. placer sa clé privée dans `certs/tls.key` ;
3. protéger les permissions de la clé privée ;
4. redémarrer le conteneur.

Le répertoire `certs` est ignoré par Git pour éviter de publier des clés privées et des certificats locaux.

## 6. Paramètres cryptographiques

### SIS

Pour un fragment binaire $D$, le nœud calcule :

1. `H3(D)` avec SHA-256 ;
2. un vecteur `X` de taille `m = 1536` en répétant le digest ;
3. une matrice pseudo-aléatoire `A` de dimension `l x m` avec la graine publique `paramA = 42` ;
4. `V = (A x X) mod q` avec `q = 4099`.

Paramètres utilisés :

| Paramètre | Valeur |
| --- | --- |
| `paramA` | `42` |
| `l` | `64` |
| `m` | `1536` |
| `q` | `4099` |
| Fonction de hachage | SHA-256 |

Le résultat `V_matrix` contient donc 64 entiers compris entre `0` et `4098`.

### Hess-IBS / BN128

Le nœud utilise la courbe `bn128` et le groupe `G1` :

- génération d'un nonce aléatoire ;
- génération d'un point `p1` ;
- calcul d'un pairing dans `G2` ;
- dérivation du challenge `w1` avec SHA-256 ;
- calcul du point `w2`.

La réponse de signature contient :

- `w1` : challenge scalaire ;
- `w2` : point de signature sous forme `[x, y]` ;
- `p1` : point public auxiliaire sous forme `[x, y]`.

### Note sur la gestion actuelle du secret

L'implémentation actuelle dérive `public_key = secret_scalar * G1` au démarrage et utilise cette valeur comme `secret_share` pour la signature. Cela représente le nœud crypto autonome actuel, mais ne constitue pas encore un KGC distribué avec attribution de shares à des identités. Une intégration KGC devra remplacer cette initialisation avant un déploiement cryptographique définitif.

## 7. API HTTPS

Toutes les routes sont servies en HTTPS sur le port `8443`.

### `GET /`

Vérifie que l'application répond.

Réponse :

```json
{"service":"crypto-node","status":"ok"}
```

### `GET /health`

Route destinée aux sondes Docker ou à un reverse proxy.

Réponse :

```json
{"status":"ok"}
```

### `GET /api/v1/crypto/parameters`

Retourne les paramètres nécessaires aux clients pour interpréter les preuves et signatures de la zone.

Exemple :

```bash
curl -k https://localhost:8443/api/v1/crypto/parameters
```

Réponse :

```json
{
  "curve": "bn128",
  "public_key": [1, 2],
  "paramA": 42,
  "sis": {
    "l": 64,
    "m": 1536,
    "q": 4099,
    "hash": "SHA-256"
  },
  "signature": {
    "scheme": "Hess-IBS",
    "group": "G1"
  }
}
```

Les valeurs `[1, 2]` sont uniquement illustratives ; la réponse réelle contient les coordonnées de la clé publique BN128.

### `POST /api/v1/crypto/integrity`

Calcule la preuve d'intégrité SIS d'un fragment.

Corps JSON :

```json
{
  "fragment_data": "ZnJhZ21lbnQtdGVzdA==",
  "paramA": 42
}
```

`fragment_data` doit être le fragment binaire encodé en Base64. `paramA` est optionnel et vaut `42` par défaut.

Exemple :

```bash
curl -k -X POST https://localhost:8443/api/v1/crypto/integrity \
  -H "Content-Type: application/json" \
  -d '{"fragment_data":"ZnJhZ21lbnQtdGVzdA==","paramA":42}'
```

Réponse :

```json
{
  "paramA": 42,
  "V_matrix": [0, 123, 2048]
}
```

L'exemple de matrice est tronqué ; la réponse réelle contient 64 valeurs.

### `POST /api/v1/crypto/signature`

Calcule la preuve SIS puis signe cette preuve.

Exemple :

```bash
curl -k -X POST https://localhost:8443/api/v1/crypto/signature \
  -H "Content-Type: application/json" \
  -d '{"fragment_data":"ZnJhZ21lbnQtdGVzdA==","paramA":42}'
```

Réponse :

```json
{
  "paramA": 42,
  "V_matrix": [0, 123, 2048],
  "signature": {
    "w1": 123456,
    "w2": [111, 222],
    "p1": [333, 444]
  }
}
```

Les valeurs et la matrice de cet exemple sont illustratives. `w2` et `p1` contiennent chacun deux coordonnées réelles du groupe `G1`.

## 8. Codes d'erreur

| Code | Cause |
| --- | --- |
| `200` | Requête traitée avec succès. |
| `422` | JSON invalide, Base64 invalide ou `paramA` différent de `42`. |
| `503` | Paramètres demandés avant l'initialisation de la clé de zone. |
| `500` | Erreur interne ou état cryptographique invalide. |

Exemple de rejet d'un mauvais `paramA` :

```json
{"detail":"paramA doit valoir 42"}
```

## 9. Intégration avec un autre service

Le service appelant doit :

1. récupérer les paramètres avec `GET /api/v1/crypto/parameters` ;
2. conserver le fragment original ;
3. l'encoder en Base64 ;
4. appeler `/integrity` ou `/signature` avec `paramA = 42` ;
5. associer `V_matrix` et `signature` au fragment transmis ;
6. transmettre ces données au broker ou au service suivant de l'architecture.

Le nœud ne publie rien et ne connaît pas le topic MQTT. Cette séparation permet de remplacer le broker sans modifier le conteneur crypto.

## 10. Développement local sans Docker

Pour tester l'application avec un environnement Python local :

```bash
pip install -r requirements.txt
CRYPTO_SECRET_SCALAR=123456 \
uvicorn crypto_node:app --host 0.0.0.0 --port 8443 \
  --ssl-certfile certs/tls.crt --ssl-keyfile certs/tls.key
```

Les certificats doivent exister avant cette commande. Le chemin recommandé reste le démarrage Docker, qui les génère automatiquement pour le développement.

## 11. Dépannage

### Le conteneur redémarre en boucle

Consulter les logs :

```bash
docker compose logs crypto-node
```

Causes fréquentes :

- `CRYPTO_SECRET_SCALAR` n'est pas un entier ;
- le scalaire est nul ou hors de l'intervalle BN128 ;
- la clé TLS montée est invalide ou ne correspond pas au certificat ;
- le port `8443` est déjà utilisé.

### Le navigateur signale un certificat non fiable

C'est attendu avec le certificat autosigné de développement. Utiliser un certificat signé en production. Avec `curl`, `-k` désactive uniquement la vérification pour les tests locaux.

### Le client reçoit `422`

Vérifier que :

- le corps possède l'en-tête `Content-Type: application/json` ;
- `fragment_data` est du Base64 valide ;
- `paramA` vaut `42`.

### Le client reçoit `503`

Le nœud n'a pas terminé son initialisation. Vérifier les logs et attendre que Uvicorn indique que l'application est prête.

## 12. Vérifications du dépôt

Commandes utiles avant livraison :

```bash
python -m py_compile crypto_node.py
docker compose config
docker compose build
```

Le build ne démarre ni ne configure de broker MQTT.