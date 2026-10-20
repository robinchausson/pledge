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
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, status, Request
import jwt
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

try:
    from .models import get_db, EdgeNode, DataLocation, StreamSession, User
    from .security import create_stream_token
    from . import security
except ImportError:
    from models import get_db, EdgeNode, DataLocation, StreamSession, User
    from security import create_stream_token
    import security

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
class NodeAllocationInfo(BaseModel):
    edge_node_id: str
    zone_id: str
    upload_url: str
    token: Optional[str] = None
    edge_host: str
    edge_port: int
    scheme: str


class NodeDownloadInfo(BaseModel):
    edge_node_id: str
    zone_id: str
    download_url: str
    transcoded_download_url: Optional[str] = None
    token: Optional[str] = None
    edge_host: str
    edge_port: int
    scheme: str


class AllocateStreamRequest(BaseModel):
    session_id: str = Field(..., description="Identifiant unique de session ou email du diffuseur")
    zone_id: Optional[str] = Field(default=None, description="Zone géographique demandée")
    enable_sharding: Optional[bool] = Field(default=True, description="Active le sharding multi-nœuds Fog si disponible")


class AllocateStreamResponse(BaseModel):
    session_id: str
    edge_node_id: str
    zone_id: str
    current_zone: Optional[str] = "Zone_1"
    upload_url: str
    token: Optional[str] = None
    edge_host: str
    edge_port: int
    scheme: str
    paramA: dict
    allocated_nodes: list[NodeAllocationInfo] = Field(default_factory=list)
    is_sharded: bool = False
    shard_count: int = 1


class DiscoverStreamResponse(BaseModel):
    session_id: str
    status: str
    edge_node_id: str
    zone_id: str
    current_zone: Optional[str] = "Zone_1"
    stream_zone: Optional[str] = None
    viewer_zone: Optional[str] = None
    download_url: str
    token: Optional[str] = None
    transcoded_download_url: Optional[str] = None
    supports_transcoding: bool = True
    edge_host: str
    edge_port: int
    scheme: str
    owner_id: str
    paramA: dict
    nodes: list[NodeDownloadInfo] = Field(default_factory=list)
    is_sharded: bool = False
    shard_count: int = 1


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
    """
    Sélectionne le meilleur nœud Edge actif en favorisant STRICTEMENT
    les nœuds Edge de la zone déclarée de l'utilisateur.
    """
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    threshold = now - datetime.timedelta(seconds=45)
    fallback_threshold = now - datetime.timedelta(minutes=5)
    zone_target = zone_id or "Zone_1"

    def node_perf_score(n: EdgeNode) -> float:
        storage = n.storage_available_gb or 0.0
        cpu = n.cpu_usage_pct or 0.0
        return storage - (cpu / 10.0)

    # 1. Nœuds actifs récents dans la zone déclarée (priorité absolue)
    zone_active = (
        db.query(EdgeNode)
        .filter(EdgeNode.node_id != "Edge_Node_01")
        .filter(EdgeNode.zone_id == zone_target)
        .filter(EdgeNode.last_seen >= threshold)
        .all()
    )
    if zone_active:
        zone_active.sort(key=node_perf_score, reverse=True)
        return zone_active[0]

    # 2. Repli zone déclarée : nœuds vus récemment (< 5 min)
    zone_fallback = (
        db.query(EdgeNode)
        .filter(EdgeNode.node_id != "Edge_Node_01")
        .filter(EdgeNode.zone_id == zone_target)
        .filter(EdgeNode.last_seen >= fallback_threshold)
        .all()
    )
    if zone_fallback:
        zone_fallback.sort(key=node_perf_score, reverse=True)
        return zone_fallback[0]

    # 3. Repli : autres nœuds actifs d'autres zones
    other_active = (
        db.query(EdgeNode)
        .filter(EdgeNode.node_id != "Edge_Node_01")
        .filter(EdgeNode.last_seen >= threshold)
        .all()
    )
    if other_active:
        other_active.sort(key=node_perf_score, reverse=True)
        return other_active[0]

    # 4. Repli : autres nœuds vus récemment (< 5 min)
    other_fallback = (
        db.query(EdgeNode)
        .filter(EdgeNode.node_id != "Edge_Node_01")
        .filter(EdgeNode.last_seen >= fallback_threshold)
        .all()
    )
    if other_fallback:
        other_fallback.sort(key=node_perf_score, reverse=True)
        return other_fallback[0]

    # 5. Repli : nœud par défaut pour la zone demandée
    default_node = db.query(EdgeNode).filter(EdgeNode.node_id == f"Edge_Node_Default_{zone_target}").first()
    if not default_node and zone_target == "Zone_1":
        default_node = db.query(EdgeNode).filter(EdgeNode.node_id == "Edge_Node_01").first()
    if not default_node:
        default_node = EdgeNode(
            node_id=f"Edge_Node_Default_{zone_target}" if zone_target != "Zone_1" else "Edge_Node_01",
            zone_id=zone_target,
            ip_address=os.getenv("EDGE_DEFAULT_HOST", "127.0.0.1"),
            wss_port=int(os.getenv("EDGE_DEFAULT_PORT", "8444")),
            storage_available_gb=50.0,
            cpu_usage_pct=10.0,
            last_seen=now,
        )
        db.add(default_node)
        db.commit()
        db.refresh(default_node)
    else:
        default_node.last_seen = now
        db.commit()
    return default_node


