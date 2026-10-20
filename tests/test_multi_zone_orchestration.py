"""
Tests d'Orchestration et de Validation Croisée Multi-Zone (AGENT 4 - QA & CI).
Couvre l'intégration système complète :
  1. Multi-Broker MQTT Simulé (Zone 1 & Zone 2 simultanés, détection de panne, reprise sur panne).
  2. Cycle de Déclaration de Zone (Inscription Zone 2, PUT /auth/zone, persistance en base, allocation automatique).
  3. Mobilité Multi-Zone avec Re-signature (Bascule Zone 1 -> Zone 2, accumulation homomorphe, audit spectateur sans TPA).
  4. Délégation Fog multi-zone et résistance aux attaques en itinérance.
"""

import base64
import datetime
import json
import os
import uuid
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

# Assure l'utilisation de la base SQLite de test
os.environ["DATABASE_URL"] = "sqlite:///./data/test_app.db"

import crypto_engine as engine
from mobility_manager import StreamerMobilityClient
from main import app
from models import Base, engine as db_engine, SessionLocal, User, EdgeNode, StreamSession, DataLocation
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
)


@pytest.fixture(scope="module")
def setup_multi_zone_environment():
    """Initialise la base de données et enregistre les nœuds Fog des deux zones."""
    Base.metadata.create_all(bind=db_engine)
    db = SessionLocal()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # Nœud Edge Zone 1 (PC Robin)
    node_z1 = db.query(EdgeNode).filter(EdgeNode.node_id == "Edge_Orch_Zone1").first()
    if not node_z1:
        node_z1 = EdgeNode(
            node_id="Edge_Orch_Zone1",
            zone_id="Zone_1",
            ip_address="192.168.1.101",
            wss_port=8444,
            storage_available_gb=150.0,
            cpu_usage_pct=10.0,
            last_seen=now,
        )
        db.add(node_z1)
    else:
        node_z1.last_seen = now

    # Nœud Edge Zone 2 (Nvidia Jetson)
    node_z2 = db.query(EdgeNode).filter(EdgeNode.node_id == "Edge_Orch_Zone2").first()
    if not node_z2:
        node_z2 = EdgeNode(
            node_id="Edge_Orch_Zone2",
            zone_id="Zone_2",
            ip_address="192.168.2.202",
            wss_port=8445,
            storage_available_gb=80.0,
            cpu_usage_pct=25.0,
            last_seen=now,
        )
        db.add(node_z2)
    else:
        node_z2.last_seen = now

    db.commit()
    db.close()
    yield


@pytest.fixture
def client(setup_multi_zone_environment):
    with TestClient(app) as test_client:
        yield test_client


# =============================================================================
# MISSION 1 : TEST MULTI-BROKER MQTT SIMULÉ (ZONE 1 & ZONE 2)
# =============================================================================

