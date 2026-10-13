import base64
import datetime
import logging
import os

import httpx
import websockets
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

try:
    from .models import DataLocation, EdgeNode, get_db
except ImportError:
    from models import DataLocation, EdgeNode, get_db

router = APIRouter()
logger = logging.getLogger("stream")

CRYPTO_SERVERS = {
    "Zone_1": os.getenv("CRYPTO_URL_ZONE_1", "https://127.0.0.1:8443"),
    "Zone_2": os.getenv("CRYPTO_URL_ZONE_2", "https://127.0.0.1:8444"),
}
CRYPTO_INLINE = os.getenv("STREAM_CRYPTO_INLINE", "false").lower() == "true"
EDGE_WS_SCHEME = os.getenv("EDGE_WS_SCHEME", "ws").lower()
crypto_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global crypto_client
    if crypto_client is None:
        crypto_client = httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(10.0, connect=3.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return crypto_client


async def close_http_client() -> None:
    global crypto_client
    if crypto_client is not None:
        await crypto_client.aclose()
        crypto_client = None


async def get_crypto_signature(fragment_bytes: bytes, zone_id: str) -> dict:
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        return {}
    payload = {
        "fragment_data": base64.b64encode(fragment_bytes).decode("ascii"),
        "paramA": 42,
    }
    try:
        response = await get_http_client().post(
            f"{crypto_url}/api/v1/crypto/signature",
            json=payload,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        logger.warning("crypto_signature_failed zone=%s error=%s", zone_id, exc)
        return {}


@router.websocket("/api/stream/publish/{client_id}")
async def publish_stream(
    websocket: WebSocket,
    client_id: str,
    db: Session = Depends(get_db),
):
    await websocket.accept()
    target_node = (
        db.query(EdgeNode)
        .filter(
            EdgeNode.ip_address.isnot(None),
            EdgeNode.wss_port.isnot(None),
            EdgeNode.last_seen >= (
                datetime.datetime.utcnow() - datetime.timedelta(seconds=30)
            ),
        )
        .order_by(EdgeNode.storage_available_gb.desc())
        .first()
    )
    if not target_node:
        await websocket.close(code=1011, reason="Aucun noeud Edge disponible")
        return
    location = (
        db.query(DataLocation)
        .filter(DataLocation.client_id == client_id)
        .first()
    )
    if location is None:
        location = DataLocation(client_id=client_id, node_id=target_node.node_id)
        db.add(location)
    else:
        location.node_id = target_node.node_id
    db.commit()

    edge_ws_url = (
        f"{EDGE_WS_SCHEME}://{target_node.ip_address}:"
        f"{target_node.wss_port}/upload/{client_id}"
    )
    try:
        async with websockets.connect(
            edge_ws_url,
            max_size=4 * 1024 * 1024,
            compression=None,
            open_timeout=5,
        ) as edge_ws:
            while True:
                chunk = await websocket.receive_bytes()
                if CRYPTO_INLINE:
                    await get_crypto_signature(chunk, target_node.zone_id)
                # Keep the media path binary: no JSON or Base64 expansion.
                await edge_ws.send(chunk)
    except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
        logger.info("stream_publish_disconnected client=%s", client_id)
    except Exception as exc:
        logger.error("stream_publish_failed client=%s error=%s", client_id, exc)
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=1011, reason="Erreur de distribution vers le Edge")


async def verify_crypto_integrity(
    fragment_b64: str,
    zone_id: str,
    expected_v_matrix: list,
) -> bool:
    crypto_url = CRYPTO_SERVERS.get(zone_id)
    if not crypto_url:
        return False
    try:
        response = await get_http_client().post(
            f"{crypto_url}/api/v1/crypto/integrity",
            json={"fragment_data": fragment_b64, "paramA": 42},
        )
        response.raise_for_status()
        return response.json().get("V_matrix") == expected_v_matrix
    except httpx.HTTPError as exc:
        logger.warning("crypto_integrity_failed zone=%s error=%s", zone_id, exc)
        return False


@router.websocket("/api/stream/watch/{client_id}")
async def watch_stream(
    websocket: WebSocket,
    client_id: str,
    db: Session = Depends(get_db),
):
    await websocket.accept()
    location = (
        db.query(DataLocation)
        .filter(DataLocation.client_id == client_id)
        .first()
    )
    if not location:
        await websocket.close(code=4004, reason="Flux introuvable")
        return
    source_node = (
        db.query(EdgeNode)
        .filter(EdgeNode.node_id == location.node_id)
        .first()
    )
    if not source_node:
        await websocket.close(code=1011, reason="Noeud Edge source introuvable")
        return

    edge_ws_url = (
        f"{EDGE_WS_SCHEME}://{source_node.ip_address}:"
        f"{source_node.wss_port}/download/{client_id}"
    )
    try:
        async with websockets.connect(
            edge_ws_url,
            max_size=4 * 1024 * 1024,
            compression=None,
            open_timeout=5,
        ) as edge_ws:
            while True:
                packet = await edge_ws.recv()
                if isinstance(packet, bytes):
                    # The viewer receives the same binary media packet.
                    await websocket.send_bytes(packet)
                else:
                    logger.warning("unexpected_text_frame client=%s", client_id)
    except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
        logger.info("stream_watch_disconnected client=%s", client_id)
    except Exception as exc:
        logger.error("stream_watch_failed client=%s error=%s", client_id, exc)
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=1011, reason="Erreur de lecture depuis le Edge")
