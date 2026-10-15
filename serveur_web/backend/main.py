from pathlib import Path
from contextlib import asynccontextmanager
import datetime
import os

from fastapi import FastAPI, Depends, HTTPException, status, Response, Request
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
import jwt

# Importations compatibles avec `uvicorn backend.main:app` et `uvicorn main:app`.
try:
    from .models import get_db, User, SessionLocal, EdgeNode, DataLocation, StreamSession
    from . import schemas, security
    from .stream_router import router as stream_router, crypto_client, CRYPTO_SERVERS
    from .mqtt_client import start_mqtt, is_mqtt_connected, get_mqtt_health
except ImportError:
    from models import get_db, User, SessionLocal, EdgeNode, DataLocation, StreamSession
    import schemas
    import security
    from stream_router import router as stream_router, crypto_client, CRYPTO_SERVERS
    from mqtt_client import start_mqtt, is_mqtt_connected, get_mqtt_health

mqtt_client_instance = None

try:
    import docker
    docker_client = docker.from_env()
except Exception as e:
    print(f"API Docker locale indisponible : {e}", flush=True)
    docker_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global mqtt_client_instance
    mqtt_client_instance = start_mqtt()

    # Création du compte administrateur par défaut s'il n'existe pas encore (persistance)
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == "admin@admin.com").first()
        if not admin:
            hashed_pwd = security.get_password_hash("Azerty123")
            admin = User(email="admin@admin.com", hashed_password=hashed_pwd, role="admin")
            db.add(admin)
            db.commit()
    except Exception as e:
        print(f"Erreur init admin: {e}", flush=True)
    finally:
        db.close()

    yield

    if mqtt_client_instance:
        try:
            mqtt_client_instance.loop_stop()
            mqtt_client_instance.disconnect()
        except Exception:
            pass

    try:
        await crypto_client.aclose()
    except Exception:
        pass


app = FastAPI(title="Pledge Smart Edge API", version="1.0.0", lifespan=lifespan)

origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclusion du routeur de streaming
app.include_router(stream_router)


# --- HEALTHCHECK COMPLET ---
@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Contrôle de santé exhaustif pour la base, MQTT et les serveurs crypto."""
    from sqlalchemy import text

    health_status = {
        "status": "ok",
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "database": "ok",
        "mqtt": "connected" if is_mqtt_connected() else "disconnected",
        "mqtt_zones": get_mqtt_health(),
        "crypto_nodes": {},
    }

    # Test base de données
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        health_status["database"] = f"error: {e}"
        health_status["status"] = "degraded"

    if health_status["mqtt"] != "connected":
        health_status["status"] = "degraded"

    # Test serveurs crypto configurés
    for zone, url in CRYPTO_SERVERS.items():
        if url:
            try:
                res = await crypto_client.get(f"{url}/health", timeout=3.0)
                health_status["crypto_nodes"][zone] = "ok" if res.status_code == 200 else f"http_{res.status_code}"
            except Exception as e:
                health_status["crypto_nodes"][zone] = f"unreachable ({type(e).__name__})"
                health_status["status"] = "degraded"

    return health_status


# --- 1. INSCRIPTION ---
@app.post("/auth/register", response_model=schemas.UserOut)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="L'email est déjà utilisé")

    hashed_pwd = security.get_password_hash(user.password)
    new_user = User(email=user.email, hashed_password=hashed_pwd, role=user.role)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


# --- 2. CONNEXION ---
@app.post("/auth/login")
def login(user_credentials: schemas.UserLogin, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_credentials.email).first()
    if not user or not security.verify_password(user_credentials.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides")

    access_token = security.create_access_token(data={"sub": str(user.id)})
    refresh_token = security.create_refresh_token(data={"sub": str(user.id)})

    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=False, samesite="Lax", max_age=security.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=False, samesite="Lax", max_age=security.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)

    return {"message": "Connexion réussie", "user": {"email": user.email, "role": user.role}}


# --- 3. DÉCONNEXION ---
@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(key="access_token")
    response.delete_cookie(key="refresh_token")
    return {"message": "Déconnexion réussie"}


# --- MIDDLEWARE & PROTECTION DES ROUTES ---
def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Non authentifié")

    try:
        payload = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if user_id is None or token_type != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide")

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expiré")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide")

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Utilisateur introuvable")

    return user


# --- 4. RÉCUPÉRER L'UTILISATEUR CONNECTÉ ---
@app.get("/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {"user": {"email": current_user.email, "role": current_user.role}}


# --- EXEMPLE DE ROUTE PROTÉGÉE ---
@app.get("/api/stream/status")
def get_stream_status(current_user: User = Depends(get_current_user)):
    return {
        "message": f"Accès autorisé pour {current_user.email}",
        "role": current_user.role,
        "stream_url": "wss://edge-server-1/stream",
    }


# --- 5. ADMINISTRATION ---
@app.get("/api/admin/containers")
def get_docker_containers(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès refusé")

    if not docker_client:
        return {"error": "API Docker indisponible sur le serveur"}

    containers = []
    try:
        for c in docker_client.containers.list(all=True):
            containers.append({
                "id": c.short_id,
                "name": c.name,
                "status": c.status,
                "image": c.attrs.get("Config", {}).get("Image", "unknown"),
            })
        return containers
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/containers/{container_name}/logs")
def get_container_logs(container_name: str, current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès refusé")

    if not docker_client:
        return {"error": "API Docker indisponible"}

    try:
        container = docker_client.containers.get(container_name)
        logs = container.logs(tail=200).decode("utf-8")
        return {"logs": logs}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/nodes")
def get_edge_nodes(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès refusé")

    nodes = db.query(EdgeNode).all()
    zones = {}
    for node in nodes:
        if node.zone_id not in zones:
            zones[node.zone_id] = []
        zones[node.zone_id].append({
            "node_id": node.node_id,
            "ip_address": node.ip_address,
            "wss_port": node.wss_port,
            "storage_available_gb": node.storage_available_gb,
            "cpu_usage_pct": node.cpu_usage_pct,
            "last_seen": node.last_seen.isoformat() if node.last_seen else None,
        })
    return {"zones": zones}


@app.get("/api/streams/active")
def get_active_streams(db: Session = Depends(get_db)):
    """Retourne uniquement les flux en cours de diffusion (live et récents)."""
    now = datetime.datetime.utcnow()
    recent_threshold = now - datetime.timedelta(seconds=25)

    # 1. Sessions marquées live et actives récemment
    live_sessions = (
        db.query(StreamSession)
        .filter(StreamSession.status == "live")
        .filter(StreamSession.last_active >= recent_threshold)
        .all()
    )
    active_stream_ids = [s.session_id for s in live_sessions]

    # 2. Si aucune session formelle trouvée, vérifier les données actives récentes
    if not active_stream_ids:
        # Fallback pour compatibilité
        recent_locations = db.query(DataLocation).filter(DataLocation.blocks_count > 0).all()
        # N'inclut que si le nœud hôte est toujours actif
        active_nodes = {n.node_id for n in db.query(EdgeNode).filter(EdgeNode.last_seen >= now - datetime.timedelta(seconds=60)).all()}
        active_stream_ids = [loc.client_id for loc in recent_locations if loc.node_id in active_nodes]

    # Déduplication
    unique_streams = list(dict.fromkeys(active_stream_ids))
    return {"streams": unique_streams}


@app.get("/")
def read_root():
    with open(Path(__file__).with_name("index.html"), "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/html")
