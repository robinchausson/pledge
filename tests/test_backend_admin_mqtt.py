"""
Tests unitaires et d'intégration pour les fonctionnalités d'administration,
le contrôle d'accès basé sur les rôles (RBAC) et l'ingestion de télémétrie MQTT.
"""

import datetime
import json
import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from main import app
from models import Base, engine, SessionLocal, User, EdgeNode, DataLocation, StreamSession
import security
from mqtt_client import make_on_message, publish_event


@pytest.fixture(scope="module")
def setup_admin_test_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    # Création d'un admin
    admin = db.query(User).filter(User.email == "test_superadmin@example.com").first()
    if not admin:
        admin = User(
            email="test_superadmin@example.com",
            hashed_password=security.get_password_hash("AdminPass123!"),
            role="admin",
        )
        db.add(admin)

    # Création d'un simple viewer
    viewer = db.query(User).filter(User.email == "test_simple_viewer@example.com").first()
    if not viewer:
        viewer = User(
            email="test_simple_viewer@example.com",
            hashed_password=security.get_password_hash("ViewerPass123!"),
            role="viewer",
        )
        db.add(viewer)

    # Création d'un nœud Fog
    node = db.query(EdgeNode).filter(EdgeNode.node_id == "Edge_Admin_Test_01").first()
    if not node:
        node = EdgeNode(
            node_id="Edge_Admin_Test_01",
            zone_id="Zone_1",
            ip_address="192.168.1.100",
            wss_port=8444,
            storage_available_gb=85.0,
            cpu_usage_pct=12.5,
            last_seen=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
        )
        db.add(node)

    db.commit()
    db.close()
    yield


@pytest.fixture
def client(setup_admin_test_db):
    with TestClient(app) as test_client:
        yield test_client


def test_admin_access_control(client):
    """Vérifie le contrôle d'accès strict sur les endpoints d'administration (/api/admin/*)."""
    # 1. Non authentifié -> 401
    res_unauth = client.get("/api/admin/nodes")
    assert res_unauth.status_code == 401

    # 2. Utilisateur standard (viewer) -> 403 Forbidden
    login_viewer = client.post(
        "/auth/login",
        json={"email": "test_simple_viewer@example.com", "password": "ViewerPass123!"},
    )
    assert login_viewer.status_code == 200

    res_forbidden = client.get("/api/admin/nodes")
    assert res_forbidden.status_code == 403
    assert "Accès refusé" in res_forbidden.json()["detail"]

    # Tentative d'accès aux conteneurs Docker
    res_containers_forbidden = client.get("/api/admin/containers")
    assert res_containers_forbidden.status_code == 403

    # 3. Déconnexion
    client.post("/auth/logout")

    # 4. Connexion Administrateur -> 200 OK
    login_admin = client.post(
        "/auth/login",
        json={"email": "test_superadmin@example.com", "password": "AdminPass123!"},
    )
    assert login_admin.status_code == 200

    res_admin_nodes = client.get("/api/admin/nodes")
    assert res_admin_nodes.status_code == 200
    data = res_admin_nodes.json()
    assert "zones" in data
    assert "Zone_1" in data["zones"]

    # Endpoint conteneurs (renvoie une liste ou un objet d'état sans crasher)
    res_admin_containers = client.get("/api/admin/containers")
    assert res_admin_containers.status_code == 200


