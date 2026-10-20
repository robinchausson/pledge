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
from mqtt_client import (
    make_on_message,
    make_on_connect,
    make_on_disconnect,
    publish_event,
    is_mqtt_connected,
    get_mqtt_health,
    _mqtt_clients,
    _mqtt_status,
    get_zone_broker_configs,
)


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


def test_multi_broker_mqtt_simultaneous_management_and_resilience():
    """
    Mission 1 : Test Multi-Broker MQTT Simulé
    - Valide que le backend gère simultanément deux brokers MQTT distincts (Zone 1 et Zone 2).
    - Vérifie la détection et la reprise sur panne si un seul broker tombe.
    """
    mock_client_z1 = MagicMock()
    mock_client_z1._client_id = b"Backend_Controller_Zone_1"
    mock_client_z2 = MagicMock()
    mock_client_z2._client_id = b"Backend_Controller_Zone_2"

    saved_clients = dict(_mqtt_clients)
    saved_status = dict(_mqtt_status)

    try:
        _mqtt_clients["Zone_1"] = mock_client_z1
        _mqtt_clients["Zone_2"] = mock_client_z2
        _mqtt_status["Zone_1"] = False
        _mqtt_status["Zone_2"] = False

        on_connect_z1 = make_on_connect("Zone_1")
        on_connect_z2 = make_on_connect("Zone_2")
        on_disconnect_z1 = make_on_disconnect("Zone_1")
        on_disconnect_z2 = make_on_disconnect("Zone_2")
        on_message_z1 = make_on_message("Zone_1")
        on_message_z2 = make_on_message("Zone_2")

        # 1. Connexion simultanée des deux brokers
        on_connect_z1(mock_client_z1, None, None, 0)
        on_connect_z2(mock_client_z2, None, None, 0)

        assert is_mqtt_connected("Zone_1") is True
        assert is_mqtt_connected("Zone_2") is True
        assert is_mqtt_connected() is True

        mock_client_z1.subscribe.assert_called_with("telemetrie/#", qos=1)
        mock_client_z2.subscribe.assert_called_with("telemetrie/#", qos=1)

        # 2. Ingestion simultanée de télémétrie des deux zones
        uid = uuid.uuid4().hex[:6]
        node_z1_id = f"Edge_Sim_Z1_{uid}"
        node_z2_id = f"Edge_Sim_Z2_{uid}"

        msg_z1 = MagicMock()
        msg_z1.topic = f"telemetrie/Zone_1/{node_z1_id}"
        msg_z1.payload = json.dumps({
            "ip_address": "192.168.1.50",
            "port_wss": 8444,
            "stockage_dispo_gb": 120.0,
            "cpu_usage_pct": 14.2,
        }).encode("utf-8")
        on_message_z1(mock_client_z1, None, msg_z1)

        msg_z2 = MagicMock()
        msg_z2.topic = f"telemetrie/Zone_2/{node_z2_id}"
        msg_z2.payload = json.dumps({
            "ip_address": "192.168.2.60",
            "port_wss": 8445,
            "stockage_dispo_gb": 64.0,
            "cpu_usage_pct": 32.5,
        }).encode("utf-8")
        on_message_z2(mock_client_z2, None, msg_z2)

        # Vérification en base des deux nœuds dans leurs zones respectives
        db = SessionLocal()
        n1 = db.query(EdgeNode).filter(EdgeNode.node_id == node_z1_id).first()
        n2 = db.query(EdgeNode).filter(EdgeNode.node_id == node_z2_id).first()
        assert n1 is not None and n1.zone_id == "Zone_1" and n1.ip_address == "192.168.1.50"
        assert n2 is not None and n2.zone_id == "Zone_2" and n2.ip_address == "192.168.2.60"
        db.close()

        # 3. Détection de panne : Déconnexion inattendue du Broker Zone 1
        on_disconnect_z1(mock_client_z1, None, 1)

        assert is_mqtt_connected("Zone_1") is False
        assert is_mqtt_connected("Zone_2") is True
        # Dégradation gracieuse : au moins un broker reste opérationnel
        assert is_mqtt_connected() is True

        # Télémétrie sur Zone 2 continue de fonctionner sans interruption
        msg_z2_update = MagicMock()
        msg_z2_update.topic = f"telemetrie/Zone_2/{node_z2_id}"
        msg_z2_update.payload = json.dumps({
            "ip_address": "192.168.2.60",
            "port_wss": 8445,
            "stockage_dispo_gb": 63.8,
            "cpu_usage_pct": 35.0,
        }).encode("utf-8")
        on_message_z2(mock_client_z2, None, msg_z2_update)

        db = SessionLocal()
        n2_updated = db.query(EdgeNode).filter(EdgeNode.node_id == node_z2_id).first()
        assert n2_updated.cpu_usage_pct == 35.0
        db.close()

        # 4. Reprise sur panne : Reconnexion du Broker Zone 1
        on_connect_z1(mock_client_z1, None, None, 0)
        assert is_mqtt_connected("Zone_1") is True
        assert is_mqtt_connected("Zone_2") is True

        # 5. Routage des publications ciblées et en diffusion (broadcast)
        publish_event("pledge/commands/purge", {"session_id": "test_s1"}, target_zone="Zone_1")
        mock_client_z1.publish.assert_called_with("pledge/commands/purge", json.dumps({"session_id": "test_s1"}), qos=1)
        mock_client_z2.publish.assert_not_called()

        mock_client_z1.reset_mock()
        mock_client_z2.reset_mock()

        publish_event("pledge/commands/purge", {"session_id": "test_s2"}, target_zone="Zone_2")
        mock_client_z2.publish.assert_called_with("pledge/commands/purge", json.dumps({"session_id": "test_s2"}), qos=1)
        mock_client_z1.publish.assert_not_called()

        mock_client_z1.reset_mock()
        mock_client_z2.reset_mock()

        # Diffusion globale sans target_zone
        publish_event("pledge/broadcast/sync", {"cmd": "sync_all"})
        mock_client_z1.publish.assert_called_with("pledge/broadcast/sync", json.dumps({"cmd": "sync_all"}), qos=1)
        mock_client_z2.publish.assert_called_with("pledge/broadcast/sync", json.dumps({"cmd": "sync_all"}), qos=1)

    finally:
        _mqtt_clients.clear()
        _mqtt_clients.update(saved_clients)
        _mqtt_status.clear()
        _mqtt_status.update(saved_status)
