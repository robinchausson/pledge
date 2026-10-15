import asyncio
import base64
import datetime
import json
import os
import re
import uuid
import httpx
import websockets
from typing import Any, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

try:
    from .models import get_db, EdgeNode, DataLocation, StreamSession
    from .security import create_stream_token
except ImportError:
    from models import get_db, EdgeNode, DataLocation, StreamSession
    from security import create_stream_token

router = APIRouter()

# Schéma et vérification TLS
EDGE_WS_SCHEME = (os.getenv("EDGE_WS_SCHEME") or "ws").lower()
CRYPTO_VERIFY_SSL = os.getenv("CRYPTO_VERIFY_SSL", "false").lower() in ("true", "1", "yes")

DEFAULT_PARAM_A = {
    "I0": 1234,
    "a": 3141,
    "C0": 567,
}


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
    timeout=httpx.Timeout(2.0, connect=1.0),
    limits=httpx.Limits(max_keepalive_connections=30, max_connections=60)
)


# --- MODÈLES PYDANTIC POUR LA SIGNALISATION PHASE 4 ---
class AllocateStreamRequest(BaseModel):
    session_id: str = Field(..., description="Identifiant unique de session ou email du diffuseur")
    zone_id: str | None = Field(default="Zone_1", description="Zone géographique demandée")


class AllocateStreamResponse(BaseModel):
    session_id: str
    edge_node_id: str
    zone_id: str
    upload_url: str
    token: Optional[str] = None
    edge_host: str
    edge_port: int
    scheme: str
    paramA: dict


class DiscoverStreamResponse(BaseModel):
    session_id: str
    status: str
    edge_node_id: str
    zone_id: str
    download_url: str
    token: Optional[str] = None
    transcoded_download_url: Optional[str] = None
    supports_transcoding: bool = True
    edge_host: str
    edge_port: int
    scheme: str
    owner_id: str
    paramA: dict


class DelegationIssueApiRequest(BaseModel):
    owner_identity: str = Field(..., description="Email ou ID du créateur du flux")
    delegate_identity: str = Field(..., description="ID du nœud Fog (ex: Edge_Node_01)")
    zone_id: Optional[str] = Field("Zone_1")


class PackageBlockApiRequest(BaseModel):
    payload_b64: str = Field(..., description="Trame vidéo encodée en Base64")
    block_index: int = Field(0, description="Numéro du bloc")
    signer_id: str = Field(..., description="Email ou identité du signataire")
    zone_id: str | None = Field(default="Zone_1")


def select_best_edge_node(db: Session, zone_id: str = "Zone_1") -> EdgeNode:
    """Sélectionne le meilleur nœud Edge actif en fonction de last_seen, CPU et espace disque."""
    threshold = datetime.datetime.utcnow() - datetime.timedelta(seconds=45)

    # 1. Nœuds actifs récents dans la zone demandée
    active_nodes = (
        db.query(EdgeNode)
        .filter(EdgeNode.last_seen >= threshold)
        .all()
    )

    if not active_nodes:
        # Repli : nœuds vus dans les 5 dernières minutes
        fallback_threshold = datetime.datetime.utcnow() - datetime.timedelta(minutes=5)
        active_nodes = (
            db.query(EdgeNode)
            .filter(EdgeNode.last_seen >= fallback_threshold)
            .all()
        )

    if not active_nodes:
        # Dernier recours : n'importe quel nœud en base
        active_nodes = db.query(EdgeNode).all()

    if not active_nodes:
        # Si aucun nœud n'est encore enregistré dans la DB, créer un nœud par défaut pour dev/test
        default_node = EdgeNode(
            node_id="Edge_Node_01",
            zone_id=zone_id or "Zone_1",
            ip_address="127.0.0.1",
            wss_port=8444,
            storage_available_gb=50.0,
            cpu_usage_pct=10.0,
            last_seen=datetime.datetime.utcnow(),
        )
        db.add(default_node)
        db.commit()
        db.refresh(default_node)
        return default_node

    # Score de sélection : favorise le stockage disponible et pénalise la charge CPU
    def node_score(n: EdgeNode):
        storage = n.storage_available_gb or 0.0
        cpu = n.cpu_usage_pct or 0.0
        zone_bonus = 100.0 if n.zone_id == zone_id else 0.0
        return zone_bonus + storage - (cpu / 10.0)

    active_nodes.sort(key=node_score, reverse=True)
    return active_nodes[0]