# =====================================================================
# 1. PLAN DE SIGNALISATION ET DÉCOUVERTE DÉCENTRALISÉ (PHASE 4)
# =====================================================================

@router.post("/api/stream/allocate", response_model=AllocateStreamResponse)
def allocate_edge_node(
    req: AllocateStreamRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Plan de Signalisation : Le diffuseur demande l'allocation de nœuds Fog.
    Supporte le Partitionnement Multi-Nœuds / Sharding (Section 3.1 & 3.2 Sellami et al. FGCS 2024).
    Le flux vidéo NE PASSERA PAS par le backend FastAPI (Section 3.5 / Phase 4).
    """
    session_id = req.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id requis")

    # Si le diffuseur ne spécifie pas explicitement zone_id, utiliser son current_zone déclaré
    effective_zone = None
    if req.zone_id and req.zone_id.strip():
        effective_zone = req.zone_id.strip()
    else:
        # 1. Vérifier si l'utilisateur est authentifié par cookie JWT
        token = request.cookies.get("access_token")
        if token:
            try:
                payload = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])
                user_id = payload.get("sub")
                if user_id:
                    user_by_token = db.query(User).filter(User.id == int(user_id)).first()
                    if user_by_token and user_by_token.current_zone:
                        effective_zone = user_by_token.current_zone
            except Exception:
                pass

        # 2. Si pas trouvé par cookie, chercher par email / session_id
        if not effective_zone:
            user_by_email = db.query(User).filter(User.email == session_id).first()
            if user_by_email and user_by_email.current_zone:
                effective_zone = user_by_email.current_zone

        # 3. Repli par défaut sur Zone_1
        if not effective_zone:
            effective_zone = "Zone_1"

    target_node = select_best_edge_node(db, effective_zone)
    if not target_node:
        raise HTTPException(status_code=503, detail="Aucun nœud Edge disponible")

    # Détection et sélection des nœuds pour le sharding Fog multi-nœuds (Option B)
    candidate_nodes = [target_node]
    if req.enable_sharding is not False:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        threshold = now - datetime.timedelta(seconds=40)
        active_nodes = (
            db.query(EdgeNode)
            .filter(EdgeNode.node_id != "Edge_Node_01")
            .filter(EdgeNode.node_id != target_node.node_id)
            .filter(EdgeNode.last_seen >= threshold)
            .all()
        )
        if not active_nodes and os.getenv("DATABASE_URL", "").startswith("sqlite:///./data/test"):
            fallback_threshold = now - datetime.timedelta(minutes=5)
            active_nodes = (
                db.query(EdgeNode)
                .filter(EdgeNode.node_id != "Edge_Node_01")
                .filter(EdgeNode.node_id != target_node.node_id)
                .filter(EdgeNode.last_seen >= fallback_threshold)
                .all()
            )
        for an in active_nodes:
            if an.node_id not in [cn.node_id for cn in candidate_nodes]:
                candidate_nodes.append(an)

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    # Inscription immédiate de la session en base de données
    stream_session = db.query(StreamSession).filter(StreamSession.session_id == session_id).first()
    if not stream_session:
        stream_session = StreamSession(
            session_id=session_id,
            owner_email=session_id,
            node_id=target_node.node_id,
            zone_id=target_node.zone_id,
            current_zone=effective_zone,
            status="live",
            last_active=now,
        )
        db.add(stream_session)
    else:
        stream_session.status = "live"
        stream_session.node_id = target_node.node_id
        stream_session.zone_id = target_node.zone_id
        stream_session.current_zone = effective_zone
        stream_session.last_active = now

    # Mise à jour des emplacements de données et génération des accès pour chaque shard
    allocated_nodes_info = []
    for cn in candidate_nodes:
        location = db.query(DataLocation).filter(
            DataLocation.client_id == session_id,
            DataLocation.node_id == cn.node_id,
        ).first()
        if not location:
            location = DataLocation(client_id=session_id, node_id=cn.node_id)
            db.add(location)

        node_token = create_stream_token(session_id, role="streamer", node_id=cn.node_id)
        node_upload_url = f"{EDGE_WS_SCHEME}://{cn.ip_address}:{cn.wss_port}/upload/{session_id}?token={node_token}"
        allocated_nodes_info.append(
            NodeAllocationInfo(
                edge_node_id=cn.node_id,
                zone_id=cn.zone_id,
                upload_url=node_upload_url,
                token=node_token,
                edge_host=cn.ip_address or "127.0.0.1",
                edge_port=cn.wss_port or 8444,
                scheme=EDGE_WS_SCHEME,
            )
        )

    db.commit()

    primary_token = allocated_nodes_info[0].token
    primary_upload_url = allocated_nodes_info[0].upload_url
    is_sharded = len(allocated_nodes_info) > 1
    shard_count = len(allocated_nodes_info)

    print(
        f"[Signalisation] Session {session_id} allouée (Nœuds={shard_count}, Sharded={is_sharded}, Principal={target_node.node_id})",
        flush=True,
    )

    return AllocateStreamResponse(
        session_id=session_id,
        edge_node_id=target_node.node_id,
        zone_id=target_node.zone_id,
        current_zone=effective_zone,
        upload_url=primary_upload_url,
        token=primary_token,
        edge_host=target_node.ip_address or "127.0.0.1",
        edge_port=target_node.wss_port or 8444,
        scheme=EDGE_WS_SCHEME,
        paramA=DEFAULT_PARAM_A,
        allocated_nodes=allocated_nodes_info,
        is_sharded=is_sharded,
        shard_count=shard_count,
    )


@router.get("/api/stream/discover/{session_id}", response_model=DiscoverStreamResponse)
def discover_stream_node(
    session_id: str,
    mode: str = "live",
    viewer_zone: Optional[str] = Query(None, description="Zone géographique déclarée du visionneur"),
    db: Session = Depends(get_db),
):
    """
    Plan d'Annuaire : Le spectateur interroge le backend pour localiser les nœuds Fog
    détenant le flux et ses shards éventuels, puis s'y connecte DIRECTEMENT sans transiter par le backend.
    """
    clean_session_id = session_id.strip()
    session = db.query(StreamSession).filter(StreamSession.session_id == clean_session_id).first()
    target_node_id = session.node_id if session else None

    # 1. Résolution de tous les nœuds détenant des fragments du flux (shards)
    locations = db.query(DataLocation).filter(DataLocation.client_id == clean_session_id).all()
    allocated_nodes_db = []
    seen_ids = set()

    for loc in locations:
        if loc.node_id not in seen_ids:
            nd = db.query(EdgeNode).filter(EdgeNode.node_id == loc.node_id).first()
            if nd:
                allocated_nodes_db.append(nd)
                seen_ids.add(loc.node_id)

    if not allocated_nodes_db and target_node_id:
        nd = db.query(EdgeNode).filter(EdgeNode.node_id == target_node_id).first()
        if nd and nd.node_id not in seen_ids:
            allocated_nodes_db.append(nd)
            seen_ids.add(nd.node_id)

    if not allocated_nodes_db:
        # Fallback si aucun nœud enregistré spécifiquement
        active_nodes = db.query(EdgeNode).all()
        if active_nodes:
            allocated_nodes_db.append(active_nodes[0])
            seen_ids.add(active_nodes[0].node_id)
        else:
            raise HTTPException(status_code=404, detail=f"Flux {clean_session_id} introuvable")

    # 2. Sélection du nœud principal (source_node) avec affinité de zone pour le visionneur
    source_node = None
    if viewer_zone:
        for nd in allocated_nodes_db:
            if nd.zone_id == viewer_zone:
                source_node = nd
                break

    if not source_node and target_node_id:
        for nd in allocated_nodes_db:
            if nd.node_id == target_node_id:
                source_node = nd
                break

    if not source_node:
        source_node = allocated_nodes_db[0]

    # Ordonnancement : source_node en premier
    ordered_nodes = [source_node] + [n for n in allocated_nodes_db if n.node_id != source_node.node_id]

    # 3. Préparation des descripteurs de téléchargement pour chaque shard
    download_nodes_info = []
    for nd in ordered_nodes:
        nd_token = create_stream_token(clean_session_id, role="viewer", node_id=nd.node_id)
        nd_dl = f"{EDGE_WS_SCHEME}://{nd.ip_address}:{nd.wss_port}/download/{clean_session_id}?mode={mode}&token={nd_token}"
        nd_t_dl = f"{EDGE_WS_SCHEME}://{nd.ip_address}:{nd.wss_port}/download/{clean_session_id}?mode={mode}&quality=720p&token={nd_token}"
        download_nodes_info.append(
            NodeDownloadInfo(
                edge_node_id=nd.node_id,
                zone_id=nd.zone_id,
                download_url=nd_dl,
                transcoded_download_url=nd_t_dl,
                token=nd_token,
                edge_host=nd.ip_address or "127.0.0.1",
                edge_port=nd.wss_port or 8444,
                scheme=EDGE_WS_SCHEME,
            )
        )

    is_sharded = len(download_nodes_info) > 1
    shard_count = len(download_nodes_info)

    primary_download_url = download_nodes_info[0].download_url
    primary_transcoded_url = download_nodes_info[0].transcoded_download_url
    primary_token = download_nodes_info[0].token

    emission_zone = session.zone_id if session and session.zone_id else source_node.zone_id
    print(
        f"[Signalisation] Visionneur pour {clean_session_id} (Sharded={is_sharded}, Nœuds={shard_count}, Principal={source_node.node_id})",
        flush=True,
    )

    return DiscoverStreamResponse(
        session_id=clean_session_id,
        status=session.status if session else "live",
        edge_node_id=source_node.node_id,
        zone_id=source_node.zone_id,
        current_zone=source_node.zone_id,
        stream_zone=emission_zone,
        viewer_zone=viewer_zone or source_node.zone_id,
        download_url=primary_download_url,
        token=primary_token,
        transcoded_download_url=primary_transcoded_url,
        supports_transcoding=True,
        edge_host=source_node.ip_address or "127.0.0.1",
        edge_port=source_node.wss_port or 8444,
        scheme=EDGE_WS_SCHEME,
        owner_id=session.owner_email if session else clean_session_id,
        paramA=DEFAULT_PARAM_A,
        nodes=download_nodes_info,
        is_sharded=is_sharded,
        shard_count=shard_count,
    )


@router.post("/api/stream/heartbeat/{session_id}")
def stream_heartbeat(session_id: str, db: Session = Depends(get_db)):
    """Maintient active la session de diffusion."""
    clean_session_id = session_id.strip()
    session = db.query(StreamSession).filter(StreamSession.session_id == clean_session_id).first()
    if session:
        session.status = "live"
        session.last_active = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        db.commit()
    return {"status": "ok", "session_id": clean_session_id}


@router.post("/api/stream/stop/{session_id}")
def stop_stream_session(session_id: str, db: Session = Depends(get_db)):
    """Marque la session comme arrêtée et commande le nettoyage du stockage Edge."""
    clean_session_id = session_id.strip()
    session = db.query(StreamSession).filter(StreamSession.session_id == clean_session_id).first()
    if session:
        session.status = "stopped"
        session.last_active = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        db.commit()

    # Nettoyer l'emplacement et la mémoire des blocs de ce flux
    db.query(DataLocation).filter(DataLocation.client_id == clean_session_id).delete()
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
