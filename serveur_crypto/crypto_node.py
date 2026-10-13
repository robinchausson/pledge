import base64
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

import crypto_engine as engine

ZONE_ID = os.getenv("ZONE_ID", "Zone_1")


class CryptoState:
    secret_scalar: int | None = None
    public_key: tuple | None = None


state = CryptoState()


def decode_fragment(fragment_data: str) -> bytes:
    try:
        return base64.b64decode(fragment_data, validate=True)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="fragment_data doit être du Base64 valide",
        ) from error


@asynccontextmanager
async def lifespan(_: FastAPI):
    configured_scalar = os.environ.get("CRYPTO_SECRET_SCALAR")
    scalar_val = int(configured_scalar) if configured_scalar else 123456789
    
    ts, pk_zone = engine.generate_zone_master_key(scalar_val)
    state.secret_scalar = ts
    state.public_key = pk_zone
    
    print(f"[{ZONE_ID}] Nœud d'identification & crypto initialisé.")
    print(f"[{ZONE_ID}] Clé publique de zone (G2) : {engine.g2_to_json(state.public_key)}")
    yield


app = FastAPI(
    title=f"Pledge Crypto & Identification Node - {ZONE_ID}",
    version="2.0.0-fgcs",
    lifespan=lifespan,
)


# --- MODÈLES DE REQUÊTES ---

class IdentifyRequest(BaseModel):
    identity: str = Field(..., description="Identifiant unique de l'utilisateur (ex: email)")


class AccumulateRequest(BaseModel):
    shares: list[list[int]] | None = Field(default=None, description="Parts secrètes G1 à accumuler")
    public_keys: list[list[list[int]]] | None = Field(default=None, description="Clés publiques G2 à accumuler")


class FragmentRequest(BaseModel):
    fragment_data: str = Field(..., description="Fragment vidéo encodé en Base64")
    param_a: dict | None = Field(default=None, alias="paramA")


class SignRequest(BaseModel):
    fragment_data: str | None = Field(default=None, description="Fragment encodé en Base64")
    v_matrix: list[int] | list[list[int]] | None = Field(default=None, alias="V_matrix")
    identity: str | None = Field(default=None, description="Identité du signataire (Ow)")
    secret_key: list[int] | None = Field(default=None, description="Clé secrète Sw [x, y]")
    param_a: dict | None = Field(default=None, alias="paramA")


class VerifyRequest(BaseModel):
    v_matrix: list[int] | list[list[int]] = Field(..., alias="V_matrix")
    param_a: dict | None = Field(default=None, alias="paramA")
    signature: dict = Field(..., description="Signature Hess-IBS {w1, w2, p1}")
    owner_id: str | None = Field(default=None, description="Identité du propriétaire (Ow)")
    zone_pk: list[list[int]] | None = Field(default=None, description="Clé publique de zone accumulée (G2)")
    is_delegated: bool = Field(default=False, description="Indique s'il s'agit d'une signature déléguée Fog")
    delegate_id: str | None = Field(default=None, description="Identité du délégué (DSd)")
    delegate_pk: list[list[int]] | None = Field(default=None, description="Clé publique du délégué (PKd)")


class DelegateRequest(BaseModel):
    owner_secret_key: list[int] | None = Field(default=None, description="Clé secrète du propriétaire Sw")
    owner_id: str | None = Field(default=None, description="Identité du propriétaire si génération locale")
    delegate_id: str = Field(..., description="Identité du nœud Fog délégué (ex: Edge_Node_01)")


# --- ROUTES PRINCIPALES ---

@app.get("/")
def read_root() -> dict[str, str]:
    return {"service": "crypto-node", "zone": ZONE_ID, "status": "ok", "spec": "FGCS-2024"}


@app.get("/health")
def health() -> dict:
    is_ready = state.public_key is not None
    return {
        "status": "ok" if is_ready else "not_ready",
        "initialized": is_ready,
        "zone": ZONE_ID,
        "curve": "bn128",
        "version": "2.0.0-fgcs",
    }


@app.get("/api/v1/crypto/parameters")
def get_crypto_parameters() -> dict:
    if state.public_key is None:
        raise HTTPException(status_code=503, detail="Le serveur crypto n'est pas initialisé")
    return {
        "curve": "bn128",
        "zone_id": ZONE_ID,
        "public_key": engine.g2_to_json(state.public_key),
        "paramA": engine.DEFAULT_PARAM_A,
        "sis": {
            "l": engine.SIS_L,
            "m": engine.SIS_M,
            "q": engine.SIS_Q,
            "hash": "SHA-256",
            "generator": "L'Ecuyer-1994",
        },
        "signature": {
            "scheme": "Hess-IBS",
            "group_curve": "alt_bn128",
            "version": "2.0-fgcs",
        },
    }


@app.post("/api/v1/crypto/identify")
def identify_owner(request: IdentifyRequest) -> dict:
    """
    Serveur d'Identification IS_s (Section 3.4.2) :
    Calcule et délivre la part secrète S_w^s = ts * H1(ID_w) pour l'identité de l'utilisateur.
    """
    if state.secret_scalar is None or state.public_key is None:
        raise HTTPException(status_code=503, detail="Serveur d'identification non initialisé")
    
    secret_share = engine.compute_secret_share(state.secret_scalar, request.identity)
    return {
        "identity": request.identity,
        "zone_id": ZONE_ID,
        "secret_share": engine.g1_to_json(secret_share),
        "zone_pk": engine.g2_to_json(state.public_key),
    }


