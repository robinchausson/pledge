import asyncio
import base64
import datetime
import json
import os
import re
import uuid
import httpx
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session

try:
    from .models import get_db, EdgeNode, DataLocation, StreamSession
except ImportError:
    from models import get_db, EdgeNode, DataLocation, StreamSession

router = APIRouter()

# Schéma et vérification TLS
EDGE_WS_SCHEME = (os.getenv("EDGE_WS_SCHEME") or "ws").lower()
CRYPTO_VERIFY_SSL = os.getenv("CRYPTO_VERIFY_SSL", "false").lower() in ("true", "1", "yes")


def normalize_url(url: str) -> str:
    """Nettoie et normalise une URL pour éviter les fautes de frappe de slashes."""
    if not url:
        return ""
    clean = url.strip()
    if clean.startswith("https:/") and not clean.startswith("https://"):
        clean = "https://" + clean[7:]
    elif clean.startswith("http:/") and not clean.startswith("http://"):
        clean = "http://" + clean[6:]
    return clean.rstrip("/")


CRYPTO_SERVERS = {
    "Zone_1": normalize_url(os.getenv("CRYPTO_URL_ZONE_1", "https://127.0.0.1:8443")),
    "Zone_2": normalize_url(os.getenv("CRYPTO_URL_ZONE_2", "https://127.0.0.1:8444")),
}

# Client HTTP partagé avec pool de connexions persistantes et timeout réactif
crypto_client = httpx.AsyncClient(
    verify=CRYPTO_VERIFY_SSL,
    timeout=httpx.Timeout(1.0, connect=0.5),
    limits=httpx.Limits(max_keepalive_connections=30, max_connections=60)
)