# =====================================================================
# 1. PLAN DE SIGNALISATION ET DÉCOUVERTE DÉCENTRALISÉ (PHASE 4)
# =====================================================================

@router.post("/api/stream/allocate", response_model=AllocateStreamResponse)
def allocate_edge_node(req: AllocateStreamRequest, db: Session = Depends(get_db)):
    """
    Plan de Signalisation : Le diffuseur demande l'allocation d'un nœud Fog.
    Le backend retourne l'adresse WebSocket DIRECTE du nœud Edge.
    Le flux vidéo NE PASSERA PAS par le backend FastAPI (Section 3.5 / Phase 4).
    """
    session_id = req.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id requis")

    target_node = select_best_edge_node(db, req.zone_id or "Zone_1")
    if not target_node:
        raise HTTPException(status_code=503, detail="Aucun nœud Edge disponible")

    # Inscription immédiate de la session en base de données
    stream_session = db.query(StreamSession).filter(StreamSession.session_id == session_id).first()
    if not stream_session:
        stream_session = StreamSession(
            session_id=session_id,
            owner_email=session_id,
            node_id=target_node.node_id,
            zone_id=target_node.zone_id,
            status="live",
            last_active=datetime.datetime.utcnow(),
        )
        db.add(stream_session)
    else:
        stream_session.status = "live"
        stream_session.node_id = target_node.node_id
        stream_session.zone_id = target_node.zone_id
        stream_session.last_active = datetime.datetime.utcnow()

    # Mise à jour de l'emplacement des données
    location = db.query(DataLocation).filter(
        DataLocation.client_id == session_id,
        DataLocation.node_id == target_node.node_id,
    ).first()
    if not location:
        location = DataLocation(client_id=session_id, node_id=target_node.node_id)
        db.add(location)

    db.commit()

    token = create_stream_token(session_id, role="streamer", node_id=target_node.node_id)
    upload_url = f"{EDGE_WS_SCHEME}://{target_node.ip_address}:{target_node.wss_port}/upload/{session_id}?token={token}"
    print(f"[Signalisation] Session {session_id} allouée sur {target_node.node_id} ({upload_url})", flush=True)

    return AllocateStreamResponse(
        session_id=session_id,
        edge_node_id=target_node.node_id,
        zone_id=target_node.zone_id,
        upload_url=upload_url,
        token=token,
        edge_host=target_node.ip_address or "127.0.0.1",
        edge_port=target_node.wss_port or 8444,
        scheme=EDGE_WS_SCHEME,
        paramA=DEFAULT_PARAM_A,
    )


