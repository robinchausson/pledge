"""
Tests unitaires et d'intégration pour le Gestionnaire de Mobilité Multi-Zone
(serveur_crypto/mobility_manager.py) selon la Section 3.4 de l'article FGCS 2024.
"""

import pytest
from unittest.mock import MagicMock
import crypto_engine as engine
from mobility_manager import StreamerMobilityClient, ZoneInfo


def test_mobility_client_initial_state():
    """Vérifie l'état initial d'un diffuseur avant d'avoir rejoint une zone."""
    client = StreamerMobilityClient(identity="alice@pledge.local")
    assert client.identity == "alice@pledge.local"
    assert len(client.visited_zones) == 0
    assert client.current_secret_key is None
    assert client.current_public_key is None

    # Tenter de signer sans clé active doit lever une exception
    with pytest.raises(RuntimeError, match="ne possède aucune clé de zone active"):
        client.sign_block(b"video chunk")

    # Tenter d'émettre une délégation sans clé active doit lever une exception
    with pytest.raises(RuntimeError, match="ne possède aucune clé active"):
        client.issue_delegation("Edge_Node_01")

    # Tenter d'exporter les identifiants publics sans clé doit lever une exception
    with pytest.raises(RuntimeError, match="Aucune clé publique initialisée"):
        client.get_public_credentials()


def test_single_zone_lifecycle():
    """Cycle de vie complet pour un diffuseur dans une seule zone Fog (Zone 1)."""
    identity = "streamer_zone1@pledge.local"
    client = StreamerMobilityClient(identity=identity)

    # 1. Setup IS Zone 1
    msk_z1, pk_z1 = engine.generate_zone_master_key(1010101)
    share_z1 = engine.compute_secret_share(msk_z1, identity)

    # 2. Enregistrement des identifiants
    client.add_zone_credentials("Zone_1", share_z1, pk_z1)
    assert len(client.visited_zones) == 1
    assert "Zone_1" in client.visited_zones
    assert client.current_secret_key is not None
    assert client.current_public_key is not None

    # 3. Signature d'un bloc de données
    payload = b"WebM Video Chunk 001 from Zone 1"
    block_pkg = client.sign_block(payload)
    assert block_pkg["signer_id"] == identity
    assert block_pkg["zones_scope"] == ["Zone_1"]
    assert "V_matrix" in block_pkg
    assert "signature" in block_pkg

    # 4. Vérification publique sans TPA
    is_valid, detail = StreamerMobilityClient.verify_block_package(block_pkg, data=payload)
    assert is_valid is True
    assert "succès" in detail

    # 5. Détection d'altération du contenu
    tampered_payload = b"Tampered Video Chunk 001"
    is_valid_tampered, tampered_detail = StreamerMobilityClient.verify_block_package(
        block_pkg, data=tampered_payload
    )
    assert is_valid_tampered is False
    assert "SIS" in tampered_detail


def test_multi_zone_mobility_accumulation():
    """
    Section 3.4.2 & 3.4.3 :
    Le diffuseur traverse la Zone 1, puis la Zone 2, puis la Zone 3.
    Ses parts secrètes et les clés publiques s'accumulent de manière homomorphe.
    """
    identity = "nomad_streamer@pledge.local"
    client = StreamerMobilityClient(identity=identity)

    # Zone 1
    msk1, pk1 = engine.generate_zone_master_key(1111111)
    share1 = engine.compute_secret_share(msk1, identity)
    client.add_zone_credentials("Zone_1", share1, pk1)

    # Zone 2 (Mobilité)
    msk2, pk2 = engine.generate_zone_master_key(2222222)
    share2 = engine.compute_secret_share(msk2, identity)
    client.add_zone_credentials("Zone_2", share2, pk2)

    assert len(client.visited_zones) == 2
    assert "Zone_1" in client.visited_zones and "Zone_2" in client.visited_zones

    # Signature avec clé accumulée Zone 1 + Zone 2
    payload_z2 = b"Streamed in Zone 2 with accumulated keys"
    pkg_z2 = client.sign_block(payload_z2)
    assert pkg_z2["zones_scope"] == ["Zone_1", "Zone_2"]

    # Vérification avec clé publique accumulée
    is_valid, _ = StreamerMobilityClient.verify_block_package(pkg_z2, data=payload_z2)
    assert is_valid is True

    # Vérification avec une seule zone (Zone 1) doit échouer
    is_valid_partial, _ = StreamerMobilityClient.verify_block_package(
        pkg_z2, data=payload_z2, override_pk=pk1
    )
    assert is_valid_partial is False

    # Zone 3 (Deuxième saut de mobilité)
    msk3, pk3 = engine.generate_zone_master_key(3333333)
    share3 = engine.compute_secret_share(msk3, identity)
    client.add_zone_credentials("Zone_3", share3, pk3)
    assert len(client.visited_zones) == 3

    payload_z3 = b"Streamed in Zone 3 with 3 accumulated zones"
    pkg_z3 = client.sign_block(payload_z3)
    assert len(pkg_z3["zones_scope"]) == 3
    is_valid_z3, _ = StreamerMobilityClient.verify_block_package(pkg_z3, data=payload_z3)
    assert is_valid_z3 is True