def test_multi_broker_mqtt_simultaneous_and_failover_recovery():
    """
    Vérifie la gestion simultanée des brokers MQTT de Zone 1 et Zone 2,
    la détection d'une coupure sur un seul broker et la reprise sur panne.
    """
    mock_z1 = MagicMock()
    mock_z1._client_id = b"Pledge_Backend_Broker_Zone_1"
    mock_z2 = MagicMock()
    mock_z2._client_id = b"Pledge_Backend_Broker_Zone_2"

    saved_clients = dict(_mqtt_clients)
    saved_status = dict(_mqtt_status)

    try:
        _mqtt_clients["Zone_1"] = mock_z1
        _mqtt_clients["Zone_2"] = mock_z2
        _mqtt_status["Zone_1"] = False
        _mqtt_status["Zone_2"] = False

        on_connect_z1 = make_on_connect("Zone_1")
        on_connect_z2 = make_on_connect("Zone_2")
        on_disconnect_z1 = make_on_disconnect("Zone_1")
        on_disconnect_z2 = make_on_disconnect("Zone_2")
        on_msg_z1 = make_on_message("Zone_1")
        on_msg_z2 = make_on_message("Zone_2")

        # 1. Établissement simultané des connexions vers les 2 brokers
        on_connect_z1(mock_z1, None, None, 0)
        on_connect_z2(mock_z2, None, None, 0)

        assert is_mqtt_connected("Zone_1") is True
        assert is_mqtt_connected("Zone_2") is True
        assert is_mqtt_connected() is True

        mock_z1.subscribe.assert_called_with("telemetrie/#", qos=1)
        mock_z2.subscribe.assert_called_with("telemetrie/#", qos=1)

        # 2. Ingestion concurrente de flux de télémétrie
        uid = uuid.uuid4().hex[:6]
        node1_id = f"Edge_MQTT_Orch_Z1_{uid}"
        node2_id = f"Edge_MQTT_Orch_Z2_{uid}"

        msg1 = MagicMock()
        msg1.topic = f"telemetrie/Zone_1/{node1_id}"
        msg1.payload = json.dumps({
            "ip_address": "192.168.1.111",
            "port_wss": 8444,
            "stockage_dispo_gb": 210.5,
            "cpu_usage_pct": 12.0,
        }).encode("utf-8")
        on_msg_z1(mock_z1, None, msg1)

        msg2 = MagicMock()
        msg2.topic = f"telemetrie/Zone_2/{node2_id}"
        msg2.payload = json.dumps({
            "ip_address": "192.168.2.222",
            "port_wss": 8445,
            "stockage_dispo_gb": 48.0,
            "cpu_usage_pct": 45.0,
        }).encode("utf-8")
        on_msg_z2(mock_z2, None, msg2)

        # Vérification en base de données
        db = SessionLocal()
        db_n1 = db.query(EdgeNode).filter(EdgeNode.node_id == node1_id).first()
        db_n2 = db.query(EdgeNode).filter(EdgeNode.node_id == node2_id).first()
        assert db_n1 is not None and db_n1.zone_id == "Zone_1" and db_n1.storage_available_gb == 210.5
        assert db_n2 is not None and db_n2.zone_id == "Zone_2" and db_n2.storage_available_gb == 48.0
        db.close()

        # 3. Détection de panne : Déconnexion abrupte du broker Zone 2
        on_disconnect_z2(mock_z2, None, 1)

        assert is_mqtt_connected("Zone_1") is True
        assert is_mqtt_connected("Zone_2") is False
        assert is_mqtt_connected() is True  # Le service global continue grâce à Zone 1

        # Zone 1 continue d'ingérer sans impact
        msg1_update = MagicMock()
        msg1_update.topic = f"telemetrie/Zone_1/{node1_id}"
        msg1_update.payload = json.dumps({
            "ip_address": "192.168.1.111",
            "port_wss": 8444,
            "stockage_dispo_gb": 210.0,
            "cpu_usage_pct": 15.5,
        }).encode("utf-8")
        on_msg_z1(mock_z1, None, msg1_update)

        db = SessionLocal()
        db_n1_up = db.query(EdgeNode).filter(EdgeNode.node_id == node1_id).first()
        assert db_n1_up.cpu_usage_pct == 15.5
        db.close()

        # 4. Reprise sur panne : Reconnexion réussie du broker Zone 2
        on_connect_z2(mock_z2, None, None, 0)
        assert is_mqtt_connected("Zone_1") is True
        assert is_mqtt_connected("Zone_2") is True

        # 5. Routage des publications ciblées
        publish_event("pledge/cmd/clean", {"zone": "Zone_2"}, target_zone="Zone_2")
        mock_z2.publish.assert_called_with("pledge/cmd/clean", json.dumps({"zone": "Zone_2"}), qos=1)
        mock_z1.publish.assert_not_called()

    finally:
        _mqtt_clients.clear()
        _mqtt_clients.update(saved_clients)
        _mqtt_status.clear()
        _mqtt_status.update(saved_status)


# =============================================================================
# MISSION 2 : TEST DU CYCLE DE DÉCLARATION DE ZONE ET ALLOCATION AUTOMATIQUE
# =============================================================================

