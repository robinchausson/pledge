"""
Tests unitaires rigoureux pour crypto_engine.py
Couvrant l'ensemble des exigences cryptographiques de l'article FGCS 2024.
"""

import numpy as np
import pytest
from py_ecc.optimized_bn128 import G1, G2, curve_order, multiply

from crypto_engine import (
    SIS_L,
    SIS_M,
    SIS_Q,
    accumulate_public_keys,
    accumulate_secret_keys,
    compute_secret_share,
    compute_sis_matrix,
    compute_sis_vector,
    dynamic_delete_block,
    dynamic_insert_block,
    dynamic_modify_block,
    generate_delegation_key,
    generate_matrix_a,
    generate_zone_master_key,
    hash_h1_to_g1,
    hash_to_bit_vector,
    json_to_g1,
    json_to_g2,
    sign_data,
    sign_delegated_data,
    verify_delegated_signature,
    verify_signature,
)


def test_matrix_a_generation():
    """Vérifie la génération déterministe de la matrice A via L'Ecuyer."""
    matrix1 = generate_matrix_a(1234, 3141, 567)
    matrix2 = generate_matrix_a(1234, 3141, 567)

    assert matrix1.shape == (SIS_L, SIS_M)
    assert np.array_equal(matrix1, matrix2)
    assert np.all(matrix1 >= 0) and np.all(matrix1 < SIS_Q)

    # Paramètres différents doivent produire une matrice différente
    matrix3 = generate_matrix_a(9999, 3141, 567)
    assert not np.array_equal(matrix1, matrix3)


def test_hash_to_bit_vector():
    """Vérifie que x_i est un vecteur strictement binaire {0, 1}^m."""
    data = b"Video Block Chunk 001"
    vector_x = hash_to_bit_vector(data)

    assert vector_x.shape == (SIS_M,)
    unique_vals = set(np.unique(vector_x))
    assert unique_vals.issubset({0, 1})


def test_sis_integrity_vector_and_matrix():
    """Vérifie le calcul matriciel SIS pour un bloc et une matrice de blocs."""
    block1 = b"Fragment 1: keyframe webm"
    block2 = b"Fragment 2: delta frame webm"

    v1 = compute_sis_vector(block1)
    assert len(v1) == SIS_L
    assert all(0 <= val < SIS_Q for val in v1)

    # Même bloc -> même vecteur
    assert v1 == compute_sis_vector(block1)

    # Bloc différent -> vecteur différent (résistance aux collisions)
    v2 = compute_sis_vector(block2)
    assert v1 != v2

    # Matrice V
    matrix_v = compute_sis_matrix([block1, block2])
    assert len(matrix_v) == SIS_L
    assert len(matrix_v[0]) == 2


def test_hess_ibs_lifecycle():
    """Cycle complet de signature et vérification Hess-IBS basée sur l'identité."""
    # 1. Setup Zone
    msk, pk_zone = generate_zone_master_key(configured_scalar=987654321)

    # 2. Identification de l'Owner
    owner_id = "alice@pledge.local"
    secret_share = compute_secret_share(msk, owner_id)

    # 3. Calcul de la preuve SIS
    chunk = b"Trame video haute definition Alice"
    v_vector = compute_sis_vector(chunk)

    # 4. Signature par Alice
    signature = sign_data(v_vector, secret_share)
    assert signature["scheme"] == "Hess-IBS"
    assert "w1" in signature and "w2" in signature

    # 5. Vérification publique par un spectateur (Bob)
    is_valid, detail = verify_signature(v_vector, signature, owner_id, pk_zone)
    assert is_valid is True
    assert "succès" in detail


def test_hess_ibs_tamper_detection():
    """Vérifie la détection de corruption de données ou d'usurpation d'identité."""
    msk, pk_zone = generate_zone_master_key(123456789)
    owner_id = "alice@pledge.local"
    secret_share = compute_secret_share(msk, owner_id)

    chunk = b"Trame originale"
    v_vector = compute_sis_vector(chunk)
    signature = sign_data(v_vector, secret_share)

    # Altération des données
    corrupted_chunk = b"Trame alteree par attaquant"
    v_corrupted = compute_sis_vector(corrupted_chunk)
    valid_corrupt, _ = verify_signature(v_corrupted, signature, owner_id, pk_zone)
    assert valid_corrupt is False

    # Usurpation d'identité
    valid_fake_id, _ = verify_signature(v_vector, signature, "mallory@pledge.local", pk_zone)
    assert valid_fake_id is False

    # Falsification du challenge w1
    bad_sig = dict(signature)
    bad_sig["w1"] = str(int(signature["w1"]) + 1)
    valid_bad_w1, _ = verify_signature(v_vector, bad_sig, owner_id, pk_zone)
    assert valid_bad_w1 is False


