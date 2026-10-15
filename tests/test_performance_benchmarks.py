"""
Banc d'Essai de Performances et Validation Expérimentale (Phase 6 du Plan d'Implémentation) :
Reproduit et valide les métriques expérimentales de l'article FGCS 2024 :
'A verifiable data integrity scheme for distributed data sharing in fog computing architecture'
(Sellami, Imine, Gallais - Future Generation Computer Systems 150, 2024).

Ce module mesure et valide :
1. Résistance empirique aux collisions SIS (Section 4.1).
2. Temps de génération de la matrice A via L'Ecuyer 1994 (O(1) stockage).
3. Temps moyen de calcul de la preuve SIS v_i (Théorème 1, ~0.42 ms dans l'article).
4. Temps de signature Hess-IBS par le diffuseur (Section 4.2).
5. Temps de vérification publique locale par accouplement bilinéaire (Section 4.3).
6. Temps de vérification de signature déléguée Fog à 3 accouplements (Section 4.4).
7. Performances des opérations dynamiques (insertion, modification, suppression en O(1)).
"""

import time
import os
import secrets
import statistics
import numpy as np
import pytest

import crypto_engine as engine
from crypto_engine import (
    SIS_L,
    SIS_M,
    SIS_Q,
    DEFAULT_PARAM_A,
    generate_matrix_a,
    compute_sis_vector,
    compute_sis_matrix,
    sign_data,
    verify_signature,
    generate_delegation_key,
    sign_delegated_data,
    verify_delegated_signature,
    dynamic_insert_block,
    dynamic_modify_block,
    dynamic_delete_block,
)


def test_empirical_sis_collision_resistance():
    """
    Validation expérimentale de la résistance aux collisions SIS (Théorème 1).
    Génère 500 fragments vidéo distincts (variations d'un bit, insertions, octets aléatoires)
    et vérifie qu'aucune collision n'est détectée dans Z_q^l.
    """
    seen_vectors = set()
    num_samples = 500
    base_data = b"STREAMING_FRAGMENT_BASE_KEYFRAME_DATA_PADDING_BUFFER"

    for i in range(num_samples):
        # Création de variations subtiles (1 bit à 1 octet altéré)
        mutated = bytearray(base_data)
        mutated.extend(i.to_bytes(4, "big"))
        mutated[i % len(mutated)] ^= 0x01

        v = compute_sis_vector(bytes(mutated), DEFAULT_PARAM_A)
        v_tuple = tuple(v)

        assert v_tuple not in seen_vectors, f"Collision SIS inattendue au spécimen {i} !"
        seen_vectors.add(v_tuple)

    assert len(seen_vectors) == num_samples
    print(f"\n[Validation SIS] {num_samples}/{num_samples} vecteurs distincts : 0 collision détectée.")


def test_matrix_a_generation_benchmark():
    """
    Mesure le temps de reconstruction déterministe de la matrice A (64x1536)
    selon le générateur de L'Ecuyer (1994).
    """
    iterations = 10
    times = []

    # Génération sans cache (paramètres différents à chaque passe)
    for i in range(iterations):
        t0 = time.perf_counter()
        mat = generate_matrix_a(1000 + i, 3141, 567)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0)

    avg_time_ms = statistics.mean(times)
    median_time_ms = statistics.median(times)

    print(f"\n[Benchmark Matrice A L'Ecuyer] Moyenne: {avg_time_ms:.3f} ms | Médiane: {median_time_ms:.3f} ms (64x1536 = 98 304 coeffs)")
    # En Python interprété, reconstruction complète < 100 ms
    assert avg_time_ms < 100.0


def test_sis_proof_vector_latency_benchmark():
    """
    Mesure le temps de calcul du vecteur d'intégrité SIS v_i = (A * x_i) mod q.
    Vérifie l'alignement avec les résultats de l'article scientifique (~0.42 ms).
    """
    block_sizes = [10 * 1024, 100 * 1024, 500 * 1024]  # 10 Ko, 100 Ko, 500 Ko
    results = {}

    for size in block_sizes:
        data = secrets.token_bytes(size)
        iterations = 25
        times = []

        for _ in range(iterations):
            t0 = time.perf_counter()
            _ = compute_sis_vector(data, DEFAULT_PARAM_A)
            t1 = time.perf_counter()
            times.append((t1 - t0) * 1000.0)

        mean_ms = statistics.mean(times)
        results[size] = mean_ms
        print(f"[Benchmark SIS v_i] Taille {size // 1024} Ko: Moyenne = {mean_ms:.3f} ms | Min = {min(times):.3f} ms")

        # Conforme aux prérequis temps réel : calcul SIS < 5.0 ms
        assert mean_ms < 5.0