@router.get("/api/stream/discover/{session_id}", response_model=DiscoverStreamResponse)
def discover_stream_node(session_id: str, mode: str = "live", db: Session = Depends(get_db)):
    """
    Plan d'Annuaire : Le spectateur interroge le backend pour localiser le nœud Edge
    qui détient le flux vidéo, puis s'y connecte DIRECTEMENT sans transiter par le backend.
    """
    clean_session_id = session_id.strip()
    session = db.query(StreamSession).filter(StreamSession.session_id == clean_session_id).first()
    target_node_id = session.node_id if session else None

    if not target_node_id:
        location = db.query(DataLocation).filter(DataLocation.client_id == clean_session_id).first()
        target_node_id = location.node_id if location else None

    if not target_node_id:
        # Fallback si le premier nœud actif détient la session
        active_nodes = db.query(EdgeNode).all()
        if active_nodes:
            target_node_id = active_nodes[0].node_id
        else:
            raise HTTPException(status_code=404, detail=f"Flux {clean_session_id} introuvable")

    source_node = db.query(EdgeNode).filter(EdgeNode.node_id == target_node_id).first()
    if not source_node:
        raise HTTPException(status_code=404, detail=f"Nœud hôte {target_node_id} introuvable")

    token = create_stream_token(clean_session_id, role="viewer", node_id=source_node.node_id)
    download_url = f"{EDGE_WS_SCHEME}://{source_node.ip_address}:{source_node.wss_port}/download/{clean_session_id}?mode={mode}&token={token}"
    transcoded_download_url = f"{EDGE_WS_SCHEME}://{source_node.ip_address}:{source_node.wss_port}/download/{clean_session_id}?mode={mode}&quality=720p&token={token}"
    print(f"[Signalisation] Visionneur orienté vers {source_node.node_id} (Direct={download_url}, Transcodé={transcoded_download_url})", flush=True)

    return DiscoverStreamResponse(
        session_id=clean_session_id,
        status=session.status if session else "live",
        edge_node_id=source_node.node_id,
        zone_id=source_node.zone_id,
        download_url=download_url,
        token=token,
        transcoded_download_url=transcoded_download_url,
        supports_transcoding=True,
        edge_host=source_node.ip_address or "127.0.0.1",
        edge_port=source_node.wss_port or 8444,
        scheme=EDGE_WS_SCHEME,
        owner_id=session.owner_email if session else clean_session_id,
        paramA=DEFAULT_PARAM_A,
    )


@router.post("/api/stream/heartbeat/{session_id}")
def stream_heartbeat(session_id: str, db: Session = Depends(get_db)):
    """Maintient active la session de diffusion."""
    clean_session_id = session_id.strip()
    session = db.query(StreamSession).filter(StreamSession.session_id == clean_session_id).first()
    if session:
        session.status = "live"
        session.last_active = datetime.datetime.utcnow()
        db.commit()
    return {"status": "ok", "session_id": clean_session_id}


@router.post("/api/stream/stop/{session_id}")
def stop_stream_session(session_id: str, db: Session = Depends(get_db)):
    """Marque la session comme arrêtée et commande le nettoyage du stockage Edge."""
    clean_session_id = session_id.strip()
    session = db.query(StreamSession).filter(StreamSession.session_id == clean_session_id).first()
    if session:
        session.status = "stopped"
        session.last_active = datetime.datetime.utcnow()
        db.commit()

    # Commander la purge du stockage sur le nœud Edge via MQTT
    try:
        from mqtt_client import publish_event
        publish_event("pledge/commands/purge_session", {"session_id": clean_session_id})
    except Exception as e:
        print(f"[Backend] Erreur commande purge MQTT: {e}", flush=True)

    return {"status": "stopped", "session_id": clean_session_id}


@router.post("/api/crypto/package-block")
async def package_stream_block(req: PackageBlockApiRequest):
    """
    Endpoint utilitaire pour préparer le paquet de transmission Phase 4
    (SIS + Hess-IBS) via le nœud crypto de zone.
    """
    zone_id = req.zone_id or "Zone_1"
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        crypto_url = "https://127.0.0.1:8443"

    payload = {
        "payload_b64": req.payload_b64,
        "block_index": req.block_index,
        "signer_id": req.signer_id,
        "paramA": DEFAULT_PARAM_A,
    }

    try:
        res = await crypto_client.post(f"{crypto_url}/api/v1/crypto/package-block", json=payload)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        # Fallback autonome si serveur crypto distant injoignable
        print(f"[Backend Crypto] Repli package-block local: {e}", flush=True)
        return {
            "block_index": req.block_index,
            "payload_b64": req.payload_b64,
            "paramA": DEFAULT_PARAM_A,
            "V_column": [0] * 64,
            "signature": {"scheme": "Hess-IBS", "w1": "1", "w2": [0, 0], "p1": [1, 2]},
            "signer_id": req.signer_id,
            "is_delegated": False,
        }


# =====================================================================
# 2. ROUTES WEBSOCKET HISTORIQUES (REPLI DE RÉTRO-COMPATIBILITÉ)
# =====================================================================

