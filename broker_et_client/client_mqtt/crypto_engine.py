"""
Moteur Cryptographique conforme à l'article scientifique :
'A verifiable data integrity scheme for distributed data sharing in fog computing architecture'
(Sellami, Imine, Gallais - Future Generation Computer Systems 150, 2024, pp. 64-77).

Piliers mathématiques implémentés :
1. SIS (Short Integer Solution) avec générateur uniforme L'Ecuyer (1994) et vecteurs binaires {0, 1}^m.
2. Schéma de signature basé sur l'identité (Hess-IBS) sur courbe BN128 avec accouplement bilinéaire.
3. Mobilité multi-zone avec accumulation homomorphe des parts secrètes et clés publiques.
4. Délégation de signature pour les nœuds Fog (transcodage légitime).
"""

from __future__ import annotations

import functools
import hashlib
import secrets
from typing import Any, Tuple

import numpy as np
from py_ecc.optimized_bn128 import (
    FQ,
    FQ2,
    G1,
    G2,
    add,
    b,
    curve_order,
    field_modulus,
    is_on_curve,
    multiply,
    neg,
    normalize,
    pairing,
)

# --- PARAMÈTRES SIS DE L'ARTICLE (Tableau 4) ---
SIS_L: int = 64       # Nombre de lignes de A
SIS_M: int = 1536     # Nombre de colonnes de A (m >= 2 * l * log2(q))
SIS_Q: int = 4099     # Modulo premier (q ~= l^2)
HASH_BITS: int = 256  # SHA-256 -> lambda = 256 bits

# Paramètres par défaut du générateur I (L'Ecuyer 1994)
DEFAULT_PARAM_A: dict[str, int] = {
    "I0": 1234,
    "a": 3141,
    "C0": 567,
}


# =====================================================================
# 1. GÉNÉRATEUR UNIFORME L'ECUYER & MATRICE A (Section 3.4.1 / 3.4.3.1)
# =====================================================================

@functools.lru_cache(maxsize=16)
def generate_matrix_a(i0: int = 1234, a: int = 3141, c0: int = 567) -> np.ndarray:
    """
    Reconstruit de manière déterministe la matrice A in Z_q^(l x m)
    selon le générateur récursif de L'Ecuyer (1994) :
      I_{n+1} = (a * I_n + C_n) mod q
      C_{n+1} = (a * I_n + C_n) div q
    """
    total_elements = SIS_L * SIS_M
    values = np.empty(total_elements, dtype=np.int64)
    current_i = int(i0)
    current_c = int(c0)
    multiplier = int(a)
    modulus = int(SIS_Q)

    for index in range(total_elements):
        combined = multiplier * current_i + current_c
        current_i = combined % modulus
        current_c = combined // modulus
        values[index] = current_i

    return values.reshape((SIS_L, SIS_M))


def parse_param_a(param_a: dict | None) -> Tuple[int, int, int]:
    """Extrait et valide le triplet (I0, a, C0)."""
    if not param_a:
        return (DEFAULT_PARAM_A["I0"], DEFAULT_PARAM_A["a"], DEFAULT_PARAM_A["C0"])
    return (
        int(param_a.get("I0", DEFAULT_PARAM_A["I0"])),
        int(param_a.get("a", DEFAULT_PARAM_A["a"])),
        int(param_a.get("C0", DEFAULT_PARAM_A["C0"])),
    )


# =====================================================================
# 2. VECTEUR BINAIRE x_i ET PREUVE D'INTÉGRITÉ SIS (Section 3.4.3.2)
# =====================================================================

def hash_to_bit_vector(data: bytes) -> np.ndarray:
    """
    H3(Di) = hi in {0, 1}^lambda (SHA-256, 256 bits).
    Injecte la séquence de bits plusieurs fois (m / lambda = 1536 / 256 = 6)
    pour former le vecteur binaire x_i in {0, 1}^m (Figure 2 de l'article).
    """
    digest = hashlib.sha256(data).digest()
    bits = np.unpackbits(np.frombuffer(digest, dtype=np.uint8))
    repetitions = SIS_M // len(bits)  # 1536 // 256 = 6
    return np.tile(bits, repetitions).astype(np.int64)


