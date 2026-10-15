"""
Nœud Cryptographique et Serveur d'Identification (IS_s) conforme à l'article scientifique :
'A verifiable data integrity scheme for distributed data sharing in fog computing architecture'
(Sellami, Imine, Gallais - Future Generation Computer Systems 150, 2024, pp. 64-77).

Fonctionnalités prises en charge :
- Phase 1 : Preuve d'intégrité SIS matricielle L'Ecuyer et Signature Hess-IBS
- Phase 2 : Serveur d'Identification IS_s et Mobilité multi-zone (S_w^s = t_s · H1(ID_w))
- Phase 3 : Délégation de signature Fog (DK_d, PK_d) et vérification à 3 accouplements
"""

from __future__ import annotations

import base64
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, List, Optional, Union

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

# Augmentation de la limite de récursion pour les accouplements BN128
sys.setrecursionlimit(10000)

from crypto_engine import (
    DEFAULT_PARAM_A,
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
    ensure_g1,
    ensure_g2,
    g1_to_json,
    g2_to_json,
    generate_delegation_key,
    generate_zone_master_key,
    json_to_g1,
    json_to_g2,
    parse_param_a,
    sign_data,
    sign_delegated_data,
    verify_delegated_signature,
    verify_signature,
)


class CryptoState:
    master_secret: int | None = None
    zone_pk: tuple | None = None
    zone_id: str = "Zone_1"


state = CryptoState()


def decode_fragment(fragment_data: str) -> bytes:
    try:
        return base64.b64decode(fragment_data, validate=True)
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="fragment_data doit être une chaîne Base64 valide",
        ) from error


def normalize_param_a(param: Any) -> dict:
    if isinstance(param, dict):
        return {
            "I0": int(param.get("I0", DEFAULT_PARAM_A["I0"])),
            "a": int(param.get("a", DEFAULT_PARAM_A["a"])),
            "C0": int(param.get("C0", DEFAULT_PARAM_A["C0"])),
        }
    return dict(DEFAULT_PARAM_A)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configured_scalar = os.environ.get("CRYPTO_SECRET_SCALAR")
    state.zone_id = os.environ.get("ZONE_ID", "Zone_1")

    scalar = int(configured_scalar) if configured_scalar else 123456789
    ts, pk_zone = generate_zone_master_key(scalar)
    state.master_secret = ts
    state.zone_pk = pk_zone

    print(
        f"[crypto-node] Initialisé pour {state.zone_id}. "
        f"Clé publique de zone PK_s : {g2_to_json(state.zone_pk)}",
        flush=True,
    )
    yield


app = FastAPI(
    title="Pledge Cryptographic Identification & Delegation Server",
    version="2.0.0",
    lifespan=lifespan,
)


class IdentifyRequest(BaseModel):
    identity: str = Field(..., description="Identifiant unique de l'utilisateur (ex: email)")


class AccumulateRequest(BaseModel):
    shares: Optional[List[List[int]]] = Field(default=None, description="Parts secrètes G1 à accumuler")
    public_keys: Optional[List[List[List[int]]]] = Field(default=None, description="Clés publiques G2 à accumuler")


class FragmentRequest(BaseModel):
    fragment_data: str = Field(..., description="Fragment vidéo encodé en Base64")
    param_a: Union[dict, int, None] = Field(default=None, alias="paramA")


class SignRequest(BaseModel):
    fragment_data: Optional[str] = Field(None, description="Fragment vidéo en Base64")
    v_matrix: Optional[List[int]] = Field(None, alias="V_matrix", description="Vecteur SIS")
    secret_share: Optional[List[int]] = Field(None, description="Part secrète Sw de l'owner")
    param_a: Union[dict, int, None] = Field(None, alias="paramA")
    owner_identity: Optional[str] = Field(None, description="Identité de l'émetteur pour auto-génération de S_w")
class DelegationIssueRequest(BaseModel):
    delegate_identity: Optional[str] = Field(None, description="Identifiant du nœud Fog délégué (DS_d)")
    delegate_id: Optional[str] = Field(None, description="Alias pour delegate_identity")
    owner_secret_key: Optional[List[int]] = Field(None, description="Clé secrète S_w de l'owner [x, y]")
    owner_identity: Optional[str] = Field(None, description="Identité de l'owner si S_w doit être généré par le serveur")


