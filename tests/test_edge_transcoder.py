"""
Tests unitaires pour edge_transcoder.py (Transcodage et Re-signature déléguée Phase 3).
"""

import base64
import json
import pytest
from edge_transcoder import EdgeTranscoder
import crypto_engine


def test_edge_transcoder_initialization():
    transcoder = EdgeTranscoder(node_id="Edge_Node_01", zone_id="Zone_1")
    assert transcoder.node_id == "Edge_Node_01"
    assert transcoder.zone_id == "Zone_1"


def test_transcode_bytes():
    original = b"Trame haute definition 1080p flux video pledge"
    transcoded = EdgeTranscoder.transcode_bytes(original, target_quality="720p")
    assert transcoded != original
    assert b"720P" in transcoded


def test_delegated_transcoding_and_verification_lifecycle():
    """
    Vérifie la chaîne complète :
    1. Streamer initialise une session
    2. Edge_Node_01 reçoit et transcode le bloc D_0
    3. Re-signature avec DK_d
    4. Spectateur vérifie via 3 accouplements
    """
    node = EdgeTranscoder(node_id="Edge_Transcoder_Paris_01", zone_id="Zone_1")
    session_id = "test_stream_session_123"
    owner_id = "alice@pledge.local"

    # Setup Zone
    msk, pk_zone = crypto_engine.generate_zone_master_key(9988776655)
    owner_secret = crypto_engine.compute_secret_share(msk, owner_id)

    # 1. Délégation explicite par Alice vers ce nœud
    dk_d, pk_d = crypto_engine.generate_delegation_key(owner_secret, node.node_id)
    node.set_delegation(
        session_id=session_id,
        owner_id=owner_id,
        delegation_key=crypto_engine.g1_to_json(dk_d),
        delegate_pk=crypto_engine.g2_to_json(pk_d),
        zone_pk=crypto_engine.g2_to_json(pk_zone),
    )

    # 2. Arrivée d'un bloc original (1080p)
    raw_block_bytes = b"Fragment video 1080p WebM VP8 Keyframe"
    payload = {
        "chunk_b64": base64.b64encode(raw_block_bytes).decode("utf-8"),
        "crypto": {"paramA": crypto_engine.DEFAULT_PARAM_A}
    }

    # 3. Transcodage et Re-signature déléguée par le Fog
    processed = node.process_and_sign_chunk(
        session_id=session_id,
        block_id=0,
        raw_chunk_payload=payload,
        target_quality="720p",
        owner_id=owner_id,
    )

    assert processed["crypto"]["is_delegated"] is True
    assert processed["crypto"]["delegate_id"] == node.node_id
    assert processed["crypto"]["signature"]["scheme"] == "Hess-IBS-Delegated"

    # 4. Vérification par le spectateur
    transcoded_data = base64.b64decode(processed["chunk_b64"])
    is_valid, detail = node.verify_transcoded_chunk(
        transcoded_data=transcoded_data,
        crypto_metadata=processed["crypto"],
        zone_pk=pk_zone,
    )
    assert is_valid is True
    assert "succès" in detail

    # 5. Détection de falsification : si les données transcodées sont corrompues
    corrupted_data = transcoded_data + b"_HACKED"
    is_valid_corrupt, _ = node.verify_transcoded_chunk(
        transcoded_data=corrupted_data,
        crypto_metadata=processed["crypto"],
        zone_pk=pk_zone,
    )
    assert is_valid_corrupt is False