def compute_sis_vector(data: bytes, param_a: dict | None = None) -> list[int]:
    """
    Calcule le vecteur SIS pour un bloc Di :
      v_i = (A * x_i) mod q in Z_q^l
    """
    i0, a, c0 = parse_param_a(param_a)
    matrix_a = generate_matrix_a(i0, a, c0)
    vector_x = hash_to_bit_vector(data)
    result_v = np.mod(np.dot(matrix_a, vector_x), SIS_Q)
    return result_v.tolist()


def compute_sis_matrix(blocks: list[bytes], param_a: dict | None = None) -> list[list[int]]:
    """
    Calcule la matrice d'intégrité V in Z_q^(l x N) pour un ensemble de N blocs :
      V = (A * X) mod q, où X = [x_1, x_2, ..., x_N]
    """
    i0, a, c0 = parse_param_a(param_a)
    matrix_a = generate_matrix_a(i0, a, c0)
    columns = [hash_to_bit_vector(block) for block in blocks]
    matrix_x = np.column_stack(columns)
    matrix_v = np.mod(np.dot(matrix_a, matrix_x), SIS_Q)
    return matrix_v.tolist()


def canonical_v_repr(v_data: Any) -> str:
    """Sérialisation canonique et déterministe de V (ou v_i) pour le hachage H2."""
    if isinstance(v_data, list):
        if v_data and isinstance(v_data[0], list):
            # Matrice l x N
            return "[" + ",".join("[" + ",".join(str(int(val)) for val in col) + "]" for col in v_data) + "]"
        return "[" + ",".join(str(int(val)) for val in v_data) + "]"
    return str(v_data)


# =====================================================================
# 3. SÉRIALISATION DES POINTS BN128 & HASH-TO-CURVE (H1, H2)
# =====================================================================

def g1_to_json(point: tuple) -> list[int]:
    """Convertit un point G1 en coordonnées affines entières [x, y]."""
    affine = normalize(point)
    return [int(affine[0]), int(affine[1])]


def json_to_g1(coords: list[int] | tuple) -> tuple:
    """Reconstruit un point G1 (projectif avec z=1) à partir de [x, y]."""
    if isinstance(coords, tuple) and len(coords) == 3:
        return coords
    if isinstance(coords, tuple) and len(coords) == 2:
        return (coords[0], coords[1], FQ.one())
    if len(coords) != 2:
        raise ValueError("Un point G1 doit comporter 2 coordonnées [x, y]")
    return (FQ(int(coords[0])), FQ(int(coords[1])), FQ.one())


def g2_to_json(point: tuple) -> list[list[int]]:
    """Convertit un point G2 en coordonnées entières [[x0, x1], [y0, y1]]."""
    affine = normalize(point)
    return [
        [int(affine[0].coeffs[0]), int(affine[0].coeffs[1])],
        [int(affine[1].coeffs[0]), int(affine[1].coeffs[1])],
    ]


def json_to_g2(coords: list[list[int]] | tuple) -> tuple:
    """Reconstruit un point G2 (projectif avec z=1) à partir de [[x0, x1], [y0, y1]]."""
    if isinstance(coords, tuple) and len(coords) == 3:
        return coords
    if isinstance(coords, tuple) and len(coords) == 2:
        return (coords[0], coords[1], FQ2.one())
    if len(coords) != 2 or len(coords[0]) != 2 or len(coords[1]) != 2:
        raise ValueError("Un point G2 doit comporter 2 paires de coordonnées")
    return (
        FQ2([int(coords[0][0]), int(coords[0][1])]),
        FQ2([int(coords[1][0]), int(coords[1][1])]),
        FQ2.one(),
    )


def ensure_g1(pt: Any) -> tuple:
    return json_to_g1(pt) if not (isinstance(pt, tuple) and len(pt) == 3) else pt


def ensure_g2(pt: Any) -> tuple:
    return json_to_g2(pt) if not (isinstance(pt, tuple) and len(pt) == 3) else pt


