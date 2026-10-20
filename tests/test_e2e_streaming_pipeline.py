"""
Test d'Intégration de Bout en Bout (End-to-End Pipeline System Test) :
Valide l'ensemble de l'architecture Fog décentralisée Pledge selon l'article FGCS 2024.

Cycle complet vérifié :
1. Setup de Zone (Serveur d'Identification IS_s).
2. Identification du Data Owner (Streamer) et émission de la part secrète S_w.
3. Plan de Signalisation FastAPI : allocation directe du nœud Edge Fog (sans goulot central).
4. Délégation de signature émise pour le nœud Fog (DK_d, PK_d).
5. Plan de Données Direct : Streaming de blocs WebM signés via WebSocket vers le Fog.
6. Transcodage Edge autonome et re-signature déléguée à la volée.
7. Plan d'Annuaire FastAPI : Découverte directe du flux par le spectateur.
8. Téléchargement et Audit Public sans TPA par le spectateur (Original & Transcodé 720p).
9. Résistance aux attaques : détection et rejet immédiat de corruption de payload ou de signature.
10. Nettoyage et libération du stockage Edge à l'arrêt du flux.
"""

import asyncio
import base64
import json
import os
import shutil
import tempfile
import uuid
import pytest
import websockets
from fastapi.testclient import TestClient

import crypto_engine as engine
from edge_transcoder import EdgeTranscoder
import client_edge as edge_module
from main import app
from models import SessionLocal, EdgeNode, StreamSession, DataLocation
from security import create_stream_token
from stream_router import DEFAULT_PARAM_A


@pytest.fixture(scope="module")
def e2e_environment():
    """Prépare un environnement isolé avec un répertoire de stockage temporaire et un port libre."""
    temp_dir = tempfile.mkdtemp(prefix="pledge_e2e_storage_")
    original_storage = edge_module.DOSSIER_RACINE_STOCKAGE
    edge_module.DOSSIER_RACINE_STOCKAGE = temp_dir

    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()

    node_id = "Edge_E2E_Paris_01"
    zone_id = "Zone_1"

    original_node_id = edge_module.NODE_ID
    original_port = edge_module.EDGE_WS_PORT
    edge_module.NODE_ID = node_id
    edge_module.EDGE_WS_PORT = port

    # Enregistrement du nœud Edge en base avec score prioritaire
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    node = db.query(EdgeNode).filter(EdgeNode.node_id == node_id).first()
    if not node:
        node = EdgeNode(
            node_id=node_id,
            zone_id=zone_id,
            ip_address="127.0.0.1",
            wss_port=port,
            storage_available_gb=99999.0,
            cpu_usage_pct=1.0,
            last_seen=now,
        )
        db.add(node)
    else:
        node.storage_available_gb = 99999.0
        node.cpu_usage_pct = 1.0
        node.ip_address = "127.0.0.1"
        node.wss_port = port
        node.last_seen = now
    db.commit()
    db.close()

    yield {
        "storage_dir": temp_dir,
        "node_id": node_id,
        "zone_id": zone_id,
        "port": port,
    }

    # Nettoyage final
    shutil.rmtree(temp_dir, ignore_errors=True)
    edge_module.DOSSIER_RACINE_STOCKAGE = original_storage
    edge_module.NODE_ID = original_node_id
    edge_module.EDGE_WS_PORT = original_port

    db = SessionLocal()
    db.query(EdgeNode).filter(EdgeNode.node_id == node_id).delete()
    db.commit()
    db.close()