def test_multi_zone_mobility_accumulation():
    """Vérifie l'accumulation des clés lors du passage entre plusieurs zones."""
    # Zone 1
    msk_z1, pk_z1 = generate_zone_master_key(111111111)
    # Zone 2
    msk_z2, pk_z2 = generate_zone_master_key(222222222)

    owner_id = "alice@pledge.local"
    share_z1 = compute_secret_share(msk_z1, owner_id)
    share_z2 = compute_secret_share(msk_z2, owner_id)

    # Accumulation
    sw_accumulated = accumulate_secret_keys([share_z1, share_z2])
    pk_accumulated = accumulate_public_keys([pk_z1, pk_z2])

    v_vector = compute_sis_vector(b"Donnees partagees sur deux zones")
    signature = sign_data(v_vector, sw_accumulated)

    # Vérification avec la clé publique accumulée
    is_valid, _ = verify_signature(v_vector, signature, owner_id, pk_accumulated)
    assert is_valid is True

    # Doit échouer si vérifié avec une seule clé de zone
    is_valid_single, _ = verify_signature(v_vector, signature, owner_id, pk_z1)
    assert is_valid_single is False


def test_signature_delegation_and_transcoding():
    """Vérifie le protocole de délégation de signature pour un nœud Fog."""
    # Zone Setup
    msk, pk_zone = generate_zone_master_key(555555555)
    owner_id = "streamer@pledge.local"
    owner_secret = compute_secret_share(msk, owner_id)

    # Nœud Fog délégué
    edge_id = "Edge_Transcoder_Node_01"

    # Alice émet la clé de délégation pour le Edge Node
    dk_edge, pk_edge = generate_delegation_key(owner_secret, edge_id)

    # Le nœud Edge transcode et modifie les données
    transcoded_chunk = b"Trame 720p transcodee par le Fog"
    v_transcoded = compute_sis_vector(transcoded_chunk)

    # Le nœud Edge signe avec sa clé de délégation
    sig_delegated = sign_delegated_data(v_transcoded, dk_edge)
    assert sig_delegated["scheme"] == "Hess-IBS-Delegated"

    # Le spectateur vérifie la signature déléguée
    is_valid, detail = verify_delegated_signature(
        v_data=v_transcoded,
        sig_dict=sig_delegated,
        owner_identity=owner_id,
        zone_pk=pk_zone,
        delegate_identity=edge_id,
        delegate_pk=pk_edge,
    )
    assert is_valid is True
    assert "succès" in detail

    # Échec si le mauvais délégué est déclaré
    is_valid_fake_edge, _ = verify_delegated_signature(
        v_data=v_transcoded,
        sig_dict=sig_delegated,
        owner_identity=owner_id,
        zone_pk=pk_zone,
        delegate_identity="Rogue_Node_99",
        delegate_pk=pk_edge,
    )
    assert is_valid_fake_edge is False


def test_dynamic_operations_lifecycle():
    """
    Section 3.5 : Opérations dynamiques sur la matrice V :
      - Insertion de bloc (3.5.3)
      - Modification de bloc (3.5.2)
      - Suppression de bloc (3.5.1)
    """
    msk, pk_zone = generate_zone_master_key(777777777)
    owner_id = "alice@pledge.local"
    owner_secret = compute_secret_share(msk, owner_id)

    # 1. Matrice initiale avec 2 blocs D1 et D2
    b1 = b"Bloc 1 original"
    b2 = b"Bloc 2 original"
    matrix_v = compute_sis_matrix([b1, b2])
    assert len(matrix_v) == SIS_L
    assert len(matrix_v[0]) == 2

    # 2. Insertion d'un 3ème bloc D3 (Section 3.5.3)
    b3 = b"Bloc 3 insere dynamiquement"
    matrix_v_inserted, v3 = dynamic_insert_block(matrix_v, b3)
    assert len(matrix_v_inserted[0]) == 3
    sig_inserted = sign_data(matrix_v_inserted, owner_secret)
    is_valid_ins, _ = verify_signature(matrix_v_inserted, sig_inserted, owner_id, pk_zone)
    assert is_valid_ins is True

    # 3. Modification du bloc D2 en D'2 (Section 3.5.2)
    b2_prime = b"Bloc 2 modifie dynamiquement"
    matrix_v_modified, v2_prime = dynamic_modify_block(matrix_v_inserted, b2_prime, index=1)
    assert len(matrix_v_modified[0]) == 3
    sig_modified = sign_data(matrix_v_modified, owner_secret)
    is_valid_mod, _ = verify_signature(matrix_v_modified, sig_modified, owner_id, pk_zone)
    assert is_valid_mod is True

    # 4. Suppression du bloc D1 (Section 3.5.1)
    matrix_v_deleted = dynamic_delete_block(matrix_v_modified, index=0)
    assert len(matrix_v_deleted[0]) == 2
    sig_deleted = sign_data(matrix_v_deleted, owner_secret)
    is_valid_del, _ = verify_signature(matrix_v_deleted, sig_deleted, owner_id, pk_zone)
    assert is_valid_del is True


def test_hash_h1_to_g1_properties():
    """Vérifie que H1 est déterministe, sur la courbe, et sans logarithme discret trivial."""
    import hashlib
    from py_ecc.optimized_bn128 import b, is_on_curve, normalize

    identity = "alice@pledge.local"
    pt1 = hash_h1_to_g1(identity)
    pt2 = hash_h1_to_g1(identity)

    # Déterminisme
    assert pt1 == pt2

    # Appartenance à la courbe
    assert is_on_curve(pt1, b)

    # Non-trivialité (ne doit pas être le scalaire direct multiplié par G1)
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    trivial_scalar = int.from_bytes(digest, "big") % (curve_order - 1) + 1
    trivial_pt = multiply(G1, trivial_scalar)
    assert normalize(pt1) != normalize(trivial_pt)