def hash_h1_to_g1(identity: str) -> tuple:
    """
    Fonction H1 : {0, 1}* -> G1 (Section 3.4.1 Step 3).
    Mappe de manière déterministe une chaîne d'identité vers un point régulier de G1
    sans logarithme discret connu (Try-and-Increment / Map-to-Curve conforme RFC 9380).
    """
    p = field_modulus
    exp_residue = (p - 1) // 2
    exp_sqrt = (p + 1) // 4
    base_msg = identity.strip().encode("utf-8")
    counter = 0

    while True:
        counter_bytes = counter.to_bytes(4, "big")
        digest = hashlib.sha256(base_msg + counter_bytes).digest()
        x = int.from_bytes(digest, "big") % p
        rhs = (x * x * x + 3) % p
        # Test du résidu quadratique via le critère d'Euler
        if pow(rhs, exp_residue, p) == 1:
            y = pow(rhs, exp_sqrt, p)
            # Normalisation déterministe : choix de la racine paire
            if y % 2 != 0:
                y = p - y
            pt = (FQ(x), FQ(y), FQ.one())
            if is_on_curve(pt, b):
                return pt
        counter += 1


def hash_h2(v_repr: str, param_a_repr: str, pairing_element: Any) -> int:
    """
    Fonction H2 : {0, 1}* -> Z_p* (Section 3.4.1 Step 3).
    Calcule le challenge scalaire w1 à partir de (V, paramA, r).
    """
    message = f"{v_repr}|{param_a_repr}|{str(pairing_element)}".encode("utf-8")
    digest = hashlib.sha256(message).digest()
    challenge = int.from_bytes(digest, "big") % curve_order
    return challenge if challenge != 0 else 1


# =====================================================================
# 4. GESTION DES CLÉS & MOBILITÉ MULTI-ZONE (Section 3.4.1 & 3.4.2)
# =====================================================================

def generate_zone_master_key(configured_scalar: int | None = None) -> Tuple[int, tuple]:
    """
    Génère le secret maître MSK = ts in Z_p* et la clé publique de zone PKs = ts * G2.
    """
    if configured_scalar and 0 < configured_scalar < curve_order:
        ts = int(configured_scalar)
    else:
        ts = secrets.randbelow(curve_order - 1) + 1
    pk_zone = multiply(G2, ts)
    return ts, pk_zone


def compute_secret_share(master_secret: int, identity: str) -> tuple:
    """
    Calcul de la part secrète par le serveur d'identification ISs :
      S_w^s = ts * H1(ID_w) in G1 (Section 3.4.2 Step 2)
    """
    h1_pt = hash_h1_to_g1(identity)
    return multiply(h1_pt, int(master_secret))


def accumulate_secret_keys(shares: list[tuple]) -> tuple:
    """Accumulation des parts secrètes : Sw = sum(S_w^s)."""
    if not shares:
        raise ValueError("Au moins une part secrète est requise")
    total = shares[0]
    for share in shares[1:]:
        total = add(total, share)
    return total


def accumulate_public_keys(pks: list[tuple]) -> tuple:
    """Accumulation des clés publiques de zones : PK = sum(PK_s)."""
    if not pks:
        raise ValueError("Au moins une clé publique est requise")
    total = pks[0]
    for pk in pks[1:]:
        total = add(total, pk)
    return total


# =====================================================================
# 5. SIGNATURE HESS-IBS & VÉRIFICATION (Section 3.4.3.3 & 3.4.4)
# =====================================================================

def sign_data(v_data: Any, secret_key: tuple, param_a: dict | None = None) -> dict:
    """
    Signature Hess-IBS générée par le propriétaire Ow :
      1. Choisir k in Z_p*, P1 = G1
      2. r = e(G2, k * P1)
      3. w1 = H2(V, paramA, r)
      4. w2 = w1 * Sw + k * P1
      sigma = (w1, w2)
    """
    i0, a, c0 = parse_param_a(param_a)
    param_repr = f"{i0},{a},{c0}"
    v_repr = canonical_v_repr(v_data)

    k = secrets.randbelow(curve_order - 1) + 1
    p1 = G1
    k_p1 = multiply(p1, k)
    r = pairing(G2, k_p1)

    w1 = hash_h2(v_repr, param_repr, r)
    w1_sw = multiply(secret_key, w1)
    w2 = add(w1_sw, k_p1)

    return {
        "scheme": "Hess-IBS",
        "w1": str(w1),
        "w2": g1_to_json(w2),
        "p1": g1_to_json(p1),
    }