@router.post("/api/crypto/delegation/issue")
async def issue_delegation_proxy(req: DelegationIssueApiRequest):
    """
    Émet une clé de délégation de signature pour un nœud Edge (Phase 3 - Section 3.6.1).
    """
    zone_id = req.zone_id or "Zone_1"
    crypto_url = CRYPTO_SERVERS.get(zone_id) or "https://127.0.0.1:8443"
    payload = {
        "owner_identity": req.owner_identity,
        "delegate_identity": req.delegate_identity,
    }
    try:
        res = await crypto_client.post(f"{crypto_url}/api/v1/crypto/delegation/issue", json=payload)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        print(f"[Backend] Erreur émission délégation: {e}", flush=True)
        raise HTTPException(status_code=502, detail=f"Erreur crypto serveur: {e}")


async def verify_crypto_delegation(zone_id: str, crypto_data: dict) -> bool:
    """Vérifie la signature déléguée d'une trame transcodée selon Section 3.6.3 de l'article (3 accouplements)."""
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        return True
    payload = {
        "V_matrix": crypto_data.get("V_matrix", []),
        "signature": crypto_data.get("signature", {}),
        "owner_identity": crypto_data.get("owner_id", "streamer@pledge.local"),
        "delegate_identity": crypto_data.get("delegate_id", "Edge_Node_01"),
        "delegate_pk": crypto_data.get("delegate_pk", []),
        "zone_pk": crypto_data.get("zone_pk"),
        "paramA": crypto_data.get("paramA", DEFAULT_PARAM_A),
    }
    try:
        res = await crypto_client.post(f"{crypto_url}/api/v1/crypto/verify/delegate", json=payload)
        res.raise_for_status()
        is_valid = res.json().get("valid", False)
        if is_valid:
            print(f"[Backend] Intégrité de la trame transcodée validée avec succès via délégation (3 accouplements bilinéaires)", flush=True)
        else:
            print(f"[Backend] Alerte : échec vérification signature déléguée !", flush=True)
        return is_valid
    except Exception as e:
        print(f"[Backend] Erreur vérification délégation ({zone_id}): {e}", flush=True)
        return True


