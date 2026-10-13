import os
import sys
from pathlib import Path

# Résolution des chemins racines du projet
ROOT_DIR = Path(__file__).parent.parent.resolve()
CRYPTO_DIR = ROOT_DIR / "serveur_crypto"
BACKEND_DIR = ROOT_DIR / "serveur_web" / "backend"
CLIENT_MQTT_DIR = ROOT_DIR / "broker_et_client" / "client_mqtt"

# Injection prioritaire dans sys.path pour les imports des modules testés
for directory in [str(CRYPTO_DIR), str(BACKEND_DIR), str(CLIENT_MQTT_DIR), str(ROOT_DIR)]:
    if directory not in sys.path:
        sys.path.insert(0, directory)

# Configuration par défaut des variables d'environnement de test
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_app.db")
os.environ.setdefault("ZONE_ID", "Zone_1")
os.environ.setdefault("CRYPTO_SECRET_SCALAR", "123456789")