def verify_signature(
    v_data: Any,
    sig_dict: dict,
    owner_identity: str,
    zone_pk: tuple,
    param_a: dict | None = None,
) -> Tuple[bool, str]:
    """
    Vérification publique Hess-IBS par un consommateur (Section 3.4.4) :
      r' = e(G2, w2) * e(PK, w1 * (-H1(ID_w)))
      Tester w1 == H2(V', paramA, r')
    """
    try:
        w1 = int(sig_dict["w1"])
        w2 = json_to_g1(sig_dict["w2"])
        pk_point = ensure_g2(zone_pk)

        i0, a, c0 = parse_param_a(param_a)
        param_repr = f"{i0},{a},{c0}"
        v_repr = canonical_v_repr(v_data)

        h1_id = hash_h1_to_g1(owner_identity)
        neg_h1_scaled = multiply(neg(h1_id), w1 % curve_order)

        term1 = pairing(G2, w2)
        term2 = pairing(pk_point, neg_h1_scaled)
        r_prime = term1 * term2

        expected_w1 = hash_h2(v_repr, param_repr, r_prime)
        if expected_w1 == w1:
            return True, "Signature Hess-IBS vérifiée avec succès"
        return False, "Échec du challenge cryptographique H2"
    except Exception as exc:
        return False, f"Erreur de vérification Hess-IBS : {exc}"


# =====================================================================
# 6. DÉLÉGATION DE SIGNATURE POUR LE FOG (Section 3.6)
# =====================================================================

def generate_delegation_key(
    owner_secret_key: tuple,
    delegate_identity: str,
    random_y: int | None = None,
) -> Tuple[tuple, tuple]:
    """
    Calcul des clés de délégation par l'Owner (Section 3.6.1) :
      y in Z_p*, PKd = y * G2
      DKd = Sw + y * H1(IDd) in G1
    Retourne (DKd, PKd).
    """
    if random_y and 0 < random_y < curve_order:
        y = int(random_y)
    else:
        y = secrets.randbelow(curve_order - 1) + 1

    pk_d = multiply(G2, y)
    h1_delegate = hash_h1_to_g1(delegate_identity)
    y_h1 = multiply(h1_delegate, y)
    dk_d = add(ensure_g1(owner_secret_key), y_h1)

    return dk_d, pk_d


def sign_delegated_data(
    v_data: Any,
    delegation_key: tuple,
    param_a: dict | None = None,
) -> dict:
    """
    Génération de signature déléguée par le Fog server DSd (Section 3.6.2) :
      w1 = H2(V', paramA, r), w2 = w1 * DKd + k * P1
      sigma1 = (w1, w2)
    """
    i0, a, c0 = parse_param_a(param_a)
    param_repr = f"{i0},{a},{c0}"
    v_repr = canonical_v_repr(v_data)

    k = secrets.randbelow(curve_order - 1) + 1
    p1 = G1
    k_p1 = multiply(p1, k)
    r = pairing(G2, k_p1)

    w1 = hash_h2(v_repr, param_repr, r)
    w1_dk = multiply(ensure_g1(delegation_key), w1)
    w2 = add(w1_dk, k_p1)

    return {
        "scheme": "Hess-IBS-Delegated",
        "w1": str(w1),
        "w2": g1_to_json(w2),
        "p1": g1_to_json(p1),
    }


