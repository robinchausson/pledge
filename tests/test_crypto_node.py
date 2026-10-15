"""
Tests unitaires et d'intégration pour les endpoints FastAPI de crypto_node.py (Phase 3).
"""

import base64
import pytest
from fastapi.testclient import TestClient
from crypto_node import app, state

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_node_state():
    """Garantit l'initialisation du nœud pour les tests."""
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_parameters():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["phase"] == "Phase 3 (Delegation & Transcoding)"

    param_resp = client.get("/api/v1/crypto/parameters")
    assert param_resp.status_code == 200
    params = param_resp.json()
    assert params["sis"]["l"] == 64
    assert params["sis"]["m"] == 1536
    assert params["sis"]["q"] == 4099
    assert params["signature"]["delegation_supported"] is True


def test_identification_endpoint():
    resp = client.post("/api/v1/auth/identify", json={"identity": "alice@pledge.local"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["identity"] == "alice@pledge.local"
    assert len(data["secret_share"]) == 2
    assert len(data["zone_pk"]) == 2


def test_integrity_and_signature_endpoints():
    chunk = b"Trame de test Pledge 1080p"
    chunk_b64 = base64.b64encode(chunk).decode("utf-8")

    # 1. Calcul SIS
    integ_resp = client.post("/api/v1/crypto/integrity", json={"fragment_data": chunk_b64, "paramA": 42})
    assert integ_resp.status_code == 200
    integ_data = integ_resp.json()
    assert len(integ_data["V_matrix"]) == 64

    # 2. Signature standard
    sig_resp = client.post("/api/v1/crypto/signature", json={
        "fragment_data": chunk_b64,
        "owner_identity": "alice@pledge.local",
    })
    assert sig_resp.status_code == 200
    sig_data = sig_resp.json()
    assert "signature" in sig_data

    # 3. Vérification standard
    verif_resp = client.post("/api/v1/crypto/verify", json={
        "V_matrix": sig_data["V_matrix"],
        "signature": sig_data["signature"],
        "owner_identity": "alice@pledge.local",
    })
    assert verif_resp.status_code == 200
    assert verif_resp.json()["valid"] is True


def test_delegation_and_transcoding_endpoints():
    """Test complet du protocole de délégation de signature Fog (Phase 3)."""
    owner_id = "streamer@pledge.local"
    edge_node_id = "Edge_Node_01"

    # 1. Émission de la clé de délégation pour le nœud Edge
    del_resp = client.post("/api/v1/crypto/delegation/issue", json={
        "owner_identity": owner_id,
        "delegate_identity": edge_node_id,
    })
    assert del_resp.status_code == 200
    del_data = del_resp.json()
    assert del_data["delegate_identity"] == edge_node_id
    assert "delegation_key" in del_data
    assert "delegate_pk" in del_data

    # 2. Le nœud Edge transcode le flux et le signe avec DK_d
    transcoded_chunk = b"Trame 720p transcodee par Edge_Node_01"
    transcoded_b64 = base64.b64encode(transcoded_chunk).decode("utf-8")

    sign_del_resp = client.post("/api/v1/crypto/signature/delegate", json={
        "fragment_data": transcoded_b64,
        "delegation_key": del_data["delegation_key"],
    })
    assert sign_del_resp.status_code == 200
    signed_del_data = sign_del_resp.json()
    assert signed_del_data["signature"]["scheme"] == "Hess-IBS-Delegated"

    # 3. Vérification par le spectateur via 3 accouplements
    verif_del_resp = client.post("/api/v1/crypto/verify/delegate", json={
        "V_matrix": signed_del_data["V_matrix"],
        "signature": signed_del_data["signature"],
        "owner_identity": owner_id,
        "delegate_identity": edge_node_id,
        "delegate_pk": del_data["delegate_pk"],
    })
    assert verif_del_resp.status_code == 200
    assert verif_del_resp.json()["valid"] is True

    # 4. Échec si usurpation du nœud délégué (faux identifiant)
    fake_verif_resp = client.post("/api/v1/crypto/verify/delegate", json={
        "V_matrix": signed_del_data["V_matrix"],
        "signature": signed_del_data["signature"],
        "owner_identity": owner_id,
        "delegate_identity": "Rogue_Edge_Node_99",
        "delegate_pk": del_data["delegate_pk"],
    })
    assert fake_verif_resp.status_code == 200
    assert fake_verif_resp.json()["valid"] is False

    # 5. Échec si altération du contenu transcodé
    altered_v = list(signed_del_data["V_matrix"])
    altered_v[0] = (altered_v[0] + 1) % 4099
    altered_resp = client.post("/api/v1/crypto/verify/delegate", json={
        "V_matrix": altered_v,
        "signature": signed_del_data["signature"],
        "owner_identity": owner_id,
        "delegate_identity": edge_node_id,
        "delegate_pk": del_data["delegate_pk"],
    })
    assert altered_resp.status_code == 200
    assert altered_resp.json()["valid"] is False


def test_dynamic_operations_api():
    """Vérification des opérations dynamiques Section 3.5 (Insertion, Modification, Suppression)."""
    from crypto_engine import compute_sis_vector

    b0 = compute_sis_vector(b"block 0")
    b1 = compute_sis_vector(b"block 1")
    b2 = compute_sis_vector(b"block 2")

    # Initial V matrix: 64 x 3
    v_matrix = [[b0[r], b1[r], b2[r]] for r in range(64)]

    # 1. Insertion avec re-signature automatique
    new_block = base64.b64encode(b"inserted block").decode()
    res_ins = client.post(
        "/api/v1/crypto/dynamic/insert",
        json={
            "v_matrix": v_matrix,
            "block_index": 1,
            "block_data_b64": new_block,
            "owner_identity": "alice@pledge.local",
        },
    )
    assert res_ins.status_code == 200
    ins_data = res_ins.json()
    assert ins_data["status"] == "success"
    assert ins_data["total_blocks"] == 4
    assert ins_data["signature"] is not None
    assert "w1" in ins_data["signature"]
    v_after_ins = ins_data["v_matrix"]
    assert len(v_after_ins) == 64
    assert len(v_after_ins[0]) == 4

    # 2. Modification
    mod_block = base64.b64encode(b"modified block").decode()
    res_mod = client.post(
        "/api/v1/crypto/dynamic/modify",
        json={
            "v_matrix": v_after_ins,
            "block_index": 2,
            "block_data_b64": mod_block,
        },
    )
    assert res_mod.status_code == 200
    mod_data = res_mod.json()
    assert mod_data["status"] == "success"
    assert mod_data["total_blocks"] == 4
    v_after_mod = mod_data["v_matrix"]

    # 3. Suppression
    res_del = client.post(
        "/api/v1/crypto/dynamic/delete",
        json={
            "v_matrix": v_after_mod,
            "block_index": 1,
        },
    )
    assert res_del.status_code == 200
    del_data = res_del.json()
    assert del_data["status"] == "success"
    assert del_data["total_blocks"] == 3
    assert len(del_data["v_matrix"][0]) == 3

    # 4. Erreur si index hors limites
    res_err = client.post(
        "/api/v1/crypto/dynamic/delete",
        json={
            "v_matrix": del_data["v_matrix"],
            "block_index": 99,
        },
    )
    assert res_err.status_code == 400

