"""
Tests pour l'API Backend Web (serveur_web/backend/main.py)
Couvrant l'authentification, la signalisation P2P Phase 4 et les zones Phase 2.
"""

import datetime
import os
import uuid
import pytest
from fastapi.testclient import TestClient

# Assure l'utilisation de la base de test sqlite locale
os.environ["DATABASE_URL"] = "sqlite:///./data/test_app.db"

from main import app
from models import Base, engine, EdgeNode, SessionLocal, User, StreamSession


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    # Enregistrer un EdgeNode pour les tests de signalisation
    node = db.query(EdgeNode).filter(EdgeNode.node_id == "Test_Edge_Node").first()
    if not node:
        node = EdgeNode(
            node_id="Test_Edge_Node",
            zone_id="Zone_1",
            ip_address="127.0.0.1",
            wss_port=8444,
            storage_available_gb=100.0,
            cpu_usage_pct=5.0,
        )
        db.add(node)
        db.commit()
    db.close()
    yield
    # Nettoyage si nécessaire


@pytest.fixture
def client(setup_db):
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert "status" in data
    assert "database" in data


def test_auth_and_roles(client):
    email = "streamer_test@example.com"
    # Register
    res_reg = client.post(
        "/auth/register",
        json={"email": email, "password": "Password123!", "role": "streamer"},
    )
    assert res_reg.status_code in (200, 400)  # 200 ou déjà créé

    # Login
    res_login = client.post(
        "/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    assert res_login.status_code == 200
    assert "user" in res_login.json()
    assert res_login.json()["user"]["email"] == email


def test_stream_allocation_signaling(client):
    """Vérifie que le diffuseur reçoit l'URL WebSocket directe du nœud Edge."""
    session_id = "stream_direct_alice"
    res = client.post(
        "/api/stream/allocate",
        json={"session_id": session_id, "zone_id": "Zone_1"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == session_id
    assert "upload_url" in data
    assert "/upload/stream_direct_alice" in data["upload_url"]
    assert "paramA" in data


def test_stream_discovery_signaling(client):
    """Vérifie que le spectateur localise le nœud Edge pour télécharger directement."""
    session_id = "stream_direct_alice"
    res = client.get(f"/api/stream/discover/{session_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == session_id
    assert "download_url" in data
    assert "/download/stream_direct_alice" in data["download_url"]


def test_register_with_zone_declaration(client):
    """Vérifie le cycle d'inscription d'un utilisateur avec déclaration explicite de zone (current_zone='Zone_2')."""
    uid = uuid.uuid4().hex[:8]
    email = f"streamer_z2_{uid}@example.com"
    password = "SecurePassword123!"

    res_reg = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "role": "streamer",
            "current_zone": "Zone_2",
        },
    )
    assert res_reg.status_code == 200
    reg_data = res_reg.json()
    assert reg_data["email"] == email
    assert reg_data["current_zone"] == "Zone_2"

    # Vérification de la persistance en base de données
    db = SessionLocal()
    db_user = db.query(User).filter(User.email == email).first()
    assert db_user is not None
    assert db_user.current_zone == "Zone_2"
    db.close()


def test_update_user_zone_endpoint_and_persistence(client):
    """Vérifie l'endpoint PUT /auth/zone, la bascule de zone et la persistance en base."""
    uid = uuid.uuid4().hex[:8]
    email = f"user_zone_switch_{uid}@example.com"
    password = "SwitchPassword123!"

    # 1. Inscription initiale en Zone_1 par défaut
    res_reg = client.post(
        "/auth/register",
        json={"email": email, "password": password, "role": "streamer"},
    )
    assert res_reg.status_code == 200
    assert res_reg.json()["current_zone"] == "Zone_1"

    # 2. Authentification pour obtenir le cookie JWT
    res_login = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert res_login.status_code == 200

    # 3. Bascule de zone vers Zone_2 via PUT /auth/zone
    res_zone_update = client.put(
        "/auth/zone",
        json={"zone_id": "Zone_2"},
    )
    assert res_zone_update.status_code == 200
    update_data = res_zone_update.json()
    assert update_data["current_zone"] == "Zone_2"
    assert update_data["user"]["current_zone"] == "Zone_2"

    # 4. Vérification via GET /auth/me
    res_me = client.get("/auth/me")
    assert res_me.status_code == 200
    assert res_me.json()["user"]["current_zone"] == "Zone_2"

    # 5. Vérification de la persistance directe en base de données
    db = SessionLocal()
    user_db = db.query(User).filter(User.email == email).first()
    assert user_db is not None
    assert user_db.current_zone == "Zone_2"
    db.close()

    # 6. Test d'alias (current_zone au lieu de zone_id) pour basculer vers Zone_1
    res_alias = client.put(
        "/auth/zone",
        json={"current_zone": "Zone_1"},
    )
    assert res_alias.status_code == 200
    assert res_alias.json()["current_zone"] == "Zone_1"

    # 7. Rejet des zones invalides
    res_invalid = client.put(
        "/auth/zone",
        json={"zone_id": "Zone_Inconnue"},
    )
    assert res_invalid.status_code == 422


def test_automatic_stream_allocation_on_declared_zone(client):
    """
    Vérifie l'allocation automatique de stream sur un nœud Edge
    appartenant à la zone déclarée par le diffuseur sans forcer req.zone_id.
    """
    db = SessionLocal()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # Création explicite d'un nœud actif en Zone 1 et d'un nœud actif en Zone 2
    node_z1 = db.query(EdgeNode).filter(EdgeNode.node_id == "Edge_Node_Auto_Z1").first()
    if not node_z1:
        node_z1 = EdgeNode(
            node_id="Edge_Node_Auto_Z1",
            zone_id="Zone_1",
            ip_address="192.168.1.10",
            wss_port=8444,
            storage_available_gb=100.0,
            cpu_usage_pct=5.0,
            last_seen=now,
        )
        db.add(node_z1)
    else:
        node_z1.last_seen = now

    node_z2 = db.query(EdgeNode).filter(EdgeNode.node_id == "Edge_Node_Auto_Z2").first()
    if not node_z2:
        node_z2 = EdgeNode(
            node_id="Edge_Node_Auto_Z2",
            zone_id="Zone_2",
            ip_address="192.168.2.20",
            wss_port=8445,
            storage_available_gb=100.0,
            cpu_usage_pct=5.0,
            last_seen=now,
        )
        db.add(node_z2)
    else:
        node_z2.last_seen = now

    # Création d'un diffuseur déclaré dans la Zone 2
    uid = uuid.uuid4().hex[:8]
    streamer_email = f"streamer_auto_z2_{uid}@example.com"
    streamer_user = User(
        email=streamer_email,
        hashed_password="HashedPassword123!",
        role="streamer",
        current_zone="Zone_2",
    )
    db.add(streamer_user)
    db.commit()
    db.close()

    # Allocation de flux sans renseigner req.zone_id (ou zone_id=None)
    alloc_res = client.post(
        "/api/stream/allocate",
        json={"session_id": streamer_email},
    )
    assert alloc_res.status_code == 200
    alloc_data = alloc_res.json()

    # Le backend doit allouer automatiquement sur le nœud de la zone déclarée (Zone_2)
    assert alloc_data["zone_id"] == "Zone_2"
    assert alloc_data["edge_node_id"] == "Edge_Node_Auto_Z2"
    assert "192.168.2.20:8445" in alloc_data["upload_url"]

    # Vérification en base de la session de stream créée
    db = SessionLocal()
    session_db = db.query(StreamSession).filter(StreamSession.session_id == streamer_email).first()
    assert session_db is not None
    assert session_db.zone_id == "Zone_2"
    assert session_db.current_zone == "Zone_2"
    assert session_db.node_id == "Edge_Node_Auto_Z2"
    db.close()


def test_select_best_edge_node_strict_zone_favoring():
    """
    Vérifie que select_best_edge_node favorise STRICTEMENT les nœuds Edge
    de la zone déclarée de l'utilisateur, même si une autre zone dispose d'un
    nœud avec de meilleures ressources matérielles (stockage/CPU).
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from stream_router import select_best_edge_node

    engine_mem = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine_mem)
    SessionMem = sessionmaker(bind=engine_mem)
    db = SessionMem()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # Nœud surpuissant en Zone 1 (2000 Go)
    node_z1 = EdgeNode(
        node_id="Edge_Node_Powerful_Z1",
        zone_id="Zone_1",
        ip_address="192.168.1.100",
        wss_port=8444,
        storage_available_gb=2000.0,
        cpu_usage_pct=1.0,
        last_seen=now,
    )
    # Nœud modeste en Zone 2 (20 Go)
    node_z2 = EdgeNode(
        node_id="Edge_Node_Modest_Z2",
        zone_id="Zone_2",
        ip_address="192.168.2.200",
        wss_port=8445,
        storage_available_gb=20.0,
        cpu_usage_pct=45.0,
        last_seen=now,
    )

    db.add(node_z1)
    db.add(node_z2)
    db.commit()

    # Demande pour la Zone 2 : doit STRICTEMENT choisir le nœud de Zone 2 malgré les specs inférieures
    best_z2 = select_best_edge_node(db, zone_id="Zone_2")
    assert best_z2.node_id == "Edge_Node_Modest_Z2"
    assert best_z2.zone_id == "Zone_2"

    # Demande pour la Zone 1 : doit choisir le nœud de Zone 1
    best_z1 = select_best_edge_node(db, zone_id="Zone_1")
    assert best_z1.node_id == "Edge_Node_Powerful_Z1"
    assert best_z1.zone_id == "Zone_1"

    db.close()


def test_schemas_user_zone_validation():
    """Vérifie la validation stricte des schémas Pydantic pour current_zone et UserZoneUpdate."""
    from schemas import UserCreate, UserOut, UserZoneUpdate, StreamSessionOut
    from pydantic import ValidationError

    # UserCreate : défaut Zone_1 et prise en charge Zone_2
    uc1 = UserCreate(email="u1@test.com", password="pwd")
    assert uc1.current_zone == "Zone_1"

    uc2 = UserCreate(email="u2@test.com", password="pwd", current_zone="Zone_2")
    assert uc2.current_zone == "Zone_2"

    # UserZoneUpdate : validation zone_id in ["Zone_1", "Zone_2"]
    uzu1 = UserZoneUpdate(zone_id="Zone_1")
    assert uzu1.zone_id == "Zone_1"

    uzu2 = UserZoneUpdate(zone_id="Zone_2")
    assert uzu2.zone_id == "Zone_2"

    with pytest.raises(ValidationError):
        UserZoneUpdate(zone_id="Zone_3")

    with pytest.raises(ValidationError):
        UserZoneUpdate(zone_id="Zone_Inconnue")

    # StreamSessionOut
    s_out = StreamSessionOut(session_id="s1", owner_email="o@test.com", current_zone="Zone_2")
    assert s_out.current_zone == "Zone_2"