def verify_delegated_signature(
    v_data: Any,
    sig_dict: dict,
    owner_identity: str,
    zone_pk: tuple,
    delegate_identity: str,
    delegate_pk: tuple,
    param_a: dict | None = None,
) -> Tuple[bool, str]:
    """
    Vérification de signature déléguée par un consommateur (Section 3.6.3) :
      r' = e(G2, w2) * e(PK, w1 * (-H1(IDw))) * e(PKd, w1 * (-H1(IDd)))
      Tester w1 == H2(V', paramA, r')
    """
    try:
        w1 = int(sig_dict["w1"])
        w2 = json_to_g1(sig_dict["w2"])
        pk_point = ensure_g2(zone_pk)
        delegate_pk_point = ensure_g2(delegate_pk)

        i0, a, c0 = parse_param_a(param_a)
        param_repr = f"{i0},{a},{c0}"
        v_repr = canonical_v_repr(v_data)

        h1_owner = hash_h1_to_g1(owner_identity)
        neg_h1_owner_scaled = multiply(neg(h1_owner), w1 % curve_order)

        h1_delegate = hash_h1_to_g1(delegate_identity)
        neg_h1_delegate_scaled = multiply(neg(h1_delegate), w1 % curve_order)

        term1 = pairing(G2, w2)
        term2 = pairing(pk_point, neg_h1_owner_scaled)
        term3 = pairing(delegate_pk_point, neg_h1_delegate_scaled)
        r_prime = term1 * term2 * term3

        expected_w1 = hash_h2(v_repr, param_repr, r_prime)
        if expected_w1 == w1:
            return True, "Signature déléguée vérifiée avec succès"
        return False, "Échec du challenge cryptographique H2 (délégation)"
    except Exception as exc:
        return False, f"Erreur de vérification déléguée : {exc}"


# =====================================================================
# 7. OPÉRATIONS DYNAMIQUES SUR LES DONNÉES (Section 3.5)
# =====================================================================

def dynamic_insert_block(
    matrix_v: list[list[int]],
    new_block_data: bytes,
    index: int | None = None,
    param_a: dict | None = None,
) -> Tuple[list[list[int]], list[int]]:
    """
    Section 3.5.3 : Insertion d'un nouveau bloc de données D'_i :
      1. Construit x' avec new_block_data
      2. Calcule v'_i = (A * x') mod q in Z_q^l
      3. Insère la colonne v'_i dans la matrice V (l x N) à la position index
    """
    v_new = compute_sis_vector(new_block_data, param_a)
    new_matrix = [list(row) for row in matrix_v]
    num_rows = len(new_matrix)
    for r in range(num_rows):
        if index is None or index >= len(new_matrix[r]):
            new_matrix[r].append(v_new[r])
        else:
            new_matrix[r].insert(index, v_new[r])
    return new_matrix, v_new


def dynamic_modify_block(
    matrix_v: list[list[int]],
    new_block_data: bytes,
    index: int,
    param_a: dict | None = None,
) -> Tuple[list[list[int]], list[int]]:
    """
    Section 3.5.2 : Mise à jour d'un bloc D_i par D'_i :
      1. Construit x'_i avec new_block_data
      2. Calcule v'_i = (A * x'_i) mod q in Z_q^l
      3. Remplace la colonne v_i par v'_i dans V (l x N)
    """
    if not matrix_v or index < 0 or index >= len(matrix_v[0]):
        raise IndexError(f"Index de bloc {index} hors limites")
    v_new = compute_sis_vector(new_block_data, param_a)
    new_matrix = [list(row) for row in matrix_v]
    for r in range(len(new_matrix)):
        new_matrix[r][index] = v_new[r]
    return new_matrix, v_new


def dynamic_delete_block(
    matrix_v: list[list[int]],
    index: int,
) -> list[list[int]]:
    """
    Section 3.5.1 : Suppression d'un bloc D_i :
      1. Supprime la colonne v_i de la matrice V (l x N)
      2. Décrémente les indices des blocs suivants
    """
    if not matrix_v or index < 0 or index >= len(matrix_v[0]):
        raise IndexError(f"Index de bloc {index} hors limites")
    new_matrix = [list(row) for row in matrix_v]
    for r in range(len(new_matrix)):
        new_matrix[r].pop(index)
    return new_matrix