class DelegatedSignRequest(BaseModel):
    delegation_key: List[int] = Field(..., description="Clé de délégation DK_d [x, y]")
    fragment_data: Optional[str] = Field(None, description="Fragment transcodé en Base64")
    v_matrix: Optional[List[int]] = Field(None, alias="V_matrix")
    param_a: Union[dict, int, None] = Field(None, alias="paramA")


class VerifyRequest(BaseModel):
    v_matrix: List[int] = Field(..., alias="V_matrix", description="Vecteur SIS v_i")
    signature: dict = Field(..., description="Signature Hess-IBS {w1, w2, p1}")
    owner_identity: Optional[str] = Field("streamer@pledge.local", description="Identité Ow")
    zone_pk: Optional[List[List[int]]] = Field(None, description="Clé publique de zone PK_s")
    param_a: Union[dict, int, None] = Field(None, alias="paramA")


class VerifyDelegatedRequest(BaseModel):
    v_matrix: List[int] = Field(..., alias="V_matrix", description="Vecteur SIS v'_i transcodé")
    signature: dict = Field(..., description="Signature déléguée {w1, w2, p1}")
    owner_identity: str = Field(..., description="Identité du créateur Ow")
    delegate_identity: str = Field(..., description="Identité du serveur Fog DS_d")
    delegate_pk: List[List[int]] = Field(..., description="Clé publique de délégation PK_d")
    zone_pk: Optional[List[List[int]]] = Field(None, description="Clé publique de zone PK_s")
    param_a: Union[dict, int, None] = Field(None, alias="paramA")


# =====================================================================
# ENDPOINTS STATUT & CONFIGURATION
# =====================================================================

@app.get("/")
def read_root() -> dict[str, str]:
    return {
        "service": "crypto-node",
        "zone_id": state.zone_id,
        "conformance": "FGCS 2024 Sellami et al.",
        "status": "ok",
    }


@app.get("/health")
def health() -> dict:
    is_ready = state.zone_pk is not None and state.master_secret is not None
    return {
        "status": "ok" if is_ready else "not_ready",
        "zone_id": state.zone_id,
        "initialized": is_ready,
        "curve": "bn128",
        "phase": "Phase 3 (Delegation & Transcoding)",
    }


@app.get("/api/v1/crypto/parameters")
def get_crypto_parameters() -> dict:
    if state.zone_pk is None:
        raise HTTPException(status_code=503, detail="Le nœud crypto n'est pas initialisé")
    return {
        "curve": "bn128",
        "zone_id": state.zone_id,
        "public_key": g2_to_json(state.zone_pk),
        "paramA": DEFAULT_PARAM_A,
        "sis": {"l": SIS_L, "m": SIS_M, "q": SIS_Q, "hash": "SHA-256", "generator": "L'Ecuyer-1994"},
        "signature": {
            "scheme": "Hess-IBS",
            "group": "G1",
            "pairing": "e: G2 x G1 -> GT",
            "delegation_supported": True,
        },
    }


# =====================================================================
# PHASE 2 : IDENTIFICATION DE L'OWNER (IS_s)
# =====================================================================

@app.post("/api/v1/auth/identify")
@app.post("/api/v1/crypto/identify")
def identify_entity(request: IdentifyRequest) -> dict:
    """
    Serveur d'Identification IS_s :
      Calcule S_w^s = t_s · H1(ID_w) in G1.
    """
    if state.master_secret is None or state.zone_pk is None:
        raise HTTPException(status_code=503, detail="Zone non initialisée")

    secret_share = compute_secret_share(state.master_secret, request.identity)
    return {
        "identity": request.identity,
        "zone_id": state.zone_id,
        "secret_share": g1_to_json(secret_share),
        "zone_pk": g2_to_json(state.zone_pk),
    }


@app.post("/api/v1/crypto/mobility/accumulate")
@app.post("/api/v1/crypto/accumulate")
def accumulate_keys_endpoint(request: AccumulateRequest) -> dict:
    """
    Mobilité Multi-Zone (Section 3.4.2 Step 5) :
    Accumule homomorphiquement les parts secrètes S_w = sum(S_w^s)
    et les clés publiques PK = sum(PK_s).
    """
    result = {}
    if request.shares:
        g1_shares = [json_to_g1(s) for s in request.shares]
        sw_accum = accumulate_secret_keys(g1_shares)
        result["accumulated_secret_key"] = g1_to_json(sw_accum)
    if request.public_keys:
        g2_pks = [json_to_g2(pk) for pk in request.public_keys]
        pk_accum = accumulate_public_keys(g2_pks)
        result["accumulated_public_key"] = g2_to_json(pk_accum)
    return result