def test_mqtt_telemetry_message_processing():
    """Vérifie l'ingestion des paquets MQTT de télémétrie des nœuds Fog."""
    on_message_func = make_on_message("Zone_1")

    # 1. Télémétrie Globale du Nœud
    global_msg = MagicMock()
    global_msg.topic = "telemetrie/Zone_1/Edge_Telemetry_Node_X"
    global_msg.payload = json.dumps({
        "ip_address": "10.0.0.99",
        "port_wss": 8444,
        "stockage_dispo_gb": 256.4,
        "cpu_usage_pct": 18.7,
        "timestamp": 123456789.0,
    }).encode("utf-8")

    on_message_func(client=None, userdata=None, msg=global_msg)

    # Vérification en base de données
    db = SessionLocal()
    node = db.query(EdgeNode).filter(EdgeNode.node_id == "Edge_Telemetry_Node_X").first()
    assert node is not None
    assert node.ip_address == "10.0.0.99"
    assert node.wss_port == 8444
    assert node.storage_available_gb == 256.4
    assert node.cpu_usage_pct == 18.7
    assert node.last_seen is not None

    # 2. Télémétrie Granulaire par Client
    client_msg = MagicMock()
    client_msg.topic = "telemetrie/Zone_1/Edge_Telemetry_Node_X/clients/session_user_mqtt_01"
    client_msg.payload = json.dumps({
        "client_id": "session_user_mqtt_01",
        "blocs_stockes": 42,
        "taille_ko": 5120.5,
        "active": True,
    }).encode("utf-8")

    on_message_func(client=None, userdata=None, msg=client_msg)

    loc = db.query(DataLocation).filter(
        DataLocation.node_id == "Edge_Telemetry_Node_X",
        DataLocation.client_id == "session_user_mqtt_01",
    ).first()
    assert loc is not None
    assert loc.blocks_count == 42
    assert loc.total_size_kb == 5120.5
    db.close()


import uuid

def test_stream_stop_triggers_mqtt_purge(client):
    """Vérifie que l'arrêt d'un flux commande l'événement de purge MQTT pour le stockage Edge."""
    session_id = f"stream_to_purge_{uuid.uuid4().hex[:8]}"

    # Inscription de la session
    db = SessionLocal()
    sess = StreamSession(
        session_id=session_id,
        owner_email="streamer@example.com",
        node_id="Edge_Admin_Test_01",
        zone_id="Zone_1",
        status="live",
        last_active=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
    )
    db.add(sess)
    db.commit()
    db.close()

    with patch("mqtt_client.publish_event") as mock_publish:
        res = client.post(f"/api/stream/stop/{session_id}")
        assert res.status_code == 200
        assert res.json()["status"] == "stopped"

        mock_publish.assert_called_once_with(
            "pledge/commands/purge_session",
            {"session_id": session_id},
        )

    # Vérifier que le statut en base est passé à stopped
    db = SessionLocal()
    updated = db.query(StreamSession).filter(StreamSession.session_id == session_id).first()
    assert updated.status == "stopped"
    db.close()


def test_active_streams_filtering(client):
    """Vérifie que /api/streams/active filtre les sessions inactives ou expirées."""
    db = SessionLocal()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    uid = uuid.uuid4().hex[:8]
    id_active = f"stream_fresh_active_{uid}"
    id_stale = f"stream_stale_inactive_{uid}"
    id_stopped = f"stream_already_stopped_{uid}"

    # Session A : active et récente (< 25s)
    sess_active = StreamSession(
        session_id=id_active,
        owner_email="alice@example.com",
        node_id="Edge_Admin_Test_01",
        zone_id="Zone_1",
        status="live",
        last_active=now,
    )
    # Session B : marquée live mais ancienne (> 60s)
    sess_stale = StreamSession(
        session_id=id_stale,
        owner_email="bob@example.com",
        node_id="Edge_Admin_Test_01",
        zone_id="Zone_1",
        status="live",
        last_active=now - datetime.timedelta(seconds=90),
    )
    # Session C : déjà arrêtée
    sess_stopped = StreamSession(
        session_id=id_stopped,
        owner_email="charlie@example.com",
        node_id="Edge_Admin_Test_01",
        zone_id="Zone_1",
        status="stopped",
        last_active=now,
    )
    db.add_all([sess_active, sess_stale, sess_stopped])
    db.commit()
    db.close()

    res = client.get("/api/streams/active")
    assert res.status_code == 200
    streams = res.json()["streams"]

    assert id_active in streams
    assert id_stale not in streams
    assert id_stopped not in streams