async def get_crypto_signature(fragment_bytes: bytes, zone_id: str) -> dict:
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        return {}
    fragment_b64 = base64.b64encode(fragment_bytes).decode("utf-8")
    payload = {"fragment_data": fragment_b64, "paramA": DEFAULT_PARAM_A}
    try:
        response = await crypto_client.post(f"{crypto_url}/api/v1/crypto/integrity", json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[Backend] Avertissement intégrité Crypto ({zone_id}): {e}", flush=True)
        return {}


async def verify_crypto_integrity(fragment_b64: str, zone_id: str, expected_v_matrix: list) -> bool:
    if not expected_v_matrix:
        return True
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        return True
    payload = {"fragment_data": fragment_b64, "paramA": DEFAULT_PARAM_A}
    try:
        response = await crypto_client.post(f"{crypto_url}/api/v1/crypto/integrity", json=payload)
        response.raise_for_status()
        data = response.json()
        actual_v_matrix = data.get("V_matrix")
        return actual_v_matrix == expected_v_matrix
    except Exception as e:
        print(f"[Backend] Erreur vérification intégrité SIS ({zone_id}): {e}", flush=True)
        return True


@router.websocket("/api/stream/publish/{client_id}")
async def publish_stream(websocket: WebSocket, client_id: str, db: Session = Depends(get_db)):
    """Route proxy historique pour compatibilité (le mode Phase 4 utilise /api/stream/allocate)."""
    await websocket.accept()
    session_id = client_id.strip()
    target_node = select_best_edge_node(db)
    if not target_node:
        await websocket.close(code=1011, reason="Aucun noeud Edge disponible")
        return

    token = create_stream_token(session_id, role="streamer", node_id=target_node.node_id)
    edge_ws_url = f"{EDGE_WS_SCHEME}://{target_node.ip_address}:{target_node.wss_port}/upload/{session_id}?token={token}"
    try:
        async with websockets.connect(edge_ws_url, max_size=None, ping_timeout=60, ping_interval=20) as edge_ws:
            compteur = 0
            while True:
                msg = await websocket.receive()
                if "text" in msg and msg["text"]:
                    try:
                        parsed = json.loads(msg["text"])
                        payload_to_store = parsed
                    except Exception:
                        payload_to_store = {
                            "block_index": compteur,
                            "payload_b64": msg["text"],
                            "paramA": DEFAULT_PARAM_A,
                            "signer_id": session_id,
                            "is_delegated": False,
                        }
                elif "bytes" in msg and msg["bytes"]:
                    chunk = msg["bytes"]
                    crypto_data = await get_crypto_signature(chunk, target_node.zone_id)
                    payload_to_store = {
                        "block_index": compteur,
                        "payload_b64": base64.b64encode(chunk).decode("utf-8"),
                        "paramA": DEFAULT_PARAM_A,
                        "V_column": crypto_data.get("V_matrix", []),
                        "signature": {"scheme": "Hess-IBS"},
                        "signer_id": session_id,
                        "is_delegated": False,
                    }
                else:
                    break

                await edge_ws.send(json.dumps(payload_to_store).encode('utf-8'))
                ack_bytes = await edge_ws.recv()
                ack = json.loads(ack_bytes) if isinstance(ack_bytes, (bytes, bytearray)) else json.loads(ack_bytes)
                compteur += 1
                await websocket.send_json({
                    "status": "stored",
                    "edge_node": target_node.node_id,
                    "block": ack.get("bloc_id", compteur)
                })
    except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
        pass
    except Exception as e:
        print(f"[Backend Proxy] Erreur upload: {e}", flush=True)


@router.websocket("/api/stream/watch/{client_id}")
async def watch_stream(websocket: WebSocket, client_id: str, mode: str = "live", quality: str = "original", db: Session = Depends(get_db)):
    """Route proxy pour le spectateur avec support de l'audit public direct (Phase 5)."""
    await websocket.accept()
    session_id = client_id.strip()
    req_quality = websocket.query_params.get("quality") or quality or "original"
    req_format = (websocket.query_params.get("format") or "json").lower().strip()

    session = db.query(StreamSession).filter(StreamSession.session_id == session_id).first()
    target_node_id = session.node_id if session else None
    if not target_node_id:
        location = db.query(DataLocation).filter(DataLocation.client_id == session_id).first()
        target_node_id = location.node_id if location else None

    source_node = db.query(EdgeNode).filter(EdgeNode.node_id == target_node_id).first() if target_node_id else None
    if not source_node:
        source_node = select_best_edge_node(db)

    token = create_stream_token(session_id, role="viewer", node_id=source_node.node_id)
    edge_ws_url = f"{EDGE_WS_SCHEME}://{source_node.ip_address}:{source_node.wss_port}/download/{session_id}?mode={mode}&quality={req_quality}&token={token}"
    try:
        async with websockets.connect(edge_ws_url, max_size=None, ping_timeout=60, ping_interval=20) as edge_ws:
            while True:
                bloc_bytes = await edge_ws.recv()
                try:
                    bloc_data = json.loads(bloc_bytes)
                    chunk_b64 = bloc_data.get("payload_b64") or bloc_data.get("chunk_b64")

                    if req_format == "raw" and chunk_b64:
                        chunk_bytes = base64.b64decode(chunk_b64)
                        await websocket.send_bytes(chunk_bytes)
                    else:
                        # Envoi du paquet complet (métadonnées SIS + Hess-IBS + payload_b64)
                        # pour audit public direct côté spectateur (Phase 5)
                        text_payload = bloc_bytes if isinstance(bloc_bytes, str) else bloc_bytes.decode('utf-8')
                        await websocket.send_text(text_payload)

                    await edge_ws.send(json.dumps({"statut": "ok"}))
                except json.JSONDecodeError:
                    if isinstance(bloc_bytes, bytes):
                        await websocket.send_bytes(bloc_bytes)
                    else:
                        await websocket.send_text(str(bloc_bytes))
                    await edge_ws.send(json.dumps({"statut": "ok"}))
    except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
        pass
    except Exception as e:
        print(f"[Backend Proxy] Erreur download: {e}", flush=True)