# =====================================================================
# PHASE 1 : CALCUL SIS MATRICIEL & SIGNATURE INITIALE
# =====================================================================

@app.post("/api/v1/crypto/integrity")
def calculate_integrity(request: FragmentRequest) -> dict:
    """
    Calcule le vecteur SIS v_i = (A * x_i) mod q avec générateur L'Ecuyer.
    """
    fragment = decode_fragment(request.fragment_data)
    param_a = normalize_param_a(request.param_a)
    vector_v = compute_sis_vector(fragment, param_a)
    return {
        "paramA": param_a,
        "V_matrix": vector_v,
    }


@app.post("/api/v1/crypto/sign")
@app.post("/api/v1/crypto/signature")
def create_signature(request: SignRequest) -> dict:
    """
    Génère une signature Hess-IBS σ = (w1, w2) avec la part secrète S_w.
    """
    param_a = normalize_param_a(request.param_a)

    if request.v_matrix is not None:
        vector_v = request.v_matrix
    elif request.fragment_data:
        fragment = decode_fragment(request.fragment_data)
        vector_v = compute_sis_vector(fragment, param_a)
    else:
        raise HTTPException(status_code=400, detail="fragment_data ou V_matrix requis")

    if request.secret_share:
        secret_key = json_to_g1(request.secret_share)
    elif request.owner_identity and state.master_secret:
        secret_key = compute_secret_share(state.master_secret, request.owner_identity)
    elif state.master_secret:
        # Repli sur l'identité par défaut
        secret_key = compute_secret_share(state.master_secret, "streamer@pledge.local")
    else:
        raise HTTPException(status_code=503, detail="Clé secrète non disponible")

    sig = sign_data(vector_v, secret_key, param_a)
    return {
        "paramA": param_a,
        "V_matrix": vector_v,
        "signature": sig,
    }


@app.post("/api/v1/crypto/verify")
def verify_signature_endpoint(request: VerifyRequest) -> dict:
    """
    Vérification Hess-IBS standard (Section 3.4.4).
    """
    param_a = normalize_param_a(request.param_a)
    zone_pk = json_to_g2(request.zone_pk) if request.zone_pk else state.zone_pk
    if zone_pk is None:
        raise HTTPException(status_code=400, detail="Clé publique de zone requise")

    is_valid, reason = verify_signature(
        v_data=request.v_matrix,
        sig_dict=request.signature,
        owner_identity=request.owner_identity or "streamer@pledge.local",
        zone_pk=zone_pk,
        param_a=param_a,
    )
    return {"valid": is_valid, "detail": reason}


# =====================================================================
# PHASE 3 : DÉLÉGATION DE SIGNATURE FOG (Section 3.6)
# =====================================================================

@app.post("/api/v1/crypto/delegation/issue")
@app.post("/api/v1/crypto/delegate")
def issue_delegation_endpoint(request: DelegationIssueRequest) -> dict:
    """
    Section 3.6.1 : Émission de la clé de délégation pour un serveur Fog DS_d.
      Owner calcule :
        y in Z_p*, PK_d = y · P
        DK_d = S_w + y · H1(ID_d)
    """
    del_id = request.delegate_identity or request.delegate_id
    if not del_id:
        raise HTTPException(status_code=400, detail="delegate_identity ou delegate_id requis")

    if request.owner_secret_key:
        owner_secret = json_to_g1(request.owner_secret_key)
    elif request.owner_identity and state.master_secret:
        owner_secret = compute_secret_share(state.master_secret, request.owner_identity)
    elif state.master_secret:
        owner_secret = compute_secret_share(state.master_secret, "streamer@pledge.local")
    else:
        raise HTTPException(status_code=400, detail="owner_secret_key ou master_secret requis")

    dk_d, pk_d = generate_delegation_key(owner_secret, del_id)
    return {
        "delegate_identity": del_id,
        "delegate_id": del_id,
        "delegation_key": g1_to_json(dk_d),
        "delegate_pk": g2_to_json(pk_d),
        "scheme": "Hess-IBS-Delegation",
        "zone_pk": g2_to_json(state.zone_pk) if state.zone_pk else None,
    }


