"""
Test d'intégration et de validation pour la Phase 4 :
Décentralisation du Plan de Données (Suppression du Goulot Central FastAPI).
Conforme au plan d'implémentation et à l'article Sellami et al. (FGCS 2024).
"""

import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Ajout des chemins au sys.path
root_dir = Path(__file__).parent.resolve()
backend_dir = root_dir / "serveur_web" / "backend"
client_mqtt_dir = root_dir / "broker_et_client" / "client_mqtt"
sys.path.insert(0, str(backend_dir))
sys.path.insert(0, str(client_mqtt_dir))

# 1. Imports du backend
from main import app
from models import SessionLocal, EdgeNode, StreamSession, DataLocation
from stream_router import DEFAULT_PARAM_A

# 2. Imports du nœud Edge
import client_edge as edge_module


@pytest.fixture(scope="module")
def setup_test_environment():
    """Prépare un dossier de stockage temporaire et un nœud Edge pour les tests."""
    temp_dir = tempfile.mkdtemp(prefix="pledge_phase4_edge_")
    original_storage = edge_module.DOSSIER_RACINE_STOCKAGE
    edge_module.DOSSIER_RACINE_STOCKAGE = temp_dir

    # Enregistrement d'un Edge Node actif dans la base
    db = SessionLocal()
    node = db.query(EdgeNode).filter(EdgeNode.node_id == "Test_Edge_Node_01").first()
    if not node:
        node = EdgeNode(
            node_id="Test_Edge_Node_01",
            zone_id="Zone_1",
            ip_address="127.0.0.1",
            wss_port=8445,
            storage_available_gb=100.0,
            cpu_usage_pct=5.0,
        )
        db.add(node)
        db.commit()
    db.close()

    yield {
        "storage_dir": temp_dir,
        "node_id": "Test_Edge_Node_01",
        "port": 8445,
    }

    # Nettoyage
    shutil.rmtree(temp_dir, ignore_errors=True)
    edge_module.DOSSIER_RACINE_STOCKAGE = original_storage


def test_signaling_allocation(setup_test_environment):
    """
    Vérifie le Plan de Signalisation : Allocation d'un nœud Edge pour le diffuseur.
    Le backend FastAPI ne prend pas en charge la vidéo, il alloue l'URL directe du Fog.
    """
    client = TestClient(app)
    streamer_id = "streamer_phase4@pledge.local"

    response = client.post("/api/stream/allocate", json={"session_id": streamer_id, "zone_id": "Zone_1"})
    assert response.status_code == 200
    data = response.json()

    assert data["session_id"] == streamer_id
    assert "upload_url" in data
    assert f"/upload/{streamer_id}" in data["upload_url"]
    assert data["paramA"] == DEFAULT_PARAM_A
    assert data["edge_port"] in (8444, 8445)

    # Vérification en base de données
    db = SessionLocal()
    session_db = db.query(StreamSession).filter(StreamSession.session_id == streamer_id).first()
    assert session_db is not None
    assert session_db.status == "live"
    db.close()


def test_signaling_discovery(setup_test_environment):
    """
    Vérifie le Plan d'Annuaire : Découverte directe du nœud hébergeur par le spectateur.
    """
    client = TestClient(app)
    streamer_id = "streamer_phase4@pledge.local"

    response = client.get(f"/api/stream/discover/{streamer_id}?mode=live")
    assert response.status_code == 200
    data = response.json()

    assert data["session_id"] == streamer_id
    assert data["status"] == "live"
    assert "download_url" in data
    assert f"/download/{streamer_id}?mode=live" in data["download_url"]
    assert data["paramA"] == DEFAULT_PARAM_A


def test_signaling_heartbeat_and_stop(setup_test_environment):
    """Vérifie le heartbeat et l'arrêt propre de la session."""
    client = TestClient(app)
    streamer_id = "streamer_phase4@pledge.local"

    # Heartbeat
    hb_res = client.post(f"/api/stream/heartbeat/{streamer_id}")
    assert hb_res.status_code == 200
    assert hb_res.json()["status"] == "ok"

    # Stop
    stop_res = client.post(f"/api/stream/stop/{streamer_id}")
    assert stop_res.status_code == 200
    assert stop_res.json()["status"] == "stopped"

    # Vérification en base
    db = SessionLocal()
    session_db = db.query(StreamSession).filter(StreamSession.session_id == streamer_id).first()
    assert session_db.status == "stopped"
    db.close()


