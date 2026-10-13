"""
Tests pour l'API Backend Web (serveur_web/backend/main.py)
Couvrant l'authentification, la signalisation P2P Phase 4 et les zones Phase 2.
"""

import os
import pytest
from fastapi.testclient import TestClient

# Assure l'utilisation de la base de test sqlite locale
os.environ["DATABASE_URL"] = "sqlite:///./data/test_app.db"

from main import app
from models import Base, engine, EdgeNode, SessionLocal


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