@app.post("/api/v1/crypto/signature/delegate")
def sign_delegated_endpoint(request: DelegatedSignRequest) -> dict:
    """
    Section 3.6.2 : Signature déléguée par le serveur Fog DS_d sur données transcodées.
      w1 = H2(V', paramA, r)
      w2 = w1 · DK_d + k · P1
    """
    param_a = normalize_param_a(request.param_a)

    if request.v_matrix is not None:
        vector_v = request.v_matrix
    elif request.fragment_data:
        fragment = decode_fragment(request.fragment_data)
        vector_v = compute_sis_vector(fragment, param_a)
    else:
        raise HTTPException(status_code=400, detail="fragment_data ou V_matrix requis")

    dk_point = json_to_g1(request.delegation_key)
    sig = sign_delegated_data(vector_v, dk_point, param_a)
    return {
        "paramA": param_a,
        "V_matrix": vector_v,
        "signature": sig,
    }


@app.post("/api/v1/crypto/verify/delegate")
def verify_delegated_endpoint(request: VerifyDelegatedRequest) -> dict:
    """
    Section 3.6.3 : Vérification publique de signature déléguée avec 3 accouplements.
      r' = e(w2, P) · e(H1(ID_w), -PK')^w1 / e(PK_d, H1(ID_d))^w1
      Vérifier w1 == H2(V', paramA, r')
    """
    param_a = normalize_param_a(request.param_a)
    zone_pk = json_to_g2(request.zone_pk) if request.zone_pk else state.zone_pk
    if zone_pk is None:
        raise HTTPException(status_code=400, detail="Clé publique de zone requise")

    delegate_pk = json_to_g2(request.delegate_pk)
    is_valid, reason = verify_delegated_signature(
        v_data=request.v_matrix,
        sig_dict=request.signature,
        owner_identity=request.owner_identity,
        zone_pk=zone_pk,
        delegate_identity=request.delegate_identity,
        delegate_pk=delegate_pk,
        param_a=param_a,
    )
    return {"valid": is_valid, "detail": reason}


class PackageBlockRequest(BaseModel):
    payload_b64: str = Field(..., description="Données binaire en Base64")
    block_index: int = Field(0, description="Index du bloc")
    signer_id: str = Field(..., description="ID de l'émetteur")
    param_a: Any = Field(default=None, alias="paramA")


@app.post("/api/v1/crypto/package-block")
def package_block_endpoint(request: PackageBlockRequest) -> dict:
    """
    Empaquette un bloc vidéo avec le calcul SIS conforme L'Ecuyer
    et la signature Hess-IBS basée sur l'identité de l'émetteur.
    """
    param_a = normalize_param_a(request.param_a)
    fragment = decode_fragment(request.payload_b64)
    v_vector = compute_sis_vector(fragment, param_a)
    secret_key = compute_secret_share(state.master_secret, request.signer_id)
    sig = sign_data(v_vector, secret_key, param_a)
    return {
        "block_index": request.block_index,
        "payload_b64": request.payload_b64,
        "paramA": param_a,
        "V_column": v_vector,
        "signature": sig,
        "signer_id": request.signer_id,
        "zone_pk": g2_to_json(state.zone_pk) if state.zone_pk else [],
        "is_delegated": False,
    }


# =====================================================================
# PHASE 6 : OPÉRATIONS DYNAMIQUES SUR LES DONNÉES (Section 3.5)
# =====================================================================

class DynamicInsertRequest(BaseModel):
    v_matrix: List[List[int]] = Field(..., description="Matrice V existante (l x N)")
    block_index: int = Field(..., description="Index k d'insertion")
    block_data_b64: str = Field(..., description="Données du nouveau bloc en Base64")
    param_a: Union[dict, int, None] = Field(None, alias="paramA")
    secret_share: Optional[List[int]] = Field(None, description="Part secrète Sw de l'owner [x, y]")
    owner_identity: Optional[str] = Field(None, description="Identité de l'owner pour signature")


class DynamicModifyRequest(BaseModel):
    v_matrix: List[List[int]] = Field(..., description="Matrice V existante (l x N)")
    block_index: int = Field(..., description="Index k de modification")
    block_data_b64: str = Field(..., description="Nouvelles données du bloc en Base64")
    param_a: Union[dict, int, None] = Field(None, alias="paramA")
    secret_share: Optional[List[int]] = Field(None, description="Part secrète Sw de l'owner [x, y]")
    owner_identity: Optional[str] = Field(None, description="Identité de l'owner pour signature")