def test_zone_declaration_cycle_and_automatic_stream_allocation(client):
    """
    Vérifie le cycle complet :
      1. Inscription avec current_zone="Zone_2".
      2. Bascule dynamique et persistance via PUT /auth/zone.
      3. Allocation automatique d'un stream sur un nœud Fog de la zone déclarée.
    """
    uid = uuid.uuid4().hex[:8]
    email = f"streamer_full_cycle_{uid}@example.com"
    pwd = "FullCyclePass456!"

    # 1. Inscription initiale en Zone 2
    res_reg = client.post(
        "/auth/register",
        json={"email": email, "password": pwd, "role": "streamer", "current_zone": "Zone_2"},
    )
    assert res_reg.status_code == 200
    assert res_reg.json()["current_zone"] == "Zone_2"

    # Vérification en base
    db = SessionLocal()
    user_db = db.query(User).filter(User.email == email).first()
    assert user_db is not None
    assert user_db.current_zone == "Zone_2"
    db.close()

    # Connexion
    res_login = client.post("/auth/login", json={"email": email, "password": pwd})
    assert res_login.status_code == 200

    # 2. Mise à jour vers Zone 1 puis retour en Zone 2 via PUT /auth/zone
    res_to_z1 = client.put("/auth/zone", json={"zone_id": "Zone_1"})
    assert res_to_z1.status_code == 200
    assert res_to_z1.json()["current_zone"] == "Zone_1"

    # Contrôle de persistance
    res_me = client.get("/auth/me")
    assert res_me.status_code == 200
    assert res_me.json()["user"]["current_zone"] == "Zone_1"

    # Retour définitif en Zone 2
    res_to_z2 = client.put("/auth/zone", json={"current_zone": "Zone_2"})
    assert res_to_z2.status_code == 200
    assert res_to_z2.json()["current_zone"] == "Zone_2"

    # 3. Allocation automatique de stream sans forcer req.zone_id
    res_alloc = client.post(
        "/api/stream/allocate",
        json={"session_id": email},
    )
    assert res_alloc.status_code == 200
    alloc_data = res_alloc.json()

    assert alloc_data["zone_id"] == "Zone_2"
    assert alloc_data["edge_node_id"] == "Edge_Orch_Zone2"
    assert "192.168.2.202:8445" in alloc_data["upload_url"]

    # Vérification en base de données de la session de diffusion
    db = SessionLocal()
    sess = db.query(StreamSession).filter(StreamSession.session_id == email).first()
    assert sess is not None
    assert sess.zone_id == "Zone_2"
    assert sess.current_zone == "Zone_2"
    assert sess.node_id == "Edge_Orch_Zone2"
    db.close()


# =============================================================================
# MISSION 3 : TEST DE MOBILITÉ MULTI-ZONE AVEC RE-SIGNATURE & AUDIT SPECTATEUR
# =============================================================================