@app.post("/api/v1/crypto/accumulate")
def accumulate_keys_endpoint(request: AccumulateRequest) -> dict:
    """
    Mobilité Multi-Zone (Section 3.4.2 Step 5) :
    Accumule les parts secrètes Sw = sum(S_w^s) et les clés publiques PK = sum(PK_s).
    """
    result = {}
    if request.shares:
        g1_shares = [engine.json_to_g1(s) for s in request.shares]
        sw_accum = engine.accumulate_secret_keys(g1_shares)
        result["accumulated_secret_key"] = engine.g1_to_json(sw_accum)
    if request.public_keys:
        g2_pks = [engine.json_to_g2(pk) for pk in request.public_keys]
        pk_accum = engine.accumulate_public_keys(g2_pks)
        result["accumulated_public_key"] = engine.g2_to_json(pk_accum)
    return result


@app.post("/api/v1/crypto/integrity")
def calculate_integrity(request: FragmentRequest) -> dict:
    """
    Calcul de la preuve d'intégrité SIS v_i = (A * x_i) mod q (Section 3.4.3.2).
    """
    fragment = decode_fragment(request.fragment_data)
    param_a = request.param_a or engine.DEFAULT_PARAM_A
    v_vector = engine.compute_sis_vector(fragment, param_a)
    return {
        "paramA": param_a,
        "V_matrix": v_vector,
    }


@app.post("/api/v1/crypto/signature")
def sign_fragment(request: SignRequest) -> dict:
    """
    Génération d'une signature Hess-IBS basée sur l'identité (Section 3.4.3.3).
    """
    param_a = request.param_a or engine.DEFAULT_PARAM_A
    
    if request.fragment_data:
        fragment = decode_fragment(request.fragment_data)
        v_vector = engine.compute_sis_vector(fragment, param_a)
    elif request.v_matrix is not None:
        v_vector = request.v_matrix
    else:
        raise HTTPException(status_code=400, detail="Fournir fragment_data ou V_matrix")

    # Clé secrète Sw
    if request.secret_key:
        secret_key = engine.json_to_g1(request.secret_key)
    elif request.identity and state.secret_scalar:
        # Émission et signature à la volée pour tests/clients simplifiés
        secret_key = engine.compute_secret_share(state.secret_scalar, request.identity)
    elif state.secret_scalar:
        secret_key = engine.compute_secret_share(state.secret_scalar, f"default@{ZONE_ID.lower()}.pledge")
    else:
        raise HTTPException(status_code=503, detail="Clé secrète non disponible")

    signature = engine.sign_data(v_vector, secret_key, param_a)
    return {
        "paramA": param_a,
        "V_matrix": v_vector,
        "signature": signature,
    }


@app.post("/api/v1/crypto/verify")
def verify_signature_endpoint(request: VerifyRequest) -> dict:
    """
    Vérification publique Hess-IBS (Section 3.4.4 & 3.6.3).
    Peut être effectuée par n'importe quel spectateur sans tiers de confiance.
    """
    param_a = request.param_a or engine.DEFAULT_PARAM_A
    zone_pk = request.zone_pk if request.zone_pk else state.public_key
    if not zone_pk:
        raise HTTPException(status_code=400, detail="zone_pk requis pour la vérification")

    owner_id = request.owner_id or f"default@{ZONE_ID.lower()}.pledge"

    if request.is_delegated:
        if not request.delegate_id or not request.delegate_pk:
            raise HTTPException(status_code=400, detail="delegate_id et delegate_pk requis pour la signature déléguée")
        is_valid, detail = engine.verify_delegated_signature(
            v_data=request.v_matrix,
            sig_dict=request.signature,
            owner_identity=owner_id,
            zone_pk=zone_pk,
            delegate_identity=request.delegate_id,
            delegate_pk=request.delegate_pk,
            param_a=param_a,
        )
    else:
        is_valid, detail = engine.verify_signature(
            v_data=request.v_matrix,
            sig_dict=request.signature,
            owner_identity=owner_id,
            zone_pk=zone_pk,
            param_a=param_a,
        )

    return {"valid": is_valid, "detail": detail}


@app.post("/api/v1/crypto/delegate")
def delegate_signing_key(request: DelegateRequest) -> dict:
    """
    Génération d'une clé de signature déléguée pour le Fog (Section 3.6.1) :
      DKd = Sw + y * H1(IDd)
    """
    if request.owner_secret_key:
        sw = engine.json_to_g1(request.owner_secret_key)
    elif request.owner_id and state.secret_scalar:
        sw = engine.compute_secret_share(state.secret_scalar, request.owner_id)
    else:
        raise HTTPException(status_code=400, detail="owner_secret_key ou owner_id requis")

    dk_d, pk_d = engine.generate_delegation_key(sw, request.delegate_id)
    return {
        "delegate_id": request.delegate_id,
        "delegation_key": engine.g1_to_json(dk_d),
        "delegate_pk": engine.g2_to_json(pk_d),
    }