class DynamicDeleteRequest(BaseModel):
    v_matrix: List[List[int]] = Field(..., description="Matrice V existante (l x N)")
    block_index: int = Field(..., description="Index k de suppression")
    param_a: Union[dict, int, None] = Field(None, alias="paramA")
    secret_share: Optional[List[int]] = Field(None, description="Part secrète Sw de l'owner [x, y]")
    owner_identity: Optional[str] = Field(None, description="Identité de l'owner pour signature")


def _try_sign_matrix(v_matrix: list, secret_share: Optional[list], owner_identity: Optional[str], param_a: dict) -> Optional[dict]:
    """Calcule la signature Hess-IBS sur la matrice V mise à jour (Section 3.5)."""
    if secret_share:
        secret_key = json_to_g1(secret_share)
        return sign_data(v_matrix, secret_key, param_a)
    elif owner_identity and state.master_secret:
        secret_key = compute_secret_share(state.master_secret, owner_identity)
        return sign_data(v_matrix, secret_key, param_a)
    return None


@app.post("/api/v1/crypto/dynamic/insert")
def dynamic_insert_endpoint(request: DynamicInsertRequest) -> dict:
    """
    Section 3.5.3 : Insertion de bloc à la position k et re-signature.
    """
    param_a = normalize_param_a(request.param_a)
    block_bytes = decode_fragment(request.block_data_b64)
    if not request.v_matrix or len(request.v_matrix) != SIS_L:
        raise HTTPException(status_code=400, detail=f"La matrice V doit comporter {SIS_L} lignes")
    num_cols = len(request.v_matrix[0])
    if request.block_index < 0 or request.block_index > num_cols:
        raise HTTPException(status_code=400, detail=f"Index d'insertion {request.block_index} hors limites")

    updated_v, new_col = dynamic_insert_block(
        matrix_v=request.v_matrix,
        new_block_data=block_bytes,
        index=request.block_index,
        param_a=param_a,
    )
    sig = _try_sign_matrix(updated_v, request.secret_share, request.owner_identity, param_a)
    return {
        "status": "success",
        "operation": "insert",
        "block_index": request.block_index,
        "new_column": new_col,
        "v_matrix": updated_v,
        "signature": sig,
        "total_blocks": len(updated_v[0]) if updated_v else 0,
    }


@app.post("/api/v1/crypto/dynamic/modify")
def dynamic_modify_endpoint(request: DynamicModifyRequest) -> dict:
    """
    Section 3.5.2 : Modification du bloc k et re-signature.
    """
    param_a = normalize_param_a(request.param_a)
    block_bytes = decode_fragment(request.block_data_b64)
    if not request.v_matrix or len(request.v_matrix) != SIS_L:
        raise HTTPException(status_code=400, detail=f"La matrice V doit comporter {SIS_L} lignes")
    num_cols = len(request.v_matrix[0])
    if request.block_index < 0 or request.block_index >= num_cols:
        raise HTTPException(status_code=400, detail=f"Index de modification {request.block_index} hors limites")

    updated_v, new_col = dynamic_modify_block(
        matrix_v=request.v_matrix,
        new_block_data=block_bytes,
        index=request.block_index,
        param_a=param_a,
    )
    sig = _try_sign_matrix(updated_v, request.secret_share, request.owner_identity, param_a)
    return {
        "status": "success",
        "operation": "modify",
        "block_index": request.block_index,
        "new_column": new_col,
        "v_matrix": updated_v,
        "signature": sig,
        "total_blocks": len(updated_v[0]) if updated_v else 0,
    }


@app.post("/api/v1/crypto/dynamic/delete")
def dynamic_delete_endpoint(request: DynamicDeleteRequest) -> dict:
    """
    Section 3.5.1 : Suppression du bloc k et re-signature.
    """
    param_a = normalize_param_a(request.param_a)
    if not request.v_matrix or len(request.v_matrix) != SIS_L:
        raise HTTPException(status_code=400, detail=f"La matrice V doit comporter {SIS_L} lignes")
    num_cols = len(request.v_matrix[0])
    if request.block_index < 0 or request.block_index >= num_cols:
        raise HTTPException(status_code=400, detail=f"Index de suppression {request.block_index} hors limites")

    updated_v = dynamic_delete_block(
        matrix_v=request.v_matrix,
        index=request.block_index,
    )
    sig = _try_sign_matrix(updated_v, request.secret_share, request.owner_identity, param_a)
    return {
        "status": "success",
        "operation": "delete",
        "block_index": request.block_index,
        "v_matrix": updated_v,
        "signature": sig,
        "total_blocks": len(updated_v[0]) if updated_v else 0,
    }