def test_mobility_idempotency():
    """Vérifie que rejoindre une zone déjà visitée ne fausse pas l'accumulation homomorphe."""
    identity = "idempotent_user@pledge.local"
    client = StreamerMobilityClient(identity=identity)

    msk, pk = engine.generate_zone_master_key(5555555)
    share = engine.compute_secret_share(msk, identity)

    client.add_zone_credentials("Zone_1", share, pk)
    initial_pk = client.current_public_key
    initial_sk = client.current_secret_key

    # Deuxième ajout pour la même zone (ne doit rien modifier)
    client.add_zone_credentials("Zone_1", share, pk)
    assert len(client.visited_zones) == 1
    assert client.current_public_key == initial_pk
    assert client.current_secret_key == initial_sk


def test_zone_specific_signing():
    """Vérifie que le diffuseur peut signer uniquement pour une zone spécifique s'il le souhaite."""
    identity = "selective_streamer@pledge.local"
    client = StreamerMobilityClient(identity=identity)

    msk1, pk1 = engine.generate_zone_master_key(7771)
    msk2, pk2 = engine.generate_zone_master_key(7772)

    client.add_zone_credentials("Zone_1", engine.compute_secret_share(msk1, identity), pk1)
    client.add_zone_credentials("Zone_2", engine.compute_secret_share(msk2, identity), pk2)

    # Signature restreinte à la Zone 1 uniquement
    data = b"Zone 1 specific video payload"
    pkg = client.sign_block(data, use_zone_only="Zone_1")
    assert pkg["zones_scope"] == ["Zone_1"]

    # Doit être vérifiable avec la clé publique de la Zone 1
    is_valid_z1, _ = StreamerMobilityClient.verify_block_package(pkg, data=data, override_pk=pk1)
    assert is_valid_z1 is True

    # Ne doit pas être vérifiable avec la clé publique accumulée
    is_valid_accum, _ = StreamerMobilityClient.verify_block_package(
        pkg, data=data, override_pk=client.current_public_key
    )
    assert is_valid_accum is False

    # Zone inconnue
    with pytest.raises(ValueError, match="inconnue du client"):
        client.sign_block(data, use_zone_only="Zone_999")


def test_delegation_issuance_from_mobility_client():
    """Vérifie l'émission d'une clé de délégation à partir du client de mobilité."""
    identity = "delegating_streamer@pledge.local"
    client = StreamerMobilityClient(identity=identity)

    msk, pk = engine.generate_zone_master_key(88888)
    client.add_zone_credentials("Zone_1", engine.compute_secret_share(msk, identity), pk)

    edge_id = "Edge_Fog_Node_Transcoder"
    del_dict = client.issue_delegation(delegate_id=edge_id, random_y=12345)

    assert del_dict["owner_id"] == identity
    assert del_dict["delegate_id"] == edge_id
    assert "delegation_key" in del_dict
    assert "delegate_pk" in del_dict

    # Transcodage et re-signature par le nœud Edge
    transcoded_payload = b"Transcoded frame 720p"
    v_trans = engine.compute_sis_vector(transcoded_payload)
    sig_del = engine.sign_delegated_data(v_trans, del_dict["delegation_key"])

    # Vérification par un spectateur
    is_valid, _ = engine.verify_delegated_signature(
        v_data=v_trans,
        sig_dict=sig_del,
        owner_identity=identity,
        zone_pk=pk,
        delegate_identity=edge_id,
        delegate_pk=del_dict["delegate_pk"],
    )
    assert is_valid is True


def test_join_zone_via_api_mock():
    """Vérifie la découverte et la négociation d'identité via l'API REST IS_s."""
    identity = "api_streamer@pledge.local"
    client = StreamerMobilityClient(identity=identity)

    msk, pk = engine.generate_zone_master_key(99999)
    share = engine.compute_secret_share(msk, identity)

    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "identity": identity,
        "zone_id": "Zone_Paris_01",
        "secret_share": engine.g1_to_json(share),
        "zone_pk": engine.g2_to_json(pk),
    }
    mock_session.post.return_value = mock_response

    res = client.join_zone_via_api("https://is-paris.pledge.local:8001", session=mock_session)
    assert res["zone_id"] == "Zone_Paris_01"
    assert res["total_zones"] == 1
    assert "Zone_Paris_01" in client.visited_zones

    pub_creds = client.get_public_credentials()
    assert pub_creds["identity"] == identity
    assert "Zone_Paris_01" in pub_creds["zones"]