@pytest.mark.asyncio
async def test_full_e2e_streaming_and_verification_pipeline(e2e_environment):
    """Exécute le cycle complet de diffusion, transcodage, streaming direct et audit public."""
    node_id = e2e_environment["node_id"]
    zone_id = e2e_environment["zone_id"]
    port = e2e_environment["port"]
    storage_dir = e2e_environment["storage_dir"]

    client_fastapi = TestClient(app)
    session_uid = uuid.uuid4().hex[:8]
    streamer_id = f"alice_streamer_{session_uid}@example.com"
    viewer_id = f"bob_viewer_{session_uid}@example.com"

    # =========================================================================
    # ÉTAPE 1 : SETUP DE ZONE & IDENTIFICATION DU STREAMER (IS_s)
    # =========================================================================
    msk_zone, pk_zone = engine.generate_zone_master_key(314159265)
    owner_secret = engine.compute_secret_share(msk_zone, streamer_id)

    # =========================================================================
    # ÉTAPE 2 : SIGNALISATION - ALLOCATION D'UN NŒUD FOG VIA FASTAPI
    # =========================================================================
    alloc_resp = client_fastapi.post(
        "/api/stream/allocate",
        json={"session_id": streamer_id, "zone_id": zone_id},
    )
    assert alloc_resp.status_code == 200
    alloc_data = alloc_resp.json()
    assert alloc_data["session_id"] == streamer_id
    assert f"/upload/{streamer_id}" in alloc_data["upload_url"]
    assert "token" in alloc_data
    streamer_token = alloc_data["token"]
    param_a = alloc_data["paramA"]

    # =========================================================================
    # ÉTAPE 3 : DÉLÉGATION DE TRANSCODAGE VERS LE NŒUD EDGE (Section 3.6.1)
    # =========================================================================
    transcoder = EdgeTranscoder(node_id=node_id, zone_id=zone_id)
    edge_module.edge_transcoder = transcoder

    dk_edge, pk_edge = engine.generate_delegation_key(owner_secret, node_id, random_y=777888)
    transcoder.set_delegation(
        session_id=streamer_id,
        owner_id=streamer_id,
        delegation_key=engine.g1_to_json(dk_edge),
        delegate_pk=engine.g2_to_json(pk_edge),
        zone_pk=engine.g2_to_json(pk_zone),
    )

    # =========================================================================
    # ÉTAPE 4 : PLAN DE DONNÉES - STREAMING DIRECT VIA WEBSOCKET (DIFFUSEUR -> FOG)
    # =========================================================================
    server = await websockets.serve(edge_module.routeur_ws, "127.0.0.1", port)

    try:
        raw_blocks = [
            b"\x1a\x45\xdf\xa3WEBM_INIT_HEADER_DATA_1080P",
            b"VP8_VIDEO_KEYFRAME_PAYLOAD_CHUNK_1",
            b"VP8_VIDEO_DELTA_FRAME_PAYLOAD_CHUNK_2",
        ]
        packaged_blocks = []

        # Construction et signature de chaque bloc côté diffuseur
        for idx, block_bytes in enumerate(raw_blocks):
            v_i = engine.compute_sis_vector(block_bytes, param_a)
            sig_i = engine.sign_data(v_i, owner_secret, param_a)

            pkt = {
                "block_index": idx,
                "payload_b64": base64.b64encode(block_bytes).decode("utf-8"),
                "paramA": param_a,
                "V_column": v_i,
                "signature": sig_i,
                "signer_id": streamer_id,
                "is_delegated": False,
            }
            packaged_blocks.append(pkt)

        # Upload direct vers le nœud Fog
        upload_ws_url = f"ws://127.0.0.1:{port}/upload/{streamer_id}?token={streamer_token}"
        async with websockets.connect(upload_ws_url) as ws_streamer:
            for pkt in packaged_blocks:
                await ws_streamer.send(json.dumps(pkt))
                ack_raw = await ws_streamer.recv()
                ack = json.loads(ack_raw)
                assert ack["statut"] == "ok"
                assert ack["bloc_id"] == pkt["block_index"]

        # Laisser le temps à l'io_executor de finaliser les écritures et le transcodage
        await asyncio.sleep(0.5)

        # =====================================================================
        # ÉTAPE 5 : ANNUAIRE - DÉCOUVERTE DIRECTE DU FLUX PAR LE SPECTATEUR
        # =====================================================================
        disc_resp = client_fastapi.get(f"/api/stream/discover/{streamer_id}?mode=start")
        assert disc_resp.status_code == 200
        disc_data = disc_resp.json()
        assert disc_data["session_id"] == streamer_id
        assert disc_data["status"] == "live"
        assert f"/download/{streamer_id}" in disc_data["download_url"]
        assert "quality=720p" in disc_data["transcoded_download_url"]
        viewer_token = disc_data["token"]

        # =====================================================================
        # ÉTAPE 6 : AUDIT PUBLIC SANS TPA DU FLUX ORIGINAL (1080p)
        # =====================================================================
        download_orig_url = f"ws://127.0.0.1:{port}/download/{streamer_id}?mode=start&quality=original&token={viewer_token}"
        async with websockets.connect(download_orig_url) as ws_viewer_orig:
            for expected_idx in range(len(raw_blocks)):
                msg_raw = await ws_viewer_orig.recv()
                packet = json.loads(msg_raw)

                assert packet["block_index"] == expected_idx
                assert packet["signer_id"] == streamer_id
                assert packet["is_delegated"] is False

                received_payload = base64.b64decode(packet["payload_b64"])
                assert received_payload == raw_blocks[expected_idx]

                # 1. Vérification SIS locale par le spectateur
                recomputed_v = engine.compute_sis_vector(received_payload, packet["paramA"])
                assert recomputed_v == packet["V_column"]

                # 2. Vérification Hess-IBS bilinéaire par le spectateur
                is_valid, detail = engine.verify_signature(
                    v_data=packet["V_column"],
                    sig_dict=packet["signature"],
                    owner_identity=packet["signer_id"],
                    zone_pk=pk_zone,
                    param_a=packet["paramA"],
                )
                assert is_valid is True, f"Échec vérification originale bloc {expected_idx}: {detail}"

        # =====================================================================
        # ÉTAPE 7 : AUDIT PUBLIC DE LA SIGNATURE DÉLÉGUÉE (720p FOG)
        # =====================================================================
        download_trans_url = f"ws://127.0.0.1:{port}/download/{streamer_id}?mode=start&quality=720p&token={viewer_token}"
        async with websockets.connect(download_trans_url) as ws_viewer_trans:
            for expected_idx in range(len(raw_blocks)):
                msg_raw = await ws_viewer_trans.recv()
                packet = json.loads(msg_raw)

                assert packet["block_index"] == expected_idx
                # Doit être certifié comme transcodé délégué
                crypto_meta = packet.get("crypto", {})
                assert crypto_meta.get("is_delegated") is True
                assert crypto_meta.get("delegate_id") == node_id

                transcoded_payload = base64.b64decode(packet["chunk_b64"])
                # Vérification de l'adaptation de résolution
                assert b"720P" in transcoded_payload

                # 1. Vérification SIS locale du bloc transcodé
                v_col = crypto_meta.get("V_column") or packet.get("V_column")
                param_a_del = crypto_meta.get("paramA") or packet.get("paramA")
                recomputed_v_trans = engine.compute_sis_vector(transcoded_payload, param_a_del)
                assert recomputed_v_trans == v_col

                # 2. Vérification de la signature déléguée via 3 couplages bilinéaires
                sig_del = crypto_meta.get("signature") or packet.get("signature")
                is_del_valid, del_detail = engine.verify_delegated_signature(
                    v_data=v_col,
                    sig_dict=sig_del,
                    owner_identity=streamer_id,
                    zone_pk=pk_zone,
                    delegate_identity=node_id,
                    delegate_pk=pk_edge,
                    param_a=param_a_del,
                )
                assert is_del_valid is True, f"Échec vérification déléguée bloc {expected_idx}: {del_detail}"

        # =====================================================================
        # ÉTAPE 8 : RÉSISTANCE AUX ATTAQUES & NON-FALSIFICATION (THÉORÈMES 1 & 2)
        # =====================================================================
        test_block = packaged_blocks[1]
        valid_payload = raw_blocks[1]

        # 1. Attaque par altération de contenu (Bit-flip)
        tampered_payload = bytearray(valid_payload)
        tampered_payload[0] ^= 0x01  # Inversion d'un seul bit
        v_tampered = engine.compute_sis_vector(bytes(tampered_payload), test_block["paramA"])
        # La preuve SIS ne correspond plus
        assert v_tampered != test_block["V_column"]
        is_tampered_sig_valid, _ = engine.verify_signature(
            v_data=v_tampered,
            sig_dict=test_block["signature"],
            owner_identity=test_block["signer_id"],
            zone_pk=pk_zone,
            param_a=test_block["paramA"],
        )
        assert is_tampered_sig_valid is False

        # 2. Attaque par falsification de signature (w1 corrompu)
        forged_sig = dict(test_block["signature"])
        forged_sig["w1"] = str(int(forged_sig["w1"]) + 42)
        is_forged_valid, _ = engine.verify_signature(
            v_data=test_block["V_column"],
            sig_dict=forged_sig,
            owner_identity=test_block["signer_id"],
            zone_pk=pk_zone,
            param_a=test_block["paramA"],
        )
        assert is_forged_valid is False

        # 3. Attaque par usurpation d'identité (Mallory)
        is_impersonation_valid, _ = engine.verify_signature(
            v_data=test_block["V_column"],
            sig_dict=test_block["signature"],
            owner_identity="mallory_attacker@evil.com",
            zone_pk=pk_zone,
            param_a=test_block["paramA"],
        )
        assert is_impersonation_valid is False

        # =====================================================================
        # ÉTAPE 9 : ARRÊT DU FLUX ET COMMANDE DE PURGE DE STOCKAGE
        # =====================================================================
        stop_resp = client_fastapi.post(f"/api/stream/stop/{streamer_id}")
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "stopped"

        # Purge effective du dossier de session
        client_dir = os.path.join(storage_dir, streamer_id)
        edge_module.purge_session_directory(client_dir)
        assert not os.path.exists(client_dir)

    finally:
        server.close()
        await server.wait_closed()