@pytest.mark.asyncio
async def test_direct_edge_p2p_data_plane(setup_test_environment):
    """
    Vérifie le Plan de Données Décentralisé direct :
    Streamer -> Edge Node (Upload direct paquet Phase 4)
    Edge Node -> Viewer (Download direct paquet Phase 4)
    AUCUN flux ne transite par le backend FastAPI central.
    """
    import websockets

    port = setup_test_environment["port"]
    storage_dir = setup_test_environment["storage_dir"]
    session_id = "p2p_direct_session_01"

    # Démarrage d'un serveur WebSocket Edge local écoutant sur 127.0.0.1:port
    server = await websockets.serve(edge_module.routeur_ws, "127.0.0.1", port)

    try:
        # 1. STREAMER : Connexion directe au Edge pour uploader des paquets Phase 4
        upload_url = f"ws://127.0.0.1:{port}/upload/{session_id}"
        async with websockets.connect(upload_url) as streamer_ws:
            # Bloc 0 : En-tête d'initialisation WebM
            init_payload = b"FAKE_WEBM_EBML_HEADER_DATA_BLOCK_0"
            packet_0 = {
                "block_index": 0,
                "payload_b64": base64.b64encode(init_payload).decode("utf-8"),
                "paramA": DEFAULT_PARAM_A,
                "V_column": [10, 20, 30],
                "signature": {"scheme": "Hess-IBS", "w1": "12345", "w2": [1, 2]},
                "signer_id": "alice@pledge.local",
                "is_delegated": False,
            }
            await streamer_ws.send(json.dumps(packet_0))
            ack_0 = json.loads(await streamer_ws.recv())
            assert ack_0["statut"] == "ok"
            assert ack_0["bloc_id"] == 0

            # Bloc 1 : Trame vidéo
            frame_payload = b"FAKE_VIDEO_KEYFRAME_DATA_BLOCK_1"
            packet_1 = {
                "block_index": 1,
                "payload_b64": base64.b64encode(frame_payload).decode("utf-8"),
                "paramA": DEFAULT_PARAM_A,
                "V_column": [40, 50, 60],
                "signature": {"scheme": "Hess-IBS", "w1": "67890", "w2": [3, 4]},
                "signer_id": "alice@pledge.local",
                "is_delegated": False,
            }
            await streamer_ws.send(json.dumps(packet_1))
            ack_1 = json.loads(await streamer_ws.recv())
            assert ack_1["statut"] == "ok"
            assert ack_1["bloc_id"] == 1

        # 2. SPECTATEUR : Connexion directe au Edge pour télécharger les blocs
        download_url = f"ws://127.0.0.1:{port}/download/{session_id}?mode=start"
        async with websockets.connect(download_url) as viewer_ws:
            # Réception du Bloc 0
            rec_0 = await viewer_ws.recv()
            data_0 = json.loads(rec_0)
            assert data_0["block_index"] == 0
            assert base64.b64decode(data_0["payload_b64"]) == init_payload
            assert data_0["signer_id"] == "alice@pledge.local"
            assert data_0["signature"]["scheme"] == "Hess-IBS"

            # Réception du Bloc 1
            rec_1 = await viewer_ws.recv()
            data_1 = json.loads(rec_1)
            assert data_1["block_index"] == 1
            assert base64.b64decode(data_1["payload_b64"]) == frame_payload
            assert data_1["V_column"] == [40, 50, 60]

        # 3. SPECTATEUR EN MODE RAW (Vérification de la compatibilité binaire)
        download_raw_url = f"ws://127.0.0.1:{port}/download/{session_id}?mode=start&format=raw"
        async with websockets.connect(download_raw_url) as viewer_raw_ws:
            raw_rec_0 = await viewer_raw_ws.recv()
            assert raw_rec_0 == init_payload

    finally:
        server.close()
        await server.wait_closed()
