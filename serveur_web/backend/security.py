import os
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt

# Constantes de sécurité (surchargées via .env en production)
SECRET_KEY = os.getenv("JWT_SECRET_KEY", os.getenv("EDGE_JWT_SECRET", "VOTRE_CLE_SECRETE_TRES_LONGUE_ET_COMPLEXE"))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifie si le mot de passe en clair correspond au hash."""
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password: str) -> str:
    """Hache le mot de passe."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def create_access_token(data: dict) -> str:
    """Crée un JWT à courte durée de vie."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: dict) -> str:
    """Crée un JWT à longue durée de vie."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_stream_token(session_id: str, role: str, node_id: str = "", expire_minutes: int = 120) -> str:
    """Crée un jeton d'accès signé éphémère pour upload ou download WebSocket sur nœud Edge."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=expire_minutes)
    payload = {
        "sub": session_id,
        "role": role,
        "node_id": node_id,
        "exp": expire,
        "type": "stream_access",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_stream_token(token: str, session_id: str, expected_role: str) -> bool:
    """Valide la signature, l'expiration et les permissions d'un jeton d'accès stream."""
    if not token:
        return False
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("type") != "stream_access":
            return False
        if payload.get("sub") != session_id:
            return False
        if payload.get("role") != expected_role:
            return False
        return True
    except Exception:
        return False