def test_multi_zone_mobility_resigning_and_viewer_verification():
    """
    Valide le scénario complet de mobilité :
      1. Diffuseur actif en Zone 1 (signe bloc 0).
      2. Bascule en Zone 2 avec accumulation homomorphe (S_w, PK).
      3. Re-signature du bloc 0 et signature du bloc 1 avec les clés accumulées.
      4. Vérification publique par le spectateur sans TPA.
      5. Rejet des clés unilatérales et détection de corruption.
    """
    streamer_id = "nomad_orchestration_streamer@pledge.local"
    viewer_id = "viewer_independent_auditor@pledge.local"
    client = StreamerMobilityClient(identity=streamer_id)

    # 1. Émission en Zone 1
    msk_z1, pk_z1 = engine.generate_zone_master_key(41414141)
    share_z1 = engine.compute_secret_share(msk_z1, streamer_id)
    client.add_zone_credentials("Zone_1", share_z1, pk_z1)

    block_0_bytes = b"WEBM_INIT_CLUSTER_VIDEO_FRAME_ZONE1_DATA"
    pkg_0 = client.sign_block(block_0_bytes)

    # Le spectateur audite le flux original émis en Zone 1
    ok_z1, detail_z1 = StreamerMobilityClient.verify_block_package(pkg_0, data=block_0_bytes)
    assert ok_z1 is True
    assert "succès" in detail_z1.lower()

    # 2. Déplacement physique / réseau du diffuseur vers la Zone 2
    msk_z2, pk_z2 = engine.generate_zone_master_key(52525252)
    share_z2 = engine.compute_secret_share(msk_z2, streamer_id)
    client.add_zone_credentials("Zone_2", share_z2, pk_z2)

    assert len(client.visited_zones) == 2
    assert "Zone_1" in client.visited_zones and "Zone_2" in client.visited_zones

    accum_sk = engine.add(share_z1, share_z2)
    accum_pk = engine.add(pk_z1, pk_z2)
    assert client.current_secret_key == accum_sk
    assert client.current_public_key == accum_pk

    # 3. Émission d'un nouveau bloc 1 en Zone 2 + re-signature du bloc 0
    block_1_bytes = b"VP8_KEYFRAME_VIDEO_BLOCK_1_ROAMING_ZONE2"
    pkg_1 = client.sign_block(block_1_bytes)
    assert pkg_1["zones_scope"] == ["Zone_1", "Zone_2"]

    pkg_0_resigned = client.sign_block(block_0_bytes)
    assert pkg_0_resigned["zones_scope"] == ["Zone_1", "Zone_2"]

    # 4. Le spectateur vérifie les flux re-signés avec la clé publique accumulée
    ok_1, msg_1 = StreamerMobilityClient.verify_block_package(pkg_1, data=block_1_bytes)
    assert ok_1 is True
    assert "succès" in msg_1.lower()

    ok_0_resigned, msg_0 = StreamerMobilityClient.verify_block_package(pkg_0_resigned, data=block_0_bytes)
    assert ok_0_resigned is True
    assert "succès" in msg_0.lower()

    # 5. Tentative de vérification avec une seule zone : DOIT ÉCHOUER
    ok_pk1_only, _ = StreamerMobilityClient.verify_block_package(pkg_1, data=block_1_bytes, override_pk=pk_z1)
    assert ok_pk1_only is False

    ok_pk2_only, _ = StreamerMobilityClient.verify_block_package(pkg_1, data=block_1_bytes, override_pk=pk_z2)
    assert ok_pk2_only is False

    # 6. Détection de falsification par le spectateur
    tampered_bytes = b"VP8_KEYFRAME_VIDEO_BLOCK_1_ROAMING_ZONE2_TAMPERED"
    ok_tampered, reason_tampered = StreamerMobilityClient.verify_block_package(pkg_1, data=tampered_bytes)
    assert ok_tampered is False
    assert "SIS" in reason_tampered


def test_multi_zone_fog_transcoding_delegation():
    """
    Vérifie qu'un nœud Edge de Zone 2 peut recevoir une délégation valide
    basée sur la clé accumulée du diffuseur mobile, et que le transcodage 720p
    est vérifiable avec 3 accouplements par n'importe quel spectateur.
    """
    streamer_id = "streamer_delegated_nomad@pledge.local"
    edge_node_id = "Edge_Orch_Zone2"
    client = StreamerMobilityClient(identity=streamer_id)

    # Accumulation Zone 1 + Zone 2
    msk1, pk1 = engine.generate_zone_master_key(616161)
    msk2, pk2 = engine.generate_zone_master_key(727272)
    client.add_zone_credentials("Zone_1", engine.compute_secret_share(msk1, streamer_id), pk1)
    client.add_zone_credentials("Zone_2", engine.compute_secret_share(msk2, streamer_id), pk2)

    # Émission de la délégation
    delegation = client.issue_delegation(delegate_id=edge_node_id, random_y=998877)
    assert delegation["owner_id"] == streamer_id
    assert delegation["delegate_id"] == edge_node_id

    # Le nœud Edge Fog transcode et signe
    raw_frame = b"VP8_1080P_RAW_VIDEO_FRAME_CHUNKS"
    transcoded_frame = b"VP8_720P_TRANSCODED_FRAME_BY_EDGE_NODE_ZONE2"

    v_trans = engine.compute_sis_vector(transcoded_frame)
    sig_trans = engine.sign_delegated_data(v_trans, delegation["delegation_key"])

    # Vérification spectateur à 3 accouplements
    is_valid, reason = engine.verify_delegated_signature(
        v_data=v_trans,
        sig_dict=sig_trans,
        owner_identity=streamer_id,
        zone_pk=client.current_public_key,
        delegate_identity=edge_node_id,
        delegate_pk=delegation["delegate_pk"],
    )
    assert is_valid is True
    assert "succès" in reason.lower()