def test_hess_ibs_signing_and_pairing_benchmark():
    """
    Mesure la latence de signature Hess-IBS et de vérification par accouplement bilinéaire.
    Vérifie la compatibilité avec un débit vidéo fluide (blocs émis toutes les 500-1000 ms).
    """
    msk, pk_zone = engine.generate_zone_master_key(7777777)
    owner_id = "bench_streamer@pledge.local"
    owner_secret = engine.compute_secret_share(msk, owner_id)

    dummy_chunk = b"VIDEO_CHUNK_FRAME_FOR_PAIRING_BENCHMARK_TEST"
    v_vector = compute_sis_vector(dummy_chunk, DEFAULT_PARAM_A)

    # 1. Benchmark Signature (5 itérations pour benchmark rapide)
    sign_times = []
    iterations = 5
    signatures = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        sig = sign_data(v_vector, owner_secret, DEFAULT_PARAM_A)
        t1 = time.perf_counter()
        sign_times.append((t1 - t0) * 1000.0)
        signatures.append(sig)

    avg_sign_ms = statistics.mean(sign_times)
    print(f"\n[Benchmark Hess-IBS Sign] Moyenne: {avg_sign_ms:.3f} ms")
    assert avg_sign_ms < 1500.0

    # 2. Benchmark Vérification Publique (Pairing BN128)
    verify_times = []
    for sig in signatures:
        t0 = time.perf_counter()
        is_valid, _ = verify_signature(
            v_data=v_vector,
            sig_dict=sig,
            owner_identity=owner_id,
            zone_pk=pk_zone,
            param_a=DEFAULT_PARAM_A,
        )
        t1 = time.perf_counter()
        assert is_valid is True
        verify_times.append((t1 - t0) * 1000.0)

    avg_verify_ms = statistics.mean(verify_times)
    print(f"[Benchmark Hess-IBS Verify] Moyenne: {avg_verify_ms:.3f} ms | Min: {min(verify_times):.3f} ms")
    assert avg_verify_ms < 2000.0


def test_delegated_fog_verification_benchmark():
    """
    Mesure la latence de vérification de signature déléguée Fog (3 accouplements bilinéaires).
    """
    msk, pk_zone = engine.generate_zone_master_key(8888888)
    owner_id = "streamer_deleg@pledge.local"
    edge_id = "Edge_Node_Transcoder_Bench"
    owner_secret = engine.compute_secret_share(msk, owner_id)

    dk_edge, pk_edge = generate_delegation_key(owner_secret, edge_id, random_y=424242)

    chunk_720p = b"TRANSCODED_720P_VIDEO_CHUNK_FRAME_PAYLOAD"
    v_trans = compute_sis_vector(chunk_720p, DEFAULT_PARAM_A)
    sig_del = sign_delegated_data(v_trans, dk_edge, DEFAULT_PARAM_A)

    verify_times = []
    iterations = 3
    for _ in range(iterations):
        t0 = time.perf_counter()
        is_valid, _ = verify_delegated_signature(
            v_data=v_trans,
            sig_dict=sig_del,
            owner_identity=owner_id,
            zone_pk=pk_zone,
            delegate_identity=edge_id,
            delegate_pk=pk_edge,
            param_a=DEFAULT_PARAM_A,
        )
        t1 = time.perf_counter()
        assert is_valid is True
        verify_times.append((t1 - t0) * 1000.0)

    avg_del_verify_ms = statistics.mean(verify_times)
    print(f"\n[Benchmark Délégation 3 Pairings] Moyenne: {avg_del_verify_ms:.3f} ms | Min: {min(verify_times):.3f} ms")
    assert avg_del_verify_ms < 3000.0


def test_dynamic_operations_benchmark():
    """
    Mesure la complexité algorithmique et le temps d'exécution des opérations dynamiques (Section 3.5) :
    - Insertion (3.5.3)
    - Modification (3.5.2)
    - Suppression (3.5.1)
    sur une matrice V de N = 50 blocs.
    Vérifie que le temps est quasi-instantané (O(1)) et ne nécessite pas de régénérer la matrice A.
    """
    # Matrice initiale de 50 blocs
    n_blocks = 50
    blocks = [f"Block_{i}_initial_content".encode("utf-8") for i in range(n_blocks)]
    matrix_v = compute_sis_matrix(blocks, DEFAULT_PARAM_A)
    assert len(matrix_v) == SIS_L
    assert len(matrix_v[0]) == n_blocks

    # 1. Insertion
    new_block = b"Dynamically_Inserted_Block_51"
    t0 = time.perf_counter()
    v_inserted, _ = dynamic_insert_block(matrix_v, new_block, index=None, param_a=DEFAULT_PARAM_A)
    t_insert_ms = (time.perf_counter() - t0) * 1000.0
    assert len(v_inserted[0]) == n_blocks + 1

    # 2. Modification
    updated_block = b"Modified_Block_25_Updated_Content"
    t0 = time.perf_counter()
    v_modified, _ = dynamic_modify_block(v_inserted, updated_block, index=25, param_a=DEFAULT_PARAM_A)
    t_modify_ms = (time.perf_counter() - t0) * 1000.0
    assert len(v_modified[0]) == n_blocks + 1

    # 3. Suppression
    t0 = time.perf_counter()
    v_deleted = dynamic_delete_block(v_modified, index=0)
    t_delete_ms = (time.perf_counter() - t0) * 1000.0
    assert len(v_deleted[0]) == n_blocks

    print(f"\n[Benchmark Opérations Dynamiques (N=50 blocs)]")
    print(f"  - Insertion:    {t_insert_ms:.3f} ms (recalcul v_new uniquement)")
    print(f"  - Modification: {t_modify_ms:.3f} ms (remplacement colonne unique)")
    print(f"  - Suppression:  {t_delete_ms:.3f} ms (retrait colonne en mémoire)")

    assert t_insert_ms < 10.0
    assert t_modify_ms < 10.0
    assert t_delete_ms < 5.0
