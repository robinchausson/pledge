from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, status, Response, Request
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
import jwt

# Importations compatibles avec `uvicorn backend.main:app` et `uvicorn main:app`.
try:
    from .models import get_db, User
    from . import schemas, security
    from .stream_router import router as stream_router
    from .mqtt_client import start_mqtt
except ImportError:
    from models import get_db, User
    import schemas
    import security
    from stream_router import router as stream_router
    from mqtt_client import start_mqtt

mqtt_client_instance = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global mqtt_client_instance
    mqtt_client_instance = start_mqtt()
    yield
    if mqtt_client_instance:
        mqtt_client_instance.loop_stop()
        mqtt_client_instance.disconnect()

app = FastAPI(title="Smart Home Edge API", lifespan=lifespan)

# Configuration CORS pour autoriser le futur frontend React à communiquer avec l'API
origins = [
    "http://localhost:5173", # Port par défaut de Vite (React)
    "http://127.0.0.1:5173",
    "http://localhost:3000", # Port par défaut de Create React App
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True, # Très important pour accepter les cookies
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inclusion du routeur de streaming
app.include_router(stream_router)

# --- 1. INSCRIPTION ---
@app.post("/auth/register", response_model=schemas.UserOut)
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    # Vérifier si l'utilisateur existe déjà
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="L'email est déjà utilisé")
    
    # Hacher le mot de passe et sauvegarder
    hashed_pwd = security.get_password_hash(user.password)
    new_user = User(email=user.email, hashed_password=hashed_pwd, role=user.role)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

# --- 2. CONNEXION ---
@app.post("/auth/login")
def login(user_credentials: schemas.UserLogin, response: Response, db: Session = Depends(get_db)):
    # Vérifier l'utilisateur
    user = db.query(User).filter(User.email == user_credentials.email).first()
    if not user or not security.verify_password(user_credentials.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides")
    
    # Générer les tokens
    access_token = security.create_access_token(data={"sub": str(user.id)})
    refresh_token = security.create_refresh_token(data={"sub": str(user.id)})
    
    # Injecter les tokens dans des Cookies HTTP-Only
    response.set_cookie(key="access_token", value=access_token, httponly=True, secure=False, samesite="Lax", max_age=security.ACCESS_TOKEN_EXPIRE_MINUTES*60)
    response.set_cookie(key="refresh_token", value=refresh_token, httponly=True, secure=False, samesite="Lax", max_age=security.REFRESH_TOKEN_EXPIRE_DAYS*24*60*60)
    
    return {"message": "Connexion réussie", "user": {"email": user.email, "role": user.role}}

# --- 3. DÉCONNEXION ---
@app.post("/auth/logout")
def logout(response: Response):
    # Supprimer les cookies
    response.delete_cookie(key="access_token")
    response.delete_cookie(key="refresh_token")
    return {"message": "Déconnexion réussie"}


# --- MIDDLEWARE & PROTECTION DES ROUTES ---
def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Non authentifié")
    
    try:
        # Décoder le token
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

# --- EXEMPLE DE ROUTE PROTÉGÉE ---
@app.get("/api/stream/status")
def get_stream_status(current_user: User = Depends(get_current_user)):
    return {
        "message": f"Accès autorisé pour {current_user.email}",
        "role": current_user.role,
        "stream_url": "wss://edge-server-1/stream"
    }

@app.get("/")
def read_root():
    with open(Path(__file__).with_name("index.html"), "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/html")