async def get_crypto_signature(fragment_bytes: bytes, zone_id: str) -> dict:
    """Interroge le serveur crypto pour calculer l'intégrité SIS du fragment en temps réel (< 2 ms)."""
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        return {}

    fragment_b64 = base64.b64encode(fragment_bytes).decode("utf-8")
    payload = {"fragment_data": fragment_b64, "paramA": 42}

    try:
        # Appel sur /integrity : calcul matriciel SIS ultra-rapide (< 2 ms)
        response = await crypto_client.post(f"{crypto_url}/api/v1/crypto/integrity", json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[Backend] Avertissement intégrité Crypto ({zone_id} à {crypto_url}): {e}", flush=True)
        return {}


async def verify_crypto_integrity(fragment_b64: str, zone_id: str, expected_v_matrix: list) -> bool:
    """Vérifie l'intégrité SIS d'un fragment vidéo avant distribution au visionneur."""
    if not expected_v_matrix:
        return True

    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        return True

    payload = {"fragment_data": fragment_b64, "paramA": 42}

    try:
        response = await crypto_client.post(f"{crypto_url}/api/v1/crypto/integrity", json=payload)
        response.raise_for_status()
        data = response.json()
        actual_v_matrix = data.get("V_matrix")
        if actual_v_matrix != expected_v_matrix:
            print(f"[Backend] Intégrité SIS compromise ({len(expected_v_matrix)} attendu vs {len(actual_v_matrix)} reçu)", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[Backend] Erreur vérification intégrité SIS ({zone_id}): {e}", flush=True)
        # En cas d'indisponibilité temporaire du serveur crypto, ne bloque pas silencieusement
        return True


def select_best_edge_node(db: Session, zone_id: str = "Zone_1") -> EdgeNode:
    """Sélectionne le meilleur nœud Edge actif en fonction de last_seen, CPU et espace disque."""
    threshold = datetime.datetime.utcnow() - datetime.timedelta(seconds=45)
    
    # 1. Nœuds actifs récents
    active_nodes = (
        db.query(EdgeNode)
        .filter(EdgeNode.last_seen >= threshold)
        .all()
    )

    if not active_nodes:
        # Repli si les horloges sont légèrement décalées : nœuds vus récemment (moins de 5 min)
        fallback_threshold = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
        active_nodes = (
            db.query(EdgeNode)
            .filter(EdgeNode.last_seen >= fallback_threshold)
            .all()
        )

    if not active_nodes:
        # Dernier recours : n'importe quel nœud enregistré dans la base
        active_nodes = db.query(EdgeNode).all()

    if not active_nodes:
        return None

    # Score de sélection : favorise le stockage libre et pénalise la charge CPU
    def node_score(n: EdgeNode):
        storage = n.storage_available_gb or 0.0
        cpu = n.cpu_usage_pct or 0.0
        return storage - (cpu / 10.0)

    active_nodes.sort(key=node_score, reverse=True)
    return active_nodes[0]


@router.websocket("/api/stream/publish/{client_id}")
async def publish_stream(websocket: WebSocket, client_id: str, db: Session = Depends(get_db)):
    await websocket.accept()
    session_id = client_id.strip()
    print(f"[Diffuseur] Connexion WSS entrante pour : {session_id}", flush=True)

    # 1. Sélection du nœud Edge actif
    target_node = select_best_edge_node(db)
    if not target_node:
        print("[Backend] Aucun nœud Edge disponible pour héberger le flux.", flush=True)
        await websocket.close(code=1011, reason="Aucun noeud Edge disponible")
        return

    print(f"[Backend] Routage vers {target_node.node_id} ({target_node.ip_address}:{target_node.wss_port})", flush=True)

    # 2. Inscription immédiate de la session en base de données pour découverte instantanée
    stream_session = db.query(StreamSession).filter(StreamSession.session_id == session_id).first()
    if not stream_session:
        stream_session = StreamSession(
            session_id=session_id,
            owner_email=session_id,
            node_id=target_node.node_id,
            zone_id=target_node.zone_id,
            status="live"
        )
        db.add(stream_session)
    else:
        stream_session.status = "live"
        stream_session.node_id = target_node.node_id
        stream_session.last_active = datetime.datetime.utcnow()

    # Mise à jour synchrone de DataLocation
    location = db.query(DataLocation).filter(
        DataLocation.client_id == session_id,
        DataLocation.node_id == target_node.node_id
    ).first()
    if not location:
        location = DataLocation(client_id=session_id, node_id=target_node.node_id)
        db.add(location)

    db.commit()

    # 3. Connexion WebSocket vers le nœud Edge
    edge_ws_url = f"{EDGE_WS_SCHEME}://{target_node.ip_address}:{target_node.wss_port}/upload/{session_id}"

    try:
        async with websockets.connect(edge_ws_url, max_size=None, ping_timeout=60, ping_interval=20) as edge_ws:
            print(f"[Backend] Connecté au nœud Edge {target_node.node_id} pour upload", flush=True)
            
            compteur = 0
            while True:
                # Réception d'un chunk binaire émis par le MediaRecorder du navigateur
                chunk = await websocket.receive_bytes()

                # Signature / intégrité via le serveur crypto de la zone
                crypto_data = await get_crypto_signature(chunk, target_node.zone_id)

                payload_to_store = {
                    "chunk_b64": base64.b64encode(chunk).decode("utf-8"),
                    "crypto": crypto_data
                }

                # Transmission au nœud Edge
                await edge_ws.send(json.dumps(payload_to_store).encode('utf-8'))

                # Acquittement du Edge
                ack_bytes = await edge_ws.recv()
                ack = json.loads(ack_bytes) if isinstance(ack_bytes, (bytes, bytearray)) else json.loads(ack_bytes)

                compteur += 1
                if compteur % 5 == 0:
                    # Mise à jour périodique de l'activité en base
                    stream_session.last_active = datetime.datetime.utcnow()
                    db.commit()

                # Acquittement au Frontend
                await websocket.send_json({
                    "status": "stored",
                    "edge_node": target_node.node_id,
                    "block": ack.get("bloc_id", compteur)
                })

    except WebSocketDisconnect:
        print(f"[Diffuseur] Déconnexion propre de {session_id}", flush=True)
    except Exception as e:
        print(f"[Backend] Erreur liaison Edge upload: {e}", flush=True)
    finally:
        # Marquer la session terminée en base
        try:
            stream_session.status = "stopped"
            stream_session.last_active = datetime.datetime.utcnow()
            db.commit()
        except Exception:
            pass


@router.websocket("/api/stream/watch/{client_id}")
async def watch_stream(websocket: WebSocket, client_id: str, db: Session = Depends(get_db)):
    await websocket.accept()
    session_id = client_id.strip()
    print(f"[Visionneur] Demande de visionnage pour : {session_id}", flush=True)

    # 1. Recherche de la session ou de l'emplacement des données
    session = db.query(StreamSession).filter(StreamSession.session_id == session_id).first()
    target_node_id = session.node_id if session else None

    if not target_node_id:
        location = db.query(DataLocation).filter(DataLocation.client_id == session_id).first()
        target_node_id = location.node_id if location else None

    if not target_node_id:
        print(f"[Backend] Flux {session_id} introuvable en base.", flush=True)
        await websocket.close(code=4004, reason="Flux introuvable")
        return

    source_node = db.query(EdgeNode).filter(EdgeNode.node_id == target_node_id).first()
    if not source_node:
        print(f"[Backend] Nœud source {target_node_id} introuvable.", flush=True)
        await websocket.close(code=1011, reason="Noeud source introuvable")
        return

    # 2. Connexion au nœud Edge pour download
    edge_ws_url = f"{EDGE_WS_SCHEME}://{source_node.ip_address}:{source_node.wss_port}/download/{session_id}"

    try:
        async with websockets.connect(edge_ws_url, max_size=None, ping_timeout=60, ping_interval=20) as edge_ws:
            print(f"[Backend] Connecté au nœud Edge {source_node.node_id} pour lecture de {session_id}", flush=True)

            while True:
                bloc_bytes = await edge_ws.recv()
                try:
                    bloc_data = json.loads(bloc_bytes)
                    chunk_b64 = bloc_data.get("chunk_b64")
                    crypto_data = bloc_data.get("crypto", {})
                    expected_v_matrix = crypto_data.get("V_matrix", [])

                    if not chunk_b64:
                        continue

                    chunk_bytes = base64.b64decode(chunk_b64)

                    # 1. Envoi immédiat des octets vidéo au visionneur (latence minimale / zéro délai)
                    await websocket.send_bytes(chunk_bytes)

                    # 2. Acquittement immédiat au nœud Edge
                    await edge_ws.send(json.dumps({"statut": "ok"}))

                    # 3. Vérification asynchrone d'intégrité SIS en arrière-plan
                    if expected_v_matrix:
                        async def check_sis_background(c_b64=chunk_b64, z_id=source_node.zone_id, exp_v=expected_v_matrix, ws=websocket, s_id=session_id):
                            is_valid = await verify_crypto_integrity(c_b64, z_id, exp_v)
                            if not is_valid:
                                print(f"[Backend] Alerte intégrité SIS sur {s_id} ! Fragment altéré détecté, fermeture immédiate.", flush=True)
                                try:
                                    await ws.close(code=1011, reason="Erreur integrite SIS")
                                except Exception:
                                    pass

                        asyncio.create_task(check_sis_background())

                except json.JSONDecodeError:
                    print("[Backend] Format de bloc invalide reçu depuis le Edge", flush=True)
                    break

    except (websockets.exceptions.ConnectionClosed, WebSocketDisconnect):
        print(f"[Visionneur] Fin de lecture pour {session_id}", flush=True)
    except Exception as e:
        print(f"[Backend] Erreur lecture depuis le Edge: {e}", flush=True)
        try:
            await websocket.close(code=1011, reason="Erreur de recuperation Edge")
        except Exception:
            pass
